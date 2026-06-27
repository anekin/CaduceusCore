#!/usr/bin/env python3
"""Extract Func Model per-op cycle predictions for Qwen2.5-3B blk.0.

Creates rtl/test_vectors/qwen_blk0/func_model_cycles.json with exactly 17 entries
(9 GEMM + 5 SFU + 3 Vector), each containing name, total cycles, DMA cycles,
compute cycles, and bottleneck field.

Usage: cd CaduceusCore && PYTHONPATH=sim python scripts/extract_func_model_cycles.py
"""

import json
import sys
from pathlib import Path

import yaml

# Add sim/ to path so we can import engine + models
_sim_dir = Path(__file__).resolve().parent.parent / "sim"
if str(_sim_dir) not in sys.path:
    sys.path.insert(0, str(_sim_dir))

from engine.mac_engine import create_engine   # noqa: E402
from models.sfu import SFUModel                # noqa: E402
from models.vector import VectorModel          # noqa: E402
from model_specs import get_spec               # noqa: E402


def _build_config(config_path: Path) -> dict:
    """Load the YAML config and return a dict suitable for create_engine."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rmsnorm_cycles(sfu: SFUModel, elements: int) -> int:
    """RMSNorm cycle count = sqrt(single-scalar) + div(all-elements) + pipeline overhead.

    Formula from plan T5:
        sfu.estimate("sqrt", 1) + sfu.estimate("div", elements) + 10
    """
    sqrt_c = int(sfu.estimate("sqrt", 1))
    div_c = int(sfu.estimate("div", elements))
    return sqrt_c + div_c + 10


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "sim" / "config" / "npu_config.yaml"

    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        return 1

    config = _build_config(config_path)
    spec = get_spec("qwen2.5-3b")

    engine = create_engine(config)
    sfu = SFUModel(config)
    vector = VectorModel(config)

    HIDDEN = spec.hidden                # 2560
    INTERMEDIATE = spec.intermediate    # 9728
    QKV = spec.qkv_dim                  # 4096
    N_HEADS = spec.num_heads            # 32
    KV_HEADS = spec.kv_heads            # 2
    H_DIM = spec.head_dim               # 128
    KV_DIM = KV_HEADS * H_DIM           # 256

    entries: list[dict] = []

    # ── Op sequence matching T4:  [RMSNORM, Q, K, V, ROPE, attn_score,
    #   SOFTMAX, attn_weight, O, VRESID, RMSNORM, gate, up, SILU, VMUL,
    #   down, VRESID] ──────────────────────────────────────────────────

    # 1. RMSNORM pre-attn
    rn1 = _rmsnorm_cycles(sfu, HIDDEN)
    entries.append({"name": "RMSNORM pre-attn", "cycles": rn1,
                    "dma_cycles": 0, "compute_cycles": rn1,
                    "bottleneck": "compute"})

    # 2. Q_proj
    r = engine.estimate(1, HIDDEN, QKV)
    entries.append({"name": "Q_proj", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 3. K_proj
    r = engine.estimate(1, HIDDEN, KV_DIM)
    entries.append({"name": "K_proj", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 4. V_proj
    r = engine.estimate(1, HIDDEN, KV_DIM)
    entries.append({"name": "V_proj", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 5. ROPE (Q + K elements)
    rope_c = int(sfu.estimate("rope", QKV + KV_DIM))
    entries.append({"name": "ROPE", "cycles": rope_c,
                    "dma_cycles": 0, "compute_cycles": rope_c,
                    "bottleneck": "compute"})

    # 6. attn_score  Q(N_heads, H_DIM) × K^T(H_DIM, KV_heads) = (32, 128)×(128, 2)
    r = engine.estimate(N_HEADS, H_DIM, KV_HEADS)
    entries.append({"name": "attn_score", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 7. SOFTMAX
    softmax_c = int(sfu.estimate("softmax", HIDDEN))
    entries.append({"name": "SOFTMAX", "cycles": softmax_c,
                    "dma_cycles": 0, "compute_cycles": softmax_c,
                    "bottleneck": "compute"})

    # 8. attn_weight  scores(N_heads, KV_heads) × V(KV_heads, H_DIM) = (32,2)×(2,128)
    r = engine.estimate(N_HEADS, KV_HEADS, H_DIM)
    entries.append({"name": "attn_weight", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 9. O_proj
    r = engine.estimate(1, QKV, HIDDEN)
    entries.append({"name": "O_proj", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 10. VRESID pre-attn
    vr1 = int(vector.estimate("add", HIDDEN))
    entries.append({"name": "VRESID pre-attn", "cycles": vr1,
                    "dma_cycles": 0, "compute_cycles": vr1,
                    "bottleneck": "compute"})

    # 11. RMSNORM post-attn
    rn2 = _rmsnorm_cycles(sfu, HIDDEN)
    entries.append({"name": "RMSNORM post-attn", "cycles": rn2,
                    "dma_cycles": 0, "compute_cycles": rn2,
                    "bottleneck": "compute"})

    # 12. gate
    r = engine.estimate(1, HIDDEN, INTERMEDIATE)
    entries.append({"name": "gate", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 13. up
    r = engine.estimate(1, HIDDEN, INTERMEDIATE)
    entries.append({"name": "up", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 14. SILU
    silu_c = int(sfu.estimate("silu", INTERMEDIATE))
    entries.append({"name": "SILU", "cycles": silu_c,
                    "dma_cycles": 0, "compute_cycles": silu_c,
                    "bottleneck": "compute"})

    # 15. VMUL gate*up
    vmul_c = int(vector.estimate("mul", INTERMEDIATE))
    entries.append({"name": "VMUL gate*up", "cycles": vmul_c,
                    "dma_cycles": 0, "compute_cycles": vmul_c,
                    "bottleneck": "compute"})

    # 16. down
    r = engine.estimate(1, INTERMEDIATE, HIDDEN)
    entries.append({"name": "down", "cycles": int(r.total_cycles),
                    "dma_cycles": int(r.dma_cycles), "compute_cycles": int(r.compute_cycles),
                    "bottleneck": r.bottleneck})

    # 17. VRESID post-attn
    vr2 = int(vector.estimate("add", HIDDEN))
    entries.append({"name": "VRESID post-attn", "cycles": vr2,
                    "dma_cycles": 0, "compute_cycles": vr2,
                    "bottleneck": "compute"})

    # ── Validation ──────────────────────────────────────────────────
    if len(entries) != 17:
        print(f"FAIL: expected 17 entries, got {len(entries)}", file=sys.stderr)
        return 1

    for e in entries:
        if not isinstance(e["cycles"], int) or e["cycles"] <= 0:
            print(f"FAIL: {e['name']} has invalid cycles={e['cycles']}", file=sys.stderr)
            return 1
        if not isinstance(e["dma_cycles"], int) or e["dma_cycles"] < 0:
            print(f"FAIL: {e['name']} has invalid dma_cycles={e['dma_cycles']}", file=sys.stderr)
            return 1
        if not isinstance(e["compute_cycles"], int) or e["compute_cycles"] <= 0:
            print(f"FAIL: {e['name']} has invalid compute_cycles={e['compute_cycles']}", file=sys.stderr)
            return 1

    total_cycles = sum(e["cycles"] for e in entries)
    if total_cycles <= 1000:
        print(f"FAIL: total cycles {total_cycles} <= 1000", file=sys.stderr)
        return 1

    # ── Write JSON ─────────────────────────────────────────────────
    out_dir = repo_root / "rtl" / "test_vectors" / "qwen_blk0"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "func_model_cycles.json"

    with open(out_path, "w") as f:
        json.dump(entries, f, indent=2)
    out_path.chmod(0o644)

    print(f"OK: wrote {len(entries)} entries to {out_path}")
    print(f"  Total cycles: {total_cycles:,}")
    for e in entries:
        b = e["bottleneck"]
        flag = " ←" if b == "dma" else ""
        print(f"  {e['name']:22s}  cycles={e['cycles']:>8,d}"
              f"  dma={e['dma_cycles']:>8,d}"
              f"  comp={e['compute_cycles']:>8,d}"
              f"  bottleneck={b}{flag}")
    print(f"  {'─'*85}")
    print(f"  {'TOTAL':22s}  cycles={total_cycles:>8,d}")

    # Additional: print JSON to stdout for validation with `python -m json.tool`
    print()
    print(json.dumps(entries, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
