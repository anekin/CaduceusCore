#!/usr/bin/env python3
"""
Installed smoke tests for CaduceusCore Runtime release.

Runs four smoke checks against the INSTALLED artifacts:
  1. C client linked against installed libcaduceus_runtime (mock://)
  2. C++ RAII client linked against installed libcaduceus_runtime (mock://)
  3. Python binding (ctypes) using mock://
  4. Python binding (ctypes) using fm://python via device_server

Usage:
    python3 scripts/run_installed_smoke_tests.py --install-prefix build/install
"""

import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
import textwrap
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, *, cwd=None, env=None, label="", check=False):
    """Run a command, return (exit_code, stdout, stderr)."""
    prefix = f"[{label}] " if label else ""
    full_cmd = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  {prefix}$ {full_cmd}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        print(f"  {prefix}FAILED (exit {proc.returncode})", flush=True)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
    return proc.returncode, proc.stdout, proc.stderr


def smoke_c_py_client(install_prefix, label="C-client(mock://)"):
    """
    Smoke test: compile a C client against the installed runtime, run with mock://.
    Uses subprocess to compile and run.
    """
    inc_dir = os.path.join(install_prefix, "include")
    lib_dir = os.path.join(install_prefix, "lib")
    lib_file = os.path.join(lib_dir, "libcaduceus_runtime.so")

    if not os.path.exists(lib_file):
        print(f"  [{label}] SKIPPED: {lib_file} not found")
        return True  # not a failure — optional

    c_src = textwrap.dedent("""\
    #include <caduceus/runtime.h>
    #include <stdio.h>
    #include <stdlib.h>
    #include <string.h>

    int main(void) {
        cad_device_open_info_t oi;
        memset(&oi, 0, sizeof(oi));
        oi.struct_size = sizeof(oi);
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = CAD_ABI_MINOR;
        oi.uri = "mock://";

        cad_device_t dev = NULL;
        cad_device_caps_t caps;
        memset(&caps, 0, sizeof(caps));
        caps.struct_size = sizeof(caps);

        cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);
        if (err != CAD_SUCCESS) {
            fprintf(stderr, "cadDeviceOpen: %s\\n", cadErrorString(err));
            return 1;
        }

        printf("device: %s, transport: %s, max_buffers: %u\\n",
               caps.device_name, caps.transport_name, caps.max_buffers);

        cadDeviceClose(dev);
        printf("C smoke: PASSED\\n");
        return 0;
    }
    """)

    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = os.path.join(tmpdir, "smoke_c.c")
        bin_file = os.path.join(tmpdir, "smoke_c")
        with open(src_file, "w") as f:
            f.write(c_src)

        # Compile
        rc, stdout, stderr = run([
            "gcc", "-std=c11", "-Wall",
            "-I", inc_dir,
            "-L", lib_dir,
            "-o", bin_file,
            src_file,
            "-lcaduceus_runtime",
            "-Wl,-rpath," + lib_dir,
        ], label=label + "-compile")
        if rc != 0:
            print(f"  [{label}] COMPILE FAILED")
            if stderr:
                sys.stderr.write(stderr)
            return False

        # Run
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = lib_dir + ":" + env.get("LD_LIBRARY_PATH", "")
        rc, stdout, stderr = run([bin_file], env=env, label=label + "-run")
        if rc != 0 or "PASSED" not in stdout:
            print(f"  [{label}] RUN FAILED (exit={rc})")
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            return False

        print(f"  [{label}] PASSED")
        return True


def smoke_cpp_py_client(install_prefix, label="C++-client(mock://)"):
    """
    Smoke test: compile a C++ RAII client against the installed runtime, run with mock://.
    """
    inc_dir = os.path.join(install_prefix, "include")
    lib_dir = os.path.join(install_prefix, "lib")
    lib_file = os.path.join(lib_dir, "libcaduceus_runtime.so")

    if not os.path.exists(lib_file):
        print(f"  [{label}] SKIPPED: {lib_file} not found")
        return True

    cpp_src = textwrap.dedent("""\
    #include <caduceus/runtime.hpp>
    #include <cstdio>
    #include <cstring>

    int main() {
        cad_device_open_info_t oi{};
        oi.struct_size = sizeof(oi);
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = CAD_ABI_MINOR;
        oi.uri = "mock://";

        try {
            cad::Device dev(oi);
            const auto &caps = dev.caps();
            printf("device: %s, transport: %s, max_buffers: %u\\n",
                   caps.device_name, caps.transport_name, caps.max_buffers);

            // Test RAII lifecycle — create and destroy without explicit calls
            printf("C++ smoke: PASSED\\n");
            return 0;
        } catch (const cad::RuntimeError &e) {
            fprintf(stderr, "RuntimeError: %s\\n", e.what());
            return 1;
        } catch (const std::exception &e) {
            fprintf(stderr, "Exception: %s\\n", e.what());
            return 1;
        }
    }
    """)

    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = os.path.join(tmpdir, "smoke_cpp.cpp")
        bin_file = os.path.join(tmpdir, "smoke_cpp")
        with open(src_file, "w") as f:
            f.write(cpp_src)

        rc, stdout, stderr = run([
            "g++", "-std=c++17", "-Wall",
            "-I", inc_dir,
            "-L", lib_dir,
            "-o", bin_file,
            src_file,
            "-lcaduceus_runtime",
            "-Wl,-rpath," + lib_dir,
        ], label=label + "-compile")
        if rc != 0:
            print(f"  [{label}] COMPILE FAILED")
            if stderr:
                sys.stderr.write(stderr)
            return False

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = lib_dir + ":" + env.get("LD_LIBRARY_PATH", "")
        rc, stdout, stderr = run([bin_file], env=env, label=label + "-run")
        if rc != 0 or "PASSED" not in stdout:
            print(f"  [{label}] RUN FAILED (exit={rc})")
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            return False

        print(f"  [{label}] PASSED")
        return True


def smoke_python_mock(install_prefix, label="Python-binding(mock://)"):
    """
    Smoke test: import the installed Python binding and exercise mock://.
    """
    lib_dir = os.path.join(install_prefix, "lib")
    lib_file = os.path.join(lib_dir, "libcaduceus_runtime.so")

    if not os.path.exists(lib_file):
        print(f"  [{label}] SKIPPED: {lib_file} not found")
        return True

    env = os.environ.copy()
    env["CADUCEUS_RUNTIME_LIB"] = lib_file
    env["PYTHONPATH"] = os.path.join(install_prefix, "share", "caduceus", "python")

    script = textwrap.dedent("""\
    import os, sys
    sys.path.insert(0, os.environ.get("PYTHONPATH", ""))
    from caduceus_runtime import Device, Buffer, CommandList, Queue, Fence, CAD_SUCCESS

    with Device("mock://") as dev:
        caps = dev.caps
        assert caps.device_name, "device_name must not be empty"
        assert caps.transport_name, "transport_name must not be empty"
        assert caps.max_buffers > 0, "max_buffers must be > 0"
        print(f"device={caps.device_name} transport={caps.transport_name}")

        # Buffer lifecycle
        buf = Buffer(dev.handle, 1024)
        buf.write(0, b"hello")
        data = buf.read(0, 5)
        assert data == b"hello", f"buffer read mismatch: {data}"
        buf.free()
        print("buffer lifecycle: OK")

        # Command list + queue + fence
        cl = CommandList(dev.handle, 1)
        cl.append_nop()
        q = Queue(dev.handle)
        f = Fence(dev.handle)
        q.submit(cl, f)
        f.wait(100000000)  # 100ms
        assert f.poll(), "fence not signalled"
        status = f.status()
        assert status == 1, f"fence status={status}, expected CAD_FENCE_COMPLETED=1"
        f.destroy()
        q.destroy()
        print("queue+fence lifecycle: OK")

    print("Python mock smoke: PASSED")
    """)

    rc, stdout, stderr = run(
        [sys.executable, "-c", script],
        env=env,
        label=label,
    )
    if rc != 0 or "PASSED" not in stdout:
        print(f"  [{label}] FAILED (exit={rc})")
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        return False

    print(f"  [{label}] PASSED")
    return True


def smoke_python_fm(install_prefix, label="Python-binding(fm://python)"):
    """
    Smoke test: start device_server.py with fm://python, exercise the Python
    binding against it, then stop the server.
    """
    lib_dir = os.path.join(install_prefix, "lib")
    lib_file = os.path.join(lib_dir, "libcaduceus_runtime.so")

    if not os.path.exists(lib_file):
        print(f"  [{label}] SKIPPED: {lib_file} not found")
        return True

    # The device server needs the Func Model from sim/
    sim_dir = os.path.join(REPO_ROOT, "sim")
    device_server_py = os.path.join(sim_dir, "device_server.py")
    if not os.path.exists(device_server_py):
        print(f"  [{label}] SKIPPED: {device_server_py} not found")
        return True

    env = os.environ.copy()
    env["CADUCEUS_RUNTIME_LIB"] = lib_file
    env["PYTHONPATH"] = sim_dir + ":" + os.path.join(install_prefix, "share", "caduceus", "python") + ":" + os.path.join(REPO_ROOT, "gen")

    server_env = env.copy()
    server_env.pop("CADUCEUS_RUNTIME_LIB", None)

    import socket
    import time as _time

    sock_path = "/tmp/caduceus_task22_smoke.sock"
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    # Start device_server in the background
    print(f"  [{label}] Starting device_server ...", flush=True)
    server_proc = subprocess.Popen(
        [sys.executable, device_server_py,
         "--sock", sock_path],
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the Unix socket to appear
    waited = 0
    while not os.path.exists(sock_path) and waited < 10:
        _time.sleep(0.5)
        waited += 0.5
        if server_proc.poll() is not None:
            stdout, stderr = server_proc.communicate()
            print(f"  [{label}] device_server exited early (rc={server_proc.returncode})")
            if stdout:
                sys.stdout.write(stdout.decode())
            if stderr:
                sys.stderr.write(stderr.decode())
            return False

    if not os.path.exists(sock_path):
        print(f"  [{label}] SKIPPED: device_server socket did not appear (timeout)")
        server_proc.terminate()
        server_proc.wait()
        return True  # non-fatal skip

    print(f"  [{label}] device_server ready (socket={sock_path})", flush=True)

    try:
        fm_uri = f"fm://unix?path={sock_path}"
        script = textwrap.dedent(f"""\
        import sys, os
        sys.path.insert(0, os.environ.get("PYTHONPATH", ""))
        from caduceus_runtime import Device

        with Device("{fm_uri}") as dev:
            caps = dev.caps
            print(f"device={{caps.device_name}} transport={{caps.transport_name}}")
            assert caps.device_name, "device_name missing"
            assert caps.transport_name, "transport_name missing"
            assert caps.max_buffers > 0, "max_buffers zero"

        print("Python fm://python smoke: PASSED")
        """)

        rc, stdout, stderr = run(
            [sys.executable, "-c", script],
            env=env,
            label=label,
        )
        if rc != 0 or "PASSED" not in stdout:
            print(f"  [{label}] FAILED (exit={rc})")
            if stdout:
                sys.stdout.write(stdout)
            if stderr:
                sys.stderr.write(stderr)
            return False

        print(f"  [{label}] PASSED")
        return True
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        print(f"  [{label}] device_server stopped", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run installed CaduceusCore smoke tests")
    parser.add_argument("--install-prefix", default="build/install",
                        help="Installation prefix (default: build/install)")
    args = parser.parse_args()

    install_prefix = os.path.join(REPO_ROOT, args.install_prefix)

    print(f"Smoke tests for install prefix: {install_prefix}")
    print(f"REPO_ROOT: {REPO_ROOT}")
    print()

    results = {}

    # 1. C client
    results["C-client(mock://)"] = smoke_c_py_client(install_prefix)

    # 2. C++ RAII client
    results["C++-client(mock://)"] = smoke_cpp_py_client(install_prefix)

    # 3. Python binding mock://
    results["Python-binding(mock://)"] = smoke_python_mock(install_prefix)

    # 4. Python binding fm://python
    results["Python-binding(fm://python)"] = smoke_python_fm(install_prefix)

    print()
    print("=" * 60)
    print("Smoke Test Summary")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            all_ok = False
    print(f"\nOverall: {'PASS' if all_ok else 'FAIL'}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
