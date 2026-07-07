#!/usr/bin/env python3
"""
W3.4: MobileNetV3-Small Func Model End-to-End Verification.

Runs MobileNetV3-Small through the CaduceusCore Func Model path:
  im2col -> INT4-per-block GEMM (GoldenMXU) -> compare raw conv outputs.

Compares per-layer raw Conv2D outputs against PyTorch float32 reference.
Target: >=15 conv layers with cos_sim >= 0.99.
"""

import struct
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import torch
import torchvision
import pytest

_sim_dir = Path(__file__).resolve().parent.parent
if str(_sim_dir) not in sys.path:
    sys.path.insert(0, str(_sim_dir))

from golden_executor import GoldenMXU
from quantize import quantize_int4_per_block as _quantize_w_int4_block

GROUP_SIZE = 128


# ======================================================================
# im2col
# ======================================================================

def im2col_conv2d(x, weight_shape, stride, padding, groups):
    """Flatten (N,C,H,W) activation into (M, K) for GEMM via im2col."""
    N_val, C, H, W = x.shape
    _, _, kH, kW = weight_shape
    pH = padding[0] if isinstance(padding, (tuple, list)) else padding
    pW = padding[1] if isinstance(padding, (tuple, list)) else padding
    H_out = (H + 2 * pH - kH) // stride + 1
    W_out = (W + 2 * pW - kW) // stride + 1

    xp = np.pad(x, ((0, 0), (0, 0), (pH, pH), (pW, pW)), mode='constant')
    C_per_group = C // groups
    K_per_group = C_per_group * kH * kW
    M_per_group = N_val * H_out * W_out
    total_K = K_per_group * groups
    cols = np.zeros((M_per_group, total_K), dtype=np.float32)

    for g in range(groups):
        c0 = g * C_per_group
        c1 = c0 + C_per_group
        col0 = g * K_per_group
        col1 = col0 + K_per_group
        for i in range(H_out):
            for j in range(W_out):
                ii = i * stride
                jj = j * stride
                patch = xp[0, c0:c1, ii:ii + kH, jj:jj + kW]
                row = i * W_out + j
                cols[row, col0:col1] = patch.reshape(-1)
    return cols


def im2col_depthwise(x, kH, kW, stride, padding):
    """Depthwise im2col: returns (H_out*W_out*C_in, kH*kW)."""
    N_val, C, H, W = x.shape
    pH = padding[0] if isinstance(padding, (tuple, list)) else padding
    pW = padding[1] if isinstance(padding, (tuple, list)) else padding
    H_out = (H + 2 * pH - kH) // stride + 1
    W_out = (W + 2 * pW - kW) // stride + 1

    xp = np.pad(x, ((0, 0), (0, 0), (pH, pH), (pW, pW)), mode='constant')
    M_dim = N_val * H_out * W_out * C
    K_dim = kH * kW
    cols = np.zeros((M_dim, K_dim), dtype=np.float32)

    for c in range(C):
        for i in range(H_out):
            for j in range(W_out):
                ii = i * stride
                jj = j * stride
                patch = xp[0, c, ii:ii + kH, jj:jj + kW]
                row = c * H_out * W_out + i * W_out + j
                cols[row, :] = patch.reshape(-1)
    return cols


# ======================================================================
# INT8 activation quantization
# ======================================================================

def quantize_act_int8(x):
    """Per-tensor symmetric INT8 quantization. Returns (int8, scale)."""
    x_f32 = np.asarray(x, dtype=np.float32)
    max_abs = float(np.max(np.abs(x_f32)))
    if max_abs < 1e-12:
        return x_f32.astype(np.int8), 1.0
    scale = max_abs / 127.0
    q = np.clip(np.round(x_f32 / scale), -128, 127).astype(np.int8)
    return q, scale


# ======================================================================
# Func Model conv forward
# ======================================================================

def conv2d_func_model(mxu, x, weight, bias, stride, padding, groups):
    """Run one Conv2D through Func Model (im2col -> INT4-per-block GEMM).

    Returns (1, C_out, H_out, W_out) float32 (raw conv output).
    """
    C_out, _, kH, kW = weight.shape
    N_val, C_in, H, W = x.shape
    pH = padding[0] if isinstance(padding, (tuple, list)) else padding
    pW = padding[1] if isinstance(padding, (tuple, list)) else padding
    H_out = (H + 2 * pH - kH) // stride + 1
    W_out = (W + 2 * pW - kW) // stride + 1

    is_dw = (groups > 1 and groups == C_in)

    if is_dw:
        # Depthwise: run each of C_in channels as a separate single-channel conv
        # im2col produces (C_in, H_out*W_out, kH*kW) -> process as group conv with C_per_group=1
        fm_parts = []
        for c in range(C_in):
            x_c = x[:, c:c+1, :, :]  # (1, 1, H, W)
            cols_c = im2col_conv2d(
                x_c, (1, 1, kH, kW),
                stride=stride, padding=padding, groups=1)
            w_c = weight[c:c+1, :, :, :].reshape(1, kH * kW).T.astype(np.float32)

            a_i8, a_s = quantize_act_int8(cols_c)
            w_p, w_sc, _ = _quantize_w_int4_block(w_c, GROUP_SIZE)
            M_c = cols_c.shape[0]
            part = mxu.matmul_int4_per_block(
                a_i8, w_p, w_sc, M_c, kH * kW, 1, group_size=GROUP_SIZE)
            if a_s != 1.0:
                part = part * a_s
            # part shape: (H_out*W_out, 1) -> (1, H_out, W_out)
            part_3d = part.reshape(1, H_out, W_out)
            fm_parts.append(part_3d)
        fm_out_4d = np.stack(fm_parts, axis=1)  # (1, C_out, H_out, W_out)
        if bias is not None:
            fm_out_4d = fm_out_4d + bias.astype(np.float32).reshape(1, -1, 1, 1)
        return fm_out_4d.astype(np.float32)
    else:
        cols = im2col_conv2d(x, weight.shape, stride, padding, groups)
        C_per_group = C_in // groups
        K_per_group = C_per_group * kH * kW
        M_per_group = N_val * H_out * W_out

        if groups > 1:
            # Grouped: run each group separately, concatenate
            C_out_per_group = C_out // groups
            fm_parts = []
            for g in range(groups):
                c0 = g * K_per_group
                c1 = c0 + K_per_group
                act_g = cols[:, c0:c1]
                w_g = weight[g * C_out_per_group:(g + 1) * C_out_per_group].reshape(
                    C_out_per_group, K_per_group).T.astype(np.float32)

                a_i8, a_s = quantize_act_int8(act_g)
                w_p, w_sc, _ = _quantize_w_int4_block(w_g, GROUP_SIZE)
                part = mxu.matmul_int4_per_block(
                    a_i8, w_p, w_sc, M_per_group, K_per_group, C_out_per_group,
                    group_size=GROUP_SIZE)
                if a_s != 1.0:
                    part = part * a_s
                fm_parts.append(part)
            fm_out = np.concatenate(fm_parts, axis=1)
            fm_out_4d = fm_out.reshape(N_val, H_out, W_out, C_out).transpose(0, 3, 1, 2)
            if bias is not None:
                fm_out_4d = fm_out_4d + bias.astype(np.float32).reshape(1, -1, 1, 1)
            return fm_out_4d.astype(np.float32)

        # groups == 1
        W_2d = weight.reshape(C_out, K_per_group).T.astype(np.float32)
        M_dim, K_dim, N_dim = M_per_group, K_per_group, C_out

    a_i8, a_s = quantize_act_int8(cols)
    w_p, w_sc, _ = _quantize_w_int4_block(W_2d, GROUP_SIZE)
    fm_out = mxu.matmul_int4_per_block(
        a_i8, w_p, w_sc, M_dim, K_dim, N_dim, group_size=GROUP_SIZE)
    if a_s != 1.0:
        fm_out = fm_out * a_s

    # Reshape: FM output is (M, N) -> (1, C_out, H_out, W_out)
    fm_out_4d = fm_out.reshape(N_val, H_out, W_out, N_dim).transpose(0, 3, 1, 2)
    if bias is not None:
        fm_out_4d = fm_out_4d + bias.astype(np.float32).reshape(1, -1, 1, 1)
    return fm_out_4d.astype(np.float32)


# ======================================================================
# Cosine similarity
# ======================================================================

def cos_sim(a, b):
    a_f = a.astype(np.float64).flatten()
    b_f = b.astype(np.float64).flatten()
    dot = float(np.dot(a_f, b_f))
    na = float(np.linalg.norm(a_f))
    nb = float(np.linalg.norm(b_f))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


# ======================================================================
# Evidence generation (standalone)
# ======================================================================

def generate_evidence():
    evidence_dir = Path(__file__).resolve().parent.parent.parent / "build" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "w3-4-mobilenetv3-fm.txt"

    lines = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines.append("=" * 70)
    lines.append("W3.4 MobileNetV3-Small Func Model E2E Verification")
    lines.append(f"Timestamp: {ts}")
    lines.append("=" * 70)
    lines.append("")

    # Load model
    lines.append("Loading model...")
    model = torchvision.models.mobilenet_v3_small(weights="DEFAULT")
    model.eval()
    lines.append(f"  TorchVision: {torchvision.__version__}")
    lines.append(f"  PyTorch: {torch.__version__}")

    # ---------- discover Conv2d modules ----------
    conv_list = []
    for name, child in model.named_modules():
        if isinstance(child, torch.nn.Conv2d):
            conv_list.append((name, child))

    lines.append(f"Conv2D layers: {len(conv_list)}")

    # ---------- capture inputs AND outputs via hooks ----------
    conv_inputs: Dict[str, np.ndarray] = {}
    conv_outputs: Dict[str, np.ndarray] = {}
    hooks = []

    def _get_module(root, dotted):
        obj = root
        for part in dotted.split('.'):
            if part.isdigit():
                obj = obj[int(part)]
            else:
                obj = getattr(obj, part)
        return obj

    for name, _ in conv_list:
        mod = _get_module(model, name)

        def make_in_hook(n):
            def hook(module, inp):
                conv_inputs[n] = inp[0].detach().numpy()
            return hook

        def make_out_hook(n):
            def hook(module, inp, outp):
                conv_outputs[n] = outp.detach().numpy()
            return hook

        hooks.append(mod.register_forward_pre_hook(make_in_hook(name)))
        hooks.append(mod.register_forward_hook(make_out_hook(name)))

    # ---------- forward pass ----------
    rng = np.random.RandomState(42)
    x_np = rng.randn(1, 3, 224, 224).astype(np.float32)
    with torch.no_grad():
        x_t = torch.from_numpy(x_np).float()
        model(x_t)

    for h in hooks:
        h.remove()

    lines.append(f"Hooks captured outputs: {len(conv_outputs)} layers")
    lines.append(f"Hooks captured inputs:  {len(conv_inputs)} layers")

    # ---------- per-layer verification ----------
    mxu = GoldenMXU()
    total = 0
    passed = 0
    failed = []

    lines.append(f"\n{'='*70}")
    lines.append("Per-Layer Func Model vs PyTorch (Raw Conv2D Output)")
    lines.append(f"{'='*70}")
    hdr = f"{'Layer':50s} {'Groups':>7s} {'Shape':20s} {'CosSim':>8s} {'MaxErr':>10s} {'Verdict':>8s}"
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for name, conv in conv_list:
        inp = conv_inputs.get(name)
        ref_out = conv_outputs.get(name)

        if inp is None or ref_out is None:
            lines.append(f"{name:50s} {'SKIP':>7s} {'':20s} {'—':>8s} {'—':>10s} {'—':>8s}")
            continue

        try:
            w_np = conv.weight.detach().numpy().astype(np.float32)
            b_np = conv.bias.detach().numpy().astype(np.float32) if conv.bias is not None else None
            stride_val = int(conv.stride[0])
            pad_val = conv.padding
            groups_val = int(conv.groups)

            fm_out = conv2d_func_model(
                mxu, inp, w_np, b_np,
                stride=stride_val, padding=pad_val, groups=groups_val,
            )

            cs = cos_sim(fm_out, ref_out)
            max_err = float(np.max(np.abs(
                fm_out.astype(np.float32) - ref_out.astype(np.float32)
            )))
            total += 1
            ok = cs >= 0.99
            if ok:
                passed += 1
            else:
                failed.append((name, cs, max_err))

            is_dw = groups_val > 1 and groups_val == conv.in_channels
            grp = "DW" if is_dw else f"g={groups_val}"
            shape_str = f"{list(fm_out.shape)}"

            lines.append(
                f"{name:50s} {grp:>7s} {shape_str:20s} "
                f"{cs:8.6f} {max_err:10.6f} {'PASS' if ok else 'FAIL':>8s}"
            )

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            lines.append(f"{name:50s} {'ERR':>7s} {'':20s} {'—':>8s} {'—':>10s} {'—':>8s}")
            lines.append(f"  {type(e).__name__}: {e}")
            # Only print first error trace
            if len(failed) == 0:
                lines.append(f"  Traceback: {tb[:300]}")

    lines.append("-" * len(hdr))
    lines.append(f"\nSummary:")
    lines.append(f"  Total layers: {total}")
    lines.append(f"  cos_sim >= 0.99: {passed}")
    lines.append(f"  Target: >=15 layers")

    if failed:
        lines.append(f"  Failed layers:")
        for n, cs, err in failed[:10]:
            lines.append(f"    {n}: cos_sim={cs:.6f}, max_err={err:.6f}")

    if total > 0:
        verdict = "PASS" if passed >= 15 else "FAIL"
        lines.append(f"  Overall: {verdict} ({passed}/{total})")

    lines.append(f"\n{'='*70}")
    lines.append("Quantization")
    lines.append(f"{'='*70}")
    lines.append(f"  Weights: INT4 per-block (group_size={GROUP_SIZE})")
    lines.append(f"  Activations: INT8 per-tensor symmetric")
    lines.append(f"  im2col: numpy (Python)")
    lines.append(f"  GEMM: GoldenMXU.matmul_int4_per_block (64x64 array)")

    evidence_text = "\n".join(lines)
    with open(evidence_path, "w") as f:
        f.write(evidence_text)

    print(evidence_text)
    print(f"\nEvidence: {evidence_path}")
    return evidence_text


# ======================================================================
# Pytest
# ======================================================================

class TestMobileNetV3FuncModel:

    @pytest.fixture(scope="class")
    def ev(self):
        return generate_evidence()

    def test_per_layer_cos_sim(self, ev):
        for line in ev.split('\n'):
            if 'cos_sim >= 0.99:' in line:
                cnt = int(line.split(':')[-1].strip())
                assert cnt >= 15, f"Need >=15 layers with cos_sim >= 0.99, got {cnt}"
                return
        assert False, "Could not parse cos_sim pass count"


if __name__ == "__main__":
    generate_evidence()
