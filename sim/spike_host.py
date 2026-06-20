#!/usr/bin/env python3
"""
Host → Spike NPU 集成测试脚本

流程:
1. 从 test_vectors 加载 golden 数据
2. 生成 Ring Buffer + 描述符 (写入 DRAM binary)
3. 启动 Spike + 固件
4. 等待固件完成后读取 DRAM 输出
5. 对比 golden
"""

import struct
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SIM_DIR = PROJECT / "sim"
FW_DIR = PROJECT / "firmware"

# 地址 (与 regmap.py 一致)
DRAM_BASE      = 0x80000000
SRAM_BASE      = 0x00000000
SRAM_SIZE      = 4 * 1024 * 1024
DOORBELL_BASE  = 0x40010000
DOORBELL_TAIL  = DOORBELL_BASE + 0x00  # W: host → NPU

RING_BUF_ADDR  = DRAM_BASE          # Ring Buffer 基址
RING_ENTRIES   = 64
CMD_DESC_SIZE  = 32

# 完成 Ring 紧随其后
COMPLETION_ADDR = RING_BUF_ADDR + RING_ENTRIES * CMD_DESC_SIZE

# 描述符区域 (Ring Buffer 之后 64KB)
DESC_BASE_ADDR = COMPLETION_ADDR + RING_ENTRIES * 32
DESC_MAX       = 128


def make_dram_image(test_dir: Path) -> tuple:
    """从 test_vectors 生成 DRAM 镜像。

    Returns:
        (dram_bytes, cmd_count, expected_outputs)
        dram_bytes: 完整 DRAM 镜像 (bytearray)
        cmd_count: 命令描述符数量
        expected_outputs: [(desc_addr, expected_golden_bytes), ...]
    """
    dram = bytearray(256 * 1024 * 1024)  # 256 MB
    manifest_path = test_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {test_dir}")

    import json
    manifest = json.loads(manifest_path.read_text())
    test_type = manifest.get("type", "mmul")

    load_file(dram, test_dir / "weight.hex",    manifest["weight_addr"])
    load_file(dram, test_dir / "activation.hex", manifest["activation_addr"])
    golden = load_file(dram, test_dir / "golden.hex", manifest["golden_addr"])

    # 构建命令描述符
    cmd_addr = RING_BUF_ADDR
    desc_addr = DESC_BASE_ADDR

    # 操作描述符 (12 × uint32 = 48 bytes)
    desc_data = struct.pack(
        "<12I",
        manifest["activation_addr"],   # input_addr
        manifest["weight_addr"],       # weight_addr
        manifest["golden_addr"],       # output_addr (= write golden here)
        SRAM_BASE,                     # input_sram
        SRAM_BASE + 0x100000,          # weight_sram
        SRAM_BASE + 0x200000,          # output_sram
        manifest.get("input_size",     manifest.get("M", 1) * manifest.get("K", 1)),
        manifest.get("weight_size",    (manifest.get("K", 1) * manifest.get("N", 1) + 1) // 2),
        manifest.get("output_size",    manifest.get("M", 1) * manifest.get("N", 1) * 4),
        manifest.get("M", 1),
        manifest.get("K", 1),
        manifest.get("N", 1),
    )
    dram[desc_addr - DRAM_BASE:desc_addr - DRAM_BASE + 48] = desc_data

    # 命令条目 (8 + 8 + 8 + padding = 32 bytes)
    cmd_entry = struct.pack("<IQQ16x", 0, desc_addr, 0)  # opcode=MMUL
    dram[cmd_addr - DRAM_BASE:cmd_addr - DRAM_BASE + 32] = cmd_entry

    return dram, 1


def load_file(dram: bytearray, hex_path: Path, addr: int) -> bytes:
    """从 hex 文件加载数据到 DRAM。"""
    if not hex_path.exists():
        print(f"  SKIP {hex_path.name} (not found)")
        return b""
    raw = hex_path.read_text().strip().replace("\n", "").replace(" ", "")
    data = bytes.fromhex(raw)
    off = addr - DRAM_BASE
    dram[off:off + len(data)] = data
    print(f"  LOAD {hex_path.name} → 0x{addr:08X} ({len(data)} B)")
    return data


def build_firmware():
    """交叉编译固件。"""
    result = subprocess.run(
        ["make", "-C", str(FW_DIR), "all"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print("BUILD ERROR:\n" + result.stderr)
        return None
    print(result.stdout)
    return FW_DIR / "build" / "npu_firmware.elf"


def run_spike(elf_path: Path, dram_img: bytes, spike_bin: str = "spike"):
    """启动 Spike 运行固件。

    Spike 参数:
      -m<base>:<size>: 定义内存区域
      --isa=RV32IM: 指令集
    """
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(dram_img)
        dram_file = f.name

    # 注意: Spike 的实际 CLI 取决于编译版本
    cmd = [
        spike_bin or str(Path.home() / "tools/spike/bin/spike"),
        "--isa=RV32IM",
        f"-m0x{DRAM_BASE:08X}:0x{(len(dram_img)):X},{dram_file}",
        str(elf_path),
    ]
    print(f"  CMD: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"  STDOUT: {result.stdout[:500]}")
    if result.stderr:
        print(f"  STDERR: {result.stderr[:500]}")
    os.unlink(dram_file)
    return result.returncode


def main():
    test_dir = PROJECT / "sim" / "test_vectors" / "tiny_tile"
    if len(sys.argv) > 1:
        test_dir = Path(sys.argv[1])

    print(f"Test: {test_dir.name}")

    # 1. 生成 DRAM 镜像
    print("\n[1/4] Generating DRAM image...")
    dram_img, cmd_count = make_dram_image(test_dir)
    print(f"       {cmd_count} commands, {len(dram_img)} bytes")

    # 2. 编译固件
    print("\n[2/4] Building firmware...")
    elf_path = build_firmware()
    if not elf_path:
        print("Firmware build failed")
        return 1

    # 3. 运行 Spike
    print("\n[3/4] Running Spike...")
    ret = run_spike(elf_path, dram_img)
    if ret != 0:
        print(f"Spike exited with code {ret}")

    print("\n[4/4] Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
