"""SpikeFirmware — drop-in replacement for miniv.py NPUFirmware.

Launches the real compiled RISC-V firmware inside Spike, routes NPU MMIO
through the Unix-socket bridge server, and exposes the same public API as
NPUFirmware so FuncModel and its tests can switch between the Python mock and
the real firmware with no caller changes.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from miniv import BOOT_ROM_BASE, BOOT_ROM_SIZE, DMEM_BASE, DMEM_SIZE
from regmap import Addr, DOORBELL
from spike_mmio_server import DEFAULT_SOCK_PATH, serve


_HERE = Path(__file__).parent

SPIKE_BIN = _HERE.parent / "spike_src" / "build" / "spike"
FIRMWARE_ELF = _HERE.parent / "firmware" / "build" / "npu_firmware_spike.elf"
PLUGIN_SO = _HERE.parent / "spike_src" / "plugins" / "npu_mmio_plugin.so"

# Instance counter for unique socket paths.
_instance_counter = 0
_instance_lock = threading.Lock()


def _is_spike_available() -> bool:
    return SPIKE_BIN.exists() and PLUGIN_SO.exists() and FIRMWARE_ELF.exists()


class SpikeFirmware:
    """Real RISC-V firmware running under Spike.

    Mirrors the public surface of ``miniv.NPUFirmware``:

    - ``doorbell``: dict with ``host_tail`` and ``npu_head``
    - ``ring_buffer_addr``: DRAM base address of the command ring
    - ``ring_size``: number of ring entries
    - ``run_loop(max_commands)``: dispatch pending commands
    - ``dispatch_interrupt(source_bit)``: records IRQ service
    - ``bind_riscv(riscv)``: no-op compatibility stub
    - ``boot(riscv, boot_rom_path)``: sets RISCVMini boot state

    Each ``run_loop`` invocation launches a fresh Spike process, serializes the
    current Python ``model.dram`` into ``ddr.bin``, and waits for the firmware
    to advance ``NPU_HEAD`` to the expected value.  This lets tests queue
    commands in multiple batches (e.g. FM-SOC-10X) without requiring live DRAM
    updates inside a long-running Spike process.
    """

    def __init__(
        self,
        sim_modules: dict,
        bridge=None,
        spike_bin: Optional[Path] = None,
        plugin_so: Optional[Path] = None,
        firmware_elf: Optional[Path] = None,
        serve_fn: Optional[Callable] = None,
    ):
        self.mod = sim_modules
        self.bridge = bridge
        self.crossbar = sim_modules.get('crossbar')
        self.doorbell: Dict[str, int] = {"host_tail": 0, "npu_head": 0}
        self.ring_buffer_addr = 0x80000000
        self.ring_size = 16
        self.irq_pending = 0
        self._irq_serviced = False
        self._irq_enabled: Dict[int, bool] = {}
        self.riscv: Optional["RISCVMini"] = None  # noqa: F821

        self._spike_bin = spike_bin or SPIKE_BIN
        self._plugin_so = plugin_so or PLUGIN_SO
        self._firmware_elf = firmware_elf or FIRMWARE_ELF
        self._serve_fn = serve_fn or serve

        global _instance_counter
        with _instance_lock:
            _instance_counter += 1
            inst_id = _instance_counter
        self._sock_path = (
            Path(os.environ.get("NPU_SOCK_PATH", DEFAULT_SOCK_PATH)).parent
            / f"npu_mmio_{os.getpid()}_{inst_id}.sock"
        )

        self._proc: Optional[subprocess.Popen] = None
        self._server: Optional[threading.Thread] = None
        self._server_obj: Optional[object] = None
        self._cleanup_registered = False

    def __del__(self):
        self.cleanup()

    def bind_riscv(self, riscv: "RISCVMini") -> None:  # noqa: F821
        """Compatibility: real firmware does not need the Python emulator."""
        self.riscv = riscv

    def boot(self, riscv: "RISCVMini", boot_rom_path: Optional[str] = None) -> None:  # noqa: F821
        """Set the Python RISC-V emulator boot state to match NPUFirmware."""
        self.riscv = riscv
        riscv.state.pc = 0x00000000
        riscv.state.write(2, DMEM_BASE + DMEM_SIZE)
        if boot_rom_path is not None and os.path.exists(boot_rom_path):
            from miniv import RISCVMini

            RISCVMini.load_hex(boot_rom_path, riscv._boot_rom, BOOT_ROM_BASE)

    def dispatch_interrupt(self, source_bit: int) -> None:
        """Record that an interrupt was serviced (used by tests directly)."""
        self._irq_serviced = True

    def run_loop(self, max_commands: int = 10) -> List[dict]:
        """Process up to *max_commands* pending doorbell commands via Spike."""
        pending = self._pending_count()
        if pending == 0:
            return []

        count = min(max_commands, pending)
        start_head = self.doorbell["npu_head"]
        expected_head = (start_head + count) % self.ring_size

        self._ensure_spike_running()
        ok = self._poll_npu_head(expected_head, count)
        self.doorbell["npu_head"] = (
            self.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, expected_head)
            if self.bridge is not None
            else expected_head
        )

        self.cleanup()

        if not ok:
            return [{"opcode": 0, "status": "timeout"} for _ in range(count)]

        results = []
        for i in range(count):
            ring_idx = (start_head + i) % self.ring_size
            status_addr = DOORBELL.BASE + DOORBELL.COMPLETION_STATUS + ring_idx * 4
            status = (
                self.bridge._status.get(status_addr, 0)
                if self.bridge is not None
                else 0
            )
            result_status = "done" if status == 0 else "error"
            results.append({"opcode": 0, "status": result_status})
        return results

    def cleanup(self) -> None:
        """Terminate Spike and shut down the bridge server."""
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
            if self._proc.stderr is not None:
                err = self._proc.stderr.read()
                if err:
                    print("[SPIKE STDERR]", err, file=sys.stderr)
            self._proc = None

        if self._server_obj is not None:
            try:
                self._server_obj.shutdown()
            except Exception:
                pass
            self._server_obj = None
            self._server = None

        try:
            if self._sock_path.exists():
                self._sock_path.unlink()
        except FileNotFoundError:
            pass

        if not self._cleanup_registered:
            atexit.register(self.cleanup)
            self._cleanup_registered = True

    # ── Internal helpers ──────────────────────────────────────────────

    def _pending_count(self) -> int:
        tail = self.doorbell["host_tail"]
        head = self.doorbell["npu_head"]
        return (tail - head) % self.ring_size

    def _poll_npu_head(self, expected_head: int, count: int) -> bool:
        if self.bridge is None:
            return False

        timeout = max(30.0, count * 30.0)
        deadline = time.time() + timeout
        addr = DOORBELL.BASE + DOORBELL.NPU_HEAD
        while time.time() < deadline:
            head = self.bridge._status.get(addr, 0)
            if head == expected_head:
                return True
            if self._proc is not None and self._proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def _ensure_spike_running(self) -> None:
        """Serialize DRAM, start the bridge server, and launch Spike."""
        self.cleanup()

        project = _HERE.parent
        ddr_path = project / "ddr.bin"
        dram = self.mod.get("dram") or self.mod.get("crossbar")
        if hasattr(dram, "dram"):
            dram = dram.dram
        if dram is None:
            raise RuntimeError("SpikeFirmware: no DRAM memory available")
        ddr_path.write_bytes(dram)

        # Restore doorbell head so a fresh Spike process resumes from the
        # previous batch rather than re-consuming commands from index 0.
        if self.bridge is not None:
            self.bridge._status[DOORBELL.BASE + DOORBELL.NPU_HEAD] = self.doorbell["npu_head"]

        ready_event = threading.Event()
        self._server_obj = self._serve_fn(
            self.bridge,
            sock_path=str(self._sock_path),
            ready_event=ready_event,
            register_signals=False,
            crossbar=self.crossbar,
        )
        ready_event.wait(timeout=5.0)

        env = os.environ.copy()
        env["NPU_SOCK_PATH"] = str(self._sock_path)
        dtc_search = _HERE.parent.parent.parent / "dtc_src"
        dtc_path = str(dtc_search / "usr" / "bin") if (dtc_search / "usr" / "bin").is_dir() else str(dtc_search)
        env["PATH"] = dtc_path + ":" + env.get("PATH", "")

        # Spike's --kernel loader requires kernel_size < region_size, so the
        # DRAM region must be strictly larger than the serialized ddr.bin.
        dram_size = len(dram)
        spike_dram_size = ((dram_size + (1 << 20) + 0xFFFFF) // 0x100000) * 0x100000

        cmd = [
            str(self._spike_bin),
            "--isa=RV32IM",
            "--pc=0x10000",
            f"-m0x00010000:0x20000,0x80000000:0x{spike_dram_size:x}",
            f"--kernel={ddr_path}",
            f"--extlib={self._plugin_so}",
            "--device=npu,0x20000000",
            str(self._firmware_elf),
        ]

        self._proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


def _spike_available() -> bool:
    """Public helper used by FuncModel to decide whether to use Spike."""
    return _is_spike_available()
