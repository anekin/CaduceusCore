#!/usr/bin/env bash
# Idempotently ensure assets/mobilenetv3_small.onnx exists.
# Prefer local generation via torchvision/torch; fall back to a public URL;
# fail with clear instructions if neither works.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSET_DIR="${SCRIPT_DIR}/../assets"
MODEL_PATH="${ASSET_DIR}/mobilenetv3_small.onnx"

if [[ -f "${MODEL_PATH}" ]]; then
    echo "[skip] ${MODEL_PATH} already exists; leaving it untouched."
    exit 0
fi

mkdir -p "${ASSET_DIR}"

generate_with_torch() {
    python3 - <<'PY' "$1"
import sys
model_path = sys.argv[1]

try:
    import torch
    import torchvision
except ImportError as exc:
    print(f"[torch] missing dependency: {exc}")
    sys.exit(1)

try:
    model = torchvision.models.mobilenet_v3_small(weights="DEFAULT")
    model.eval()
    dummy_input = torch.randn(1, 3, 224, 224)
    opset_version = 17
    torch.onnx.export(
        model,
        dummy_input,
        model_path,
        opset_version=opset_version,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
    )
    try:
        import onnx
        onnx.checker.check_model(onnx.load(model_path))
        print("[torch] exported and validated")
    except ImportError:
        print("[torch] exported (onnx package not available for validation)")
except Exception as exc:
    print(f"[torch] export failed: {exc}")
    sys.exit(1)
PY
}

download_from_url() {
    local url="${1:-}"
    local dest="$2"
    if [[ -z "${url}" ]]; then
        echo "[download] MOBILENETV3_ONNX_URL is not set"
        return 1
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "${dest}" "${url}"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "${dest}" "${url}"
    else
        echo "[download] neither curl nor wget is available"
        return 1
    fi
}

echo "[info] ${MODEL_PATH} not found; attempting torchvision/torch export..."
if generate_with_torch "${MODEL_PATH}"; then
    echo "[ok] generated ${MODEL_PATH}"
    exit 0
fi

echo "[info] torch export unavailable or failed; attempting download fallback..."
if download_from_url "${MOBILENETV3_ONNX_URL:-}" "${MODEL_PATH}"; then
    echo "[ok] downloaded ${MODEL_PATH}"
    exit 0
fi

cat >&2 <<'MSG'
Error: could not obtain assets/mobilenetv3_small.onnx.

To fix this, choose one of the following:

1. Install torch + torchvision and re-run this script:
      pip install torch torchvision
      bash scripts/gen_mobilenetv3_onnx.sh

2. Provide a direct download URL and re-run:
      MOBILENETV3_ONNX_URL=https://example.com/mobilenetv3_small.onnx \
        bash scripts/gen_mobilenetv3_onnx.sh

3. Manually place a MobileNetV3-Small ONNX file at:
      assets/mobilenetv3_small.onnx
   (input shape 1x3x224x224, opset >= 13)
MSG
exit 1
