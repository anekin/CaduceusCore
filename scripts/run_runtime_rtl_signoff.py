#!/usr/bin/env python3
"""RTL Runtime Signoff Runner (FEASIBILITY-ONLY).

Runs the contract-conformance and negative scenarios against the
RTL protocol mock endpoint and writes evidence JSON.

Usage:
  # Happy: contract conformance
  PYTHONPATH=sim python3 scripts/run_runtime_rtl_signoff.py \\
      --device rtl://mock --matrix contract-conformance \\
      --evidence .omo/evidence/task-18-rtl-runtime.json

  # Negative: malformed protocol + missing EDA preflight
  PYTHONPATH=sim python3 -m pytest sim/tests/test_runtime_rtl_transport.py \\
      -q -k 'malformed_protocol or preflight_missing_eda' \\
      2>&1 | tee .omo/evidence/task-18-rtl-runtime-negative.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR.parent if _SCRIPT_DIR.name == "scripts" else Path.cwd()

# Ensure sim/ is on the import path
_sim_path = _PACKAGE_ROOT / "sim"
if str(_sim_path) not in sys.path:
    sys.path.insert(0, str(_sim_path))


def _start_rtl_mock(sock_path: str):
    """Start the RTL mock endpoint in a background process."""
    from sim.rtl_protocol_endpoint import serve

    server = serve(sock_path=sock_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Wait for socket availability
    for _ in range(100):
        try:
            import socket

            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            s.close()
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.01)
    else:
        raise RuntimeError(f"RTL mock server failed to start on {sock_path}")
    return server


def _run_contract_conformance() -> dict[str, Any]:
    """Run contract conformance tests against the Python mock endpoint."""
    import socket
    import struct

    import flatbuffers

    sys.path.insert(0, str(_PACKAGE_ROOT))
    sys.path.insert(0, str(_PACKAGE_ROOT / "gen"))
    sys.path.insert(0, str(_sim_path))

    from caduceus_device_protocol.BufferAllocRequest import BufferAllocRequestT
    from caduceus_device_protocol.BufferAllocResponse import BufferAllocResponseT
    from caduceus_device_protocol.DeviceMessage import DeviceMessage
    from caduceus_device_protocol.DeviceMessage import DeviceMessageT
    from caduceus_device_protocol.DeviceOpcode import DeviceOpcode
    from caduceus_device_protocol.DeviceStatus import DeviceStatus
    from caduceus_device_protocol.MessageHeader import MessageHeaderT

    from sim.device_protocol import (
        MAGIC,
        PROTOCOL_VERSION,
        build_message,
        parse_message,
        recv_framed,
        send_framed,
    )

    SOCK = "/tmp/caduceus_rtl_signoff.sock"
    server = _start_rtl_mock(SOCK)
    results = []
    tests_run = 0
    tests_passed = 0

    try:
        # Test 1: Magic constant
        tests_run += 1
        magic_ok = MAGIC == 0x43414455
        results.append(
            {
                "test": "protocol_magic",
                "passed": magic_ok,
                "expected": "0x43414455",
                "actual": f"0x{MAGIC:08X}",
            }
        )
        if magic_ok:
            tests_passed += 1

        # Test 2: Protocol version
        tests_run += 1
        version_ok = PROTOCOL_VERSION == 1
        results.append(
            {
                "test": "protocol_version",
                "passed": version_ok,
                "expected": 1,
                "actual": PROTOCOL_VERSION,
            }
        )
        if version_ok:
            tests_passed += 1

        # Test 3-8: Socket-based golden vectors
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)

        try:
            # Test 3: Request ID echo
            tests_run += 1
            inner = BufferAllocRequestT()
            inner.size = 64
            ib = flatbuffers.Builder(256)
            root = inner.Pack(ib)
            ib.Finish(root)

            msg = DeviceMessageT()
            msg.header = MessageHeaderT()
            msg.header.magic = MAGIC
            msg.header.protocolVersion = PROTOCOL_VERSION
            msg.header.requestId = 777
            msg.header.opcode = DeviceOpcode.OPCODE_BUFFER_ALLOC
            msg.header.status = DeviceStatus.STATUS_OK
            msg.payload = bytes(ib.Output())

            send_framed(s, build_message(msg))
            wire = recv_framed(s)
            resp, _ = parse_message(bytes(wire))
            rid_ok = resp.header.requestId == 777
            results.append(
                {
                    "test": "request_id_echo",
                    "passed": rid_ok,
                    "expected": 777,
                    "actual": resp.header.requestId,
                }
            )
            if rid_ok:
                tests_passed += 1

            # Test 4: Opcode echo
            tests_run += 1
            opcode_ok = (
                resp.header.opcode == DeviceOpcode.OPCODE_BUFFER_ALLOC
            )
            results.append(
                {
                    "test": "opcode_echo",
                    "passed": opcode_ok,
                    "expected": DeviceOpcode.OPCODE_BUFFER_ALLOC,
                    "actual": resp.header.opcode,
                }
            )
            if opcode_ok:
                tests_passed += 1

            # Test 5: Status is OK
            tests_run += 1
            status_ok = resp.header.status == DeviceStatus.STATUS_OK
            results.append(
                {
                    "test": "status_ok",
                    "passed": status_ok,
                    "expected": DeviceStatus.STATUS_OK,
                    "actual": resp.header.status,
                }
            )
            if status_ok:
                tests_passed += 1

            # Test 6: Checksum valid
            tests_run += 1
            _, computed = parse_message(bytes(wire))
            checksum_ok = computed == resp.header.checksum
            results.append(
                {
                    "test": "checksum_valid",
                    "passed": checksum_ok,
                    "expected": resp.header.checksum,
                    "actual": computed,
                }
            )
            if checksum_ok:
                tests_passed += 1

            # Test 7: Buffer alloc returns valid handle
            tests_run += 1
            pl = bytes(resp.payload)
            inner_resp = BufferAllocResponseT.InitFromPackedBuf(pl, 0)
            handle_ok = inner_resp.handle > 0
            results.append(
                {
                    "test": "buffer_alloc_handle",
                    "passed": handle_ok,
                    "expected": "> 0",
                    "actual": inner_resp.handle,
                }
            )
            if handle_ok:
                tests_passed += 1

            # Test 8: Bad magic rejected (patch raw wire, bypass build_message)
            tests_run += 1
            bad_msg = DeviceMessageT()
            bad_msg.header = MessageHeaderT()
            bad_msg.header.magic = MAGIC  # build_message corrects this
            bad_msg.header.protocolVersion = PROTOCOL_VERSION
            bad_msg.header.requestId = 1
            bad_msg.header.opcode = DeviceOpcode.OPCODE_BUFFER_ALLOC
            bad_msg.header.status = DeviceStatus.STATUS_OK
            bad_msg.payload = bytes(ib.Output())

            bad_wire = bytearray(build_message(bad_msg))
            # Patch magic at the inline MessageHeader struct position
            view = DeviceMessage.GetRootAs(bad_wire)
            hdr_off = view.Header()._tab.Pos
            bad_wire[hdr_off : hdr_off + 4] = struct.pack("<I", 0xAAAAAAAA)

            send_framed(s, bad_wire)
            bad_rx = recv_framed(s)
            bad_resp, _ = parse_message(bytes(bad_rx))
            reject_ok = bad_resp.header.status == DeviceStatus.STATUS_INVALID_MESSAGE
            results.append(
                {
                    "test": "bad_magic_rejected",
                    "passed": reject_ok,
                    "expected": DeviceStatus.STATUS_INVALID_MESSAGE,
                    "actual": bad_resp.header.status,
                }
            )
            if reject_ok:
                tests_passed += 1

        finally:
            s.close()

    finally:
        server.server_close()
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass

    return {
        "matrix": "contract-conformance",
        "device": "rtl://mock",
        "tests_run": tests_run,
        "tests_passed": tests_passed,
        "tests_failed": tests_run - tests_passed,
        "verdict": "pass" if tests_passed == tests_run else "fail",
        "results": results,
    }


def _run_negative_tests() -> dict[str, Any]:
    """Run the C++ negative tests (preflight + malformed)."""
    build_dir = _PACKAGE_ROOT / "build" / "software"

    # Check if the C++ test binary exists
    test_bin = build_dir / "test_rtl_transport_negative"
    if not test_bin.is_file():
        return {
            "matrix": "negative",
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "verdict": "skip",
            "error": f"test binary not found: {test_bin}",
            "results": [],
        }

    result = subprocess.run(
        [str(test_bin)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    passed = result.returncode == 0

    return {
        "matrix": "negative-preflight",
        "device": "rtl:// (bare, no VCS/simv)",
        "tests_run": 1,
        "tests_passed": 1 if passed else 0,
        "tests_failed": 0 if passed else 1,
        "verdict": "pass" if passed else "fail",
        "results": [
            {
                "test": "rtl_transport_negative",
                "passed": passed,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CaduceusCore RTL Runtime Signoff Runner"
    )
    parser.add_argument(
        "--device",
        default="rtl://mock",
        help="Device URI (default: rtl://mock)",
    )
    parser.add_argument(
        "--matrix",
        default="contract-conformance",
        help="Test matrix to run (contract-conformance, negative, all)",
    )
    parser.add_argument(
        "--evidence",
        default=".omo/evidence/task-18-rtl-runtime.json",
        help="Evidence output file",
    )
    args = parser.parse_args(argv)

    evidence: dict[str, Any] = {
        "task": 18,
        "phase": "feasibility-only",
        "transport": "rtl",
        "transport_schema": {
            "protocol": "FlatBuffers DeviceMessage",
            "magic": "0x43414455 (CADU)",
            "version": 1,
            "framing": "length-prefixed (4-byte BE uint32 + payload)",
            "checksum": "CRC-32/IEEE over wire bytes with checksum field zeroed",
            "header_layout": "32 bytes: magic(4) + version(4) + request_id(8) + opcode(4) + payload_length(4) + status(4) + checksum(4)",
        },
        "fixture_mode": "mock",
        "intended_rtl_integration_path": (
            "rtl:// (bare) performs EDA preflight (VCS + simv_soc_top), "
            "then connects to the SoC RTL simulator via Unix socket. "
            "rtl://mock connects to the Python mock endpoint for contract "
            "validation. Full SoC RTL replay is deferred."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "records": [],
    }

    if args.matrix in ("contract-conformance", "all"):
        happy = _run_contract_conformance()
        evidence["records"].append(happy)

    if args.matrix in ("negative", "all"):
        negative = _run_negative_tests()
        evidence["records"].append(negative)

    # Write evidence
    ev_path = Path(args.evidence)
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps(evidence, indent=2) + "\n")

    # Determine overall verdict
    all_pass = all(r.get("verdict") == "pass" for r in evidence["records"])
    print(json.dumps(evidence, indent=2))
    print(f"\nEvidence written to {ev_path}")
    print(f"Overall verdict: {'PASS' if all_pass else 'FAIL'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
