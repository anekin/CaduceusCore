
## 2026-07-19 10:48 UTC — Spike Plugin Rebuild (PH7)

### Lesson: ABI mismatch fixed by rebuild on target server

The `npu_mmio_plugin.so` was originally compiled on a machine with GLIBC 2.32, but sz0001 has GLIBC 2.17. This caused `undefined symbol: _Z15mmio_device_mapB5cxx11v`.

**Fix**: Rebuilt on sz0001 using devtoolset-9 (g++ 9.3.1) linking against system GLIBC 2.17.

**Key commands**:
```bash
source /opt/rh/devtoolset-9/enable
cd spike_src/plugins && make clean && make
```

**Verification**: Plugin loads in Spike without GLIBC errors, connects to MMIO bridge socket. Symbol `_Z15mmio_device_mapv` resolves via system libstdc++.

**Remaining**: mmul_smoke test fails with `BrokenPipeError` after socket connect — pre-existing firmware/bridge protocol issue, NOT plugin ABI.

**Lesson**: Always build shared objects on the target runtime environment. devtoolset-9 provides g++ 9.3.1 with C++17 support on RHEL/CentOS 7 while linking against system GLIBC 2.17.
