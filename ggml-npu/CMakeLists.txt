# CaduceusCore NPU backend for ggml — Command IR lowering + CPU fallback
#
# This file is designed to be included from llama.cpp's build system
# at third_party/llama.cpp/ggml/src/ggml-npu/ (placed by scripts/fetch_llama_cpp.py).
#
# The backend uses:
#   - CaduceusCore Host Runtime C API for device/buffer/queue/fence ops
#   - CaduceusCore Command IR for descriptor lowering and blob validation
#
# Build prerequisites:
#   - cmake -S software -B build/software (builds libcaduceus_runtime.so)
#   - cmake -S third_party/llama.cpp -B build/llama -DGGML_NPU=ON -DGGML_BACKEND_DL=ON

ggml_add_backend_library(ggml-npu
    ggml-npu.cpp
)

# ── Test executable: single MMUL via fm:// ──────────────────────────
#
# Builds a one-node ggml graph (F32 activation x Q4_0 weight) and
# exercises the NPU backend's real fm:// submission path.  Verifies
# that the expected NPU execution log lines appear in stderr.
add_executable(test_npu_single_mmul
    test_npu_single_mmul.cpp
)
target_link_libraries(test_npu_single_mmul PRIVATE ggml)
target_include_directories(test_npu_single_mmul PRIVATE
    ${CMAKE_SOURCE_DIR}/ggml/include
    ${CMAKE_SOURCE_DIR}/ggml/src
)

# Include path computation — relative to third_party/llama.cpp/ggml/src/ggml-npu/
set(_SW_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../software/include")
set(_SW_SRC     "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../software/src")
set(_SW_COMPILER "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../software/compiler")
set(_SW_BUILD   "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../build/software")
set(_GEN_DIR    "${CMAKE_CURRENT_SOURCE_DIR}/../../../../../gen")
set(_FB_INCLUDE "/tmp/flatbuffers-25.2.10/include")

target_include_directories(ggml-npu PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/ggml/include   # ggml-backend.h, ggml.h
    ${CMAKE_SOURCE_DIR}/ggml/src       # ggml-backend-impl.h, ggml-impl.h
    ${_SW_INCLUDE}                     # caduceus/runtime.h
    ${_SW_COMPILER}                    # command_ir.h, command_ir_internal.h
    ${_GEN_DIR}                        # npu_abi.h (needed by lower.c)
)

target_compile_features(ggml-npu PRIVATE cxx_std_17)

# ── Compile Command IR sources into ggml-npu ───────────────────────
#
# The command IR (ir.c, lower.c, blob.c) is compiled directly into
# the ggml-npu shared library so no separate libcaduceus_command_ir.so
# is needed at DL load time.

target_sources(ggml-npu PRIVATE
    ${_SW_COMPILER}/ir.c
    ${_SW_COMPILER}/lower.c
    ${_SW_COMPILER}/blob.c
)

# ── Link Host Runtime ────────────────────────────────────────────────
#
# Prefer the pre-built shared library. If not found, build from source
# as a static link (requires FlatBuffers for fm:// support).

find_library(CADUCEUS_RUNTIME_LIB caduceus_runtime
    PATHS ${_SW_BUILD}
    NO_DEFAULT_PATH
)

if(CADUCEUS_RUNTIME_LIB)
    message(STATUS "ggml-npu: linking pre-built libcaduceus_runtime.so")
    target_link_libraries(ggml-npu PRIVATE ${CADUCEUS_RUNTIME_LIB})
else()
    message(STATUS "ggml-npu: building Host Runtime from source (static)")
    # Build runtime core + mock transport from source
    target_sources(ggml-npu PRIVATE
        ${_SW_SRC}/runtime_core.c
        ${_SW_SRC}/transport_mock.c
    )
    target_include_directories(ggml-npu PRIVATE
        ${_SW_SRC}   # runtime_core.h
    )
    # Try to add fm:// transport if FlatBuffers is available
    if(EXISTS "${_FB_INCLUDE}/flatbuffers/flatbuffers.h")
        target_sources(ggml-npu PRIVATE ${_SW_SRC}/transport_fm.cpp)
        target_include_directories(ggml-npu PRIVATE
            ${_FB_INCLUDE}
            ${_GEN_DIR}/device_protocol
        )
        target_compile_definitions(ggml-npu PRIVATE CADUCEUS_HAS_FM_TRANSPORT=1)
        message(STATUS "ggml-npu: fm:// transport enabled (FlatBuffers)")
    else()
        message(STATUS "ggml-npu: fm:// transport NOT available (FlatBuffers not found)")
    endif()
endif()
