"""NoC functional model tests: packet routing, delivery, and round-trip transfer."""

import numpy as np
import pytest
from golden_executor import NoCPacket, GoldenNoC, GoldenExecutor


class TestNoCPacket:
    """NoCPacket construction and auto-size."""

    def test_create_minimal(self):
        p = NoCPacket(0, 1, np.array([1, 2, 3], dtype=np.int8), 1, 0, 3)
        assert p.src_id == 0
        assert p.dst_id == 1
        assert p.size_bytes == 3
        assert p.payload.tolist() == [1, 2, 3]

    def test_auto_size(self):
        p = NoCPacket(2, 3, np.array([10, 20, 30, 40], dtype=np.int32))
        assert p.size_bytes == 16  # 4 elements × 4 bytes each

    def test_auto_size_empty_payload(self):
        p = NoCPacket(0, 0, np.array([], dtype=np.uint8))
        assert p.size_bytes == 0


class TestGoldenNoCRoute:
    """GoldenNoC.route_packet behavior."""

    def test_route_basic(self):
        noc = GoldenNoC()
        p = NoCPacket(0, 1, np.array([1], dtype=np.uint8))
        assert noc.route_packet(p, {}) is True

    def test_route_blocked_missing_dst(self):
        noc = GoldenNoC()
        p = NoCPacket(0, 5, np.array([1], dtype=np.uint8))
        assert noc.route_packet(p, {"active_nodes": {0, 1, 2}}) is False

    def test_route_blocked_congestion(self):
        noc = GoldenNoC()
        p = NoCPacket(0, 1, np.array([1], dtype=np.uint8))
        state = {"congestion": {(0, 1): 1}}
        assert noc.route_packet(p, state) is False

    def test_route_ok_congestion_clear(self):
        noc = GoldenNoC()
        p = NoCPacket(0, 1, np.array([1], dtype=np.uint8))
        state = {"congestion": {(0, 1): 0}}
        assert noc.route_packet(p, state) is True


class TestGoldenNoCDeliver:
    """GoldenNoC.deliver_payload — bit-exact data movement."""

    def test_deliver_basic(self):
        noc = GoldenNoC()
        data = np.array([0xDE, 0xAD, 0xBE, 0xEF], dtype=np.uint8)
        p = NoCPacket(0, 1, data)
        sram = bytearray(16)
        noc.deliver_payload(p, sram, 4)
        assert list(sram[4:8]) == [0xDE, 0xAD, 0xBE, 0xEF]
        # Unwritten bytes remain zero
        assert list(sram[0:4]) == [0, 0, 0, 0]

    def test_deliver_overflow(self):
        noc = GoldenNoC()
        data = np.array([1, 2, 3, 4, 5], dtype=np.uint8)
        p = NoCPacket(0, 1, data)
        sram = bytearray(3)
        with pytest.raises(ValueError, match="overflow"):
            noc.deliver_payload(p, sram, 0)

    def test_deliver_int32_preserves_bytes(self):
        noc = GoldenNoC()
        # A negative int32 has specific byte pattern
        data = np.array([-1, 0, 1], dtype=np.int32)
        p = NoCPacket(0, 1, data)
        sram = bytearray(32)
        noc.deliver_payload(p, sram, 0)
        # Read back and verify
        recovered = np.frombuffer(bytes(sram[:12]), dtype=np.int32)
        assert np.array_equal(recovered, data)


class TestGoldenNoCBuild:
    """GoldenNoC.build_transfer_packet."""

    def test_build_transfer(self):
        noc = GoldenNoC()
        data = np.array([1, 2, 3, 4], dtype=np.int8)
        p = noc.build_transfer_packet(0, 1, data, priority=2)
        assert p.src_id == 0
        assert p.dst_id == 1
        assert p.priority == 2
        assert p.size_bytes == 4
        assert np.array_equal(p.payload, data)

    def test_build_auto_ndarray(self):
        noc = GoldenNoC()
        p = noc.build_transfer_packet(3, 7, [10, 20])  # plain list
        assert p.size_bytes == 16  # int64 by default on most platforms


class TestGoldenNoCRoundTrip:
    """End-to-end round-trip: node0 → node1, echo back, payloads match."""

    def test_round_trip_echo(self):
        noc = GoldenNoC()
        # Node 0 builds a packet for node 1
        original = np.array([1, 2, 3, 4, 5], dtype=np.int32)
        pkt = noc.build_transfer_packet(0, 1, original, priority=0)

        # Route from node0 → node1 (empty network state = all clear)
        assert noc.route_packet(pkt, {}) is True

        # Deliver to node1's SRAM at address 0
        sram_node1 = bytearray(256)
        noc.deliver_payload(pkt, sram_node1, 0)

        # Node 1 reads from its SRAM and builds echo packet back to node0
        recovered = np.frombuffer(bytes(sram_node1[:20]), dtype=np.int32)
        echo_pkt = noc.build_transfer_packet(1, 0, recovered, priority=1)

        # Route echo back
        assert noc.route_packet(echo_pkt, {}) is True

        # Deliver to node0's SRAM at address 64
        sram_node0 = bytearray(256)
        noc.deliver_payload(echo_pkt, sram_node0, 64)

        # Node 0 reads back and compares
        echo_recovered = np.frombuffer(bytes(sram_node0[64:84]), dtype=np.int32)
        assert np.array_equal(original, echo_recovered), \
            f"Round-trip payload mismatch: {original} vs {echo_recovered}"

    def test_golden_executor_has_noc(self):
        """GoldenExecutor registers self.noc."""
        exec = GoldenExecutor()
        assert hasattr(exec, "noc")
        assert isinstance(exec.noc, GoldenNoC)
