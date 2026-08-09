"""
AXI Tracer — record all memory-mapped I/O transactions for RTL comparison.

Outputs JSON transaction log with: timestamp, operation, address, data, module.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AXITransaction:
    """Single AXI/MMIO transaction."""
    seq: int
    op: str           # 'R' or 'W'
    addr: int
    data: int         # value written or read
    module: str       # 'MXU', 'SFU', 'VECTOR', 'DMA', 'DOORBELL', 'INTC', 'DRAM', 'SRAM'
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            'seq': self.seq,
            'op': self.op,
            'addr': f'0x{self.addr:08X}',
            'data': f'0x{self.data:08X}',
            'module': self.module,
            'ts': round(self.timestamp, 6),
        }


class AXITracer:
    """Records all AXI transactions with module-level attribution."""

    def __init__(self):
        self.transactions: List[AXITransaction] = []
        self._seq = 0
        self._start_time = time.time()

    def record(self, op: str, addr: int, data: int, module: str):
        self._seq += 1
        self.transactions.append(AXITransaction(
            seq=self._seq,
            op=op,
            addr=addr,
            data=data,
            module=module,
            timestamp=time.time() - self._start_time,
        ))

    def classify_addr(self, addr: int) -> str:
        """Classify address into module name."""
        from regmap import Addr
        if addr >= Addr.DRAM_BASE:
            return 'DRAM'
        if addr < 0x40000000:
            return 'SRAM'
        base = addr & 0xFFFFF000
        module_map = {
            Addr.MXU_BASE & 0xFFFFF000: 'MXU',
            Addr.SFU_BASE & 0xFFFFF000: 'SFU',
            Addr.VECTOR_BASE & 0xFFFFF000: 'VECTOR',
            Addr.DMA_BASE & 0xFFFFF000: 'DMA',
            Addr.DOORBELL & 0xFFFFF000: 'DOORBELL',
            Addr.INTC_BASE & 0xFFFFF000: 'INTC',
        }
        return module_map.get(base, 'UNKNOWN')

    def to_json(self, path: Optional[str] = None) -> str:
        """Export transactions to JSON string or file."""
        data = {
            'total_transactions': len(self.transactions),
            'transactions': [t.to_dict() for t in self.transactions],
        }
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        if path:
            with open(path, 'w') as f:
                f.write(json_str)
        return json_str

    def summary(self) -> str:
        """Human-readable summary of transaction log."""
        lines = [f"AXI Trace: {len(self.transactions)} transactions"]
        by_module: Dict[str, int] = {}
        by_op: Dict[str, int] = {}
        for t in self.transactions:
            by_module[t.module] = by_module.get(t.module, 0) + 1
            by_op[t.op] = by_op.get(t.op, 0) + 1
        lines.append(f"  Reads: {by_op.get('R', 0)}, Writes: {by_op.get('W', 0)}")
        for mod, count in sorted(by_module.items()):
            lines.append(f"  {mod}: {count}")
        return '\n'.join(lines)

    def verify_ordering(self) -> List[str]:
        """Verify transaction ordering: DMA transfers before MXU starts, etc."""
        warnings = []
        txs = self.transactions

        # Track state transitions
        dma_done = False
        mxu_started = False
        for t in txs:
            if t.module == 'DMA' and t.op == 'W' and (t.addr & 0xFF) == 0x08:  # STATUS = DONE
                dma_done = True
            if t.module == 'MXU' and t.op == 'W' and (t.addr & 0xFF) == 0x04:  # CMD = START
                if not dma_done:
                    warnings.append(f"MXU started before DMA done at seq={t.seq}")
                mxu_started = True

        if not warnings:
            warnings.append("✅ Transaction ordering verified")
        return warnings

    def clear(self):
        self.transactions.clear()
        self._seq = 0
        self._start_time = time.time()
