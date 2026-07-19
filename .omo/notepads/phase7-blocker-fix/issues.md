
## 2026-07-19 10:48 UTC — PH7: Spike Plugin Rebuild

### Issue: Spike plugin ABI mismatch (RESOLVED)
**Symptom**: `GLIBC_2.32 not found` → `undefined symbol: _Z15mmio_device_mapB5cxx11v`
**Fix**: Rebuilt on sz0001 with devtoolset-9 (g++ 9.3.1). Plugin now links against GLIBC 2.17.
**Evidence**: `build/evidence/ph7-spike-fixed.txt`

### Remaining: mmul_smoke test fails (OPEN)
**Symptom**: `BrokenPipeError` after spike plugin connects to MMIO bridge socket.
**Note**: Plugin loads and connects (no ABI error). Firmware/bridge protocol fails immediately after connection. Likely firmware protocol mismatch or missing NPU hardware simulation. Needs separate investigation of firmware IRQ/NPU_HEAD signaling.
**Status**: OPEN — not blocking PH7 plugin fix.
