cmake_minimum_required(VERSION 3.14)
project(caduceus-runtime LANGUAGES C CXX)

option(CADUCEUS_BUILD_TESTS "Build runtime tests" ON)
option(CADUCEUS_BUILD_SHARED "Build shared library for Python bindings" ON)

# ── Header-only library (ABI contract) ─────────────────────────────

add_library(caduceus_runtime_headers INTERFACE)
target_include_directories(caduceus_runtime_headers INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# ── Runtime stubs (minimal, for existing ABI tests) ────────────────

add_library(caduceus_runtime_stubs STATIC
    src/runtime_stubs.c
)
target_include_directories(caduceus_runtime_stubs PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(caduceus_runtime_stubs PUBLIC c_std_11)
target_link_libraries(caduceus_runtime_stubs PUBLIC caduceus_runtime_headers)

# ── Real runtime core + mock transport ──────────────────────────────

set(RUNTIME_CORE_SOURCES
    src/runtime_core.c
    src/transport_mock.c
    src/transport_fm.cpp
    src/transport_fpga.cpp
    src/transport_rtl.cpp
)

# Static library (for C/C++ tests)
add_library(caduceus_runtime_core STATIC
    ${RUNTIME_CORE_SOURCES}
)
target_include_directories(caduceus_runtime_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src        # for runtime_core.h
    ${CMAKE_CURRENT_SOURCE_DIR}/../gen/device_protocol
    /tmp/flatbuffers-25.2.10/include
)
target_compile_features(caduceus_runtime_core PUBLIC c_std_11)
target_link_libraries(caduceus_runtime_core PUBLIC caduceus_runtime_headers)

# Shared library (for Python ctypes bindings)
if(CADUCEUS_BUILD_SHARED)
    add_library(caduceus_runtime_shared SHARED
        ${RUNTIME_CORE_SOURCES}
    )
    target_include_directories(caduceus_runtime_shared PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen/device_protocol
        /tmp/flatbuffers-25.2.10/include
    )
    target_compile_features(caduceus_runtime_shared PUBLIC c_std_11)
    target_link_libraries(caduceus_runtime_shared PUBLIC caduceus_runtime_headers)
    set_target_properties(caduceus_runtime_shared PROPERTIES OUTPUT_NAME "caduceus_runtime")

    # Create development symlink so Python ctypes binding can find the shared
    # library via its fallback path (build/software/libcaduceus_runtime.so).
    # Recreated on every build to stay in sync.
    add_custom_command(TARGET caduceus_runtime_shared POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_SOURCE_DIR}/build"
        COMMAND ${CMAKE_COMMAND} -E create_symlink
            "../../build/software/libcaduceus_runtime.so"
            "${CMAKE_CURRENT_SOURCE_DIR}/build/libcaduceus_runtime.so"
        COMMENT "Creating symlink software/build/libcaduceus_runtime.so for Python ctypes"
    )
endif()

# ── Production command IR / lowering / blob codec ──────────────────

set(COMPILER_SOURCES
    compiler/ir.c
    compiler/lower.c
    compiler/blob.c
)

add_library(caduceus_command_ir STATIC
    ${COMPILER_SOURCES}
)
target_include_directories(caduceus_command_ir PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/compiler
    ${CMAKE_CURRENT_SOURCE_DIR}/../gen
)
target_compile_features(caduceus_command_ir PUBLIC c_std_11)

if(CADUCEUS_BUILD_SHARED)
    add_library(caduceus_command_ir_shared SHARED
        ${COMPILER_SOURCES}
    )
    target_include_directories(caduceus_command_ir_shared PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_compile_features(caduceus_command_ir_shared PUBLIC c_std_11)
    set_target_properties(caduceus_command_ir_shared PROPERTIES OUTPUT_NAME "caduceus_command_ir")
endif()

# ── Tests ──────────────────────────────────────────────────────────

if(CADUCEUS_BUILD_TESTS)
    enable_testing()

    # --- Existing ABI tests (against stubs) ---

    add_executable(test_runtime_abi
        tests/test_runtime_abi.cpp
    )
    target_link_libraries(test_runtime_abi PRIVATE caduceus_runtime_stubs)
    target_compile_features(test_runtime_abi PRIVATE cxx_std_17)
    add_test(NAME runtime_abi COMMAND test_runtime_abi)

    add_executable(test_runtime_abi_negative
        tests/test_runtime_abi_negative.cpp
    )
    target_link_libraries(test_runtime_abi_negative PRIVATE caduceus_runtime_stubs)
    target_compile_features(test_runtime_abi_negative PRIVATE cxx_std_17)
    add_test(NAME runtime_abi_negative COMMAND test_runtime_abi_negative)
    set_tests_properties(runtime_abi_negative PROPERTIES
        ENVIRONMENT "ASAN_OPTIONS=halt_on_error=0")

    add_executable(test_abi_layout
        tests/test_abi_layout.cpp
    )
    target_include_directories(test_abi_layout PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_compile_features(test_abi_layout PRIVATE cxx_std_17)
    add_test(NAME abi_layout COMMAND test_abi_layout)

    # --- NEW: C conformance tests (against real runtime + mock) ---

    add_executable(test_runtime_conformance
        tests/test_runtime_conformance.c
    )
    target_link_libraries(test_runtime_conformance PRIVATE caduceus_runtime_core)
    target_compile_features(test_runtime_conformance PRIVATE c_std_11)
    # GCC __attribute__((constructor)) is fine with -Wall
    target_compile_options(test_runtime_conformance PRIVATE -Wall -Wextra)
    add_test(NAME runtime_conformance COMMAND test_runtime_conformance)

    # --- ExecuteBlob conformance tests ---

    add_executable(test_execute_blob_conformance
        tests/test_execute_blob_conformance.c
    )
    target_link_libraries(test_execute_blob_conformance PRIVATE caduceus_runtime_core)
    target_compile_features(test_execute_blob_conformance PRIVATE c_std_11)
    target_compile_options(test_execute_blob_conformance PRIVATE -Wall -Wextra)
    add_test(NAME execute_blob_conformance COMMAND test_execute_blob_conformance)

    # --- NEW: C fault/negative tests ---

    add_executable(test_runtime_faults
        tests/test_runtime_faults.c
    )
    target_link_libraries(test_runtime_faults PRIVATE caduceus_runtime_core)
    target_compile_features(test_runtime_faults PRIVATE c_std_11)
    target_compile_options(test_runtime_faults PRIVATE -Wall -Wextra)
    add_test(NAME runtime_faults COMMAND test_runtime_faults)
    # Intentional use-after-free tests (buffer_use_after_free, etc.)
    set_tests_properties(runtime_faults PROPERTIES
        ENVIRONMENT "ASAN_OPTIONS=halt_on_error=0")

    # --- W2-T8: fpga:// explicitly unsupported (no silent mock fallback) ---

    add_executable(test_unsupported_uri
        tests/test_unsupported_uri.c
    )
    target_link_libraries(test_unsupported_uri PRIVATE caduceus_runtime_core)
    target_compile_features(test_unsupported_uri PRIVATE c_std_11)
    target_compile_options(test_unsupported_uri PRIVATE -Wall -Wextra)
    add_test(NAME unsupported_uri COMMAND test_unsupported_uri)

    # --- I-009: fm://spike must be accepted (not UNSUPPORTED) ---

    add_executable(test_spike_uri
        tests/test_spike_uri.c
    )
    target_link_libraries(test_spike_uri PRIVATE caduceus_runtime_core)
    target_compile_features(test_spike_uri PRIVATE c_std_11)
    target_compile_options(test_spike_uri PRIVATE -Wall -Wextra)
    add_test(NAME spike_uri COMMAND test_spike_uri)

    # --- W2-T2: Command data serialization tests ---

    add_executable(test_cmd_serialization
        tests/test_cmd_serialization.c
    )
    target_link_libraries(test_cmd_serialization PRIVATE caduceus_runtime_core)
    target_compile_features(test_cmd_serialization PRIVATE c_std_11)
    target_compile_options(test_cmd_serialization PRIVATE -Wall -Wextra)
    add_test(NAME cmd_serialization COMMAND test_cmd_serialization)

    add_executable(test_cmd_serialization_negative
        tests/test_cmd_serialization_negative.c
    )
    target_link_libraries(test_cmd_serialization_negative PRIVATE caduceus_runtime_core)
    target_compile_features(test_cmd_serialization_negative PRIVATE c_std_11)
    target_compile_options(test_cmd_serialization_negative PRIVATE -Wall -Wextra)
    add_test(NAME cmd_serialization_negative COMMAND test_cmd_serialization_negative)
    # Intentional freed-buffer-submit tests
    set_tests_properties(cmd_serialization_negative PROPERTIES
        ENVIRONMENT "ASAN_OPTIONS=halt_on_error=0")

    # --- W2-T9: FM transport cmd_blob forwarding test ---

    add_executable(test_fm_transport_blob
        tests/test_fm_transport_blob.cpp
    )
    target_include_directories(test_fm_transport_blob PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen/device_protocol
        /tmp/flatbuffers-25.2.10/include
    )
    target_link_libraries(test_fm_transport_blob PRIVATE caduceus_runtime_core)
    target_compile_features(test_fm_transport_blob PRIVATE cxx_std_17)
    target_compile_options(test_fm_transport_blob PRIVATE -Wall)
    add_test(NAME fm_transport COMMAND test_fm_transport_blob)

    # --- ggml Runtime fault propagation tests ---

    add_executable(test_ggml_runtime_faults
        tests/test_ggml_runtime_faults.c
    )
    target_link_libraries(test_ggml_runtime_faults PRIVATE caduceus_runtime_core)
    target_compile_features(test_ggml_runtime_faults PRIVATE c_std_11)
    target_compile_options(test_ggml_runtime_faults PRIVATE -Wall -Wextra)
    add_test(NAME ggml_runtime_faults COMMAND test_ggml_runtime_faults)
    # Intentional use-after-free tests (queue_create_on_closed_device)
    set_tests_properties(ggml_runtime_faults PROPERTIES
        ENVIRONMENT "ASAN_OPTIONS=halt_on_error=0")

    # --- ggml op support negative tests (command IR validation) ---

    add_executable(test_ggml_op_support_negative
        tests/test_ggml_op_support_negative.cpp
    )
    target_include_directories(test_ggml_op_support_negative PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
        ${CMAKE_CURRENT_SOURCE_DIR}/tests
    )
    target_link_libraries(test_ggml_op_support_negative PRIVATE caduceus_command_ir)
    target_compile_features(test_ggml_op_support_negative PRIVATE cxx_std_17)
    target_compile_options(test_ggml_op_support_negative PRIVATE -Wall)
    add_test(NAME ggml_op_support_negative COMMAND test_ggml_op_support_negative)

    # --- NEW: C++ conformance tests (doctest) ---

    add_executable(test_runtime_conformance_cpp
        tests/test_runtime_conformance.cpp
    )
    target_include_directories(test_runtime_conformance_cpp PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/tests   # for doctest.h
    )
    target_link_libraries(test_runtime_conformance_cpp PRIVATE caduceus_runtime_core)
    target_compile_features(test_runtime_conformance_cpp PRIVATE cxx_std_17)
    target_compile_options(test_runtime_conformance_cpp PRIVATE -Wall)
    add_test(NAME runtime_conformance_cpp COMMAND test_runtime_conformance_cpp)

    # --- NEW: Command IR lowering tests ---

    add_executable(test_command_lowering
        tests/test_command_lowering.cpp
    )
    target_include_directories(test_command_lowering PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_command_lowering PRIVATE caduceus_command_ir)
    target_compile_features(test_command_lowering PRIVATE cxx_std_17)
    target_compile_options(test_command_lowering PRIVATE -Wall)
    add_test(NAME command_lowering COMMAND test_command_lowering)

    add_executable(test_command_lowering_negative
        tests/test_command_lowering_negative.cpp
    )
    target_include_directories(test_command_lowering_negative PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_command_lowering_negative PRIVATE caduceus_command_ir)
    target_compile_features(test_command_lowering_negative PRIVATE cxx_std_17)
    target_compile_options(test_command_lowering_negative PRIVATE -Wall)
    add_test(NAME command_lowering_negative COMMAND test_command_lowering_negative)

    # --- FPGA transport conformance tests (fake fixtures) ---

    add_executable(test_fpga_transport
        tests/test_fpga_transport.cpp
    )
    target_link_libraries(test_fpga_transport PRIVATE caduceus_runtime_core)
    target_compile_features(test_fpga_transport PRIVATE cxx_std_17)
    target_compile_options(test_fpga_transport PRIVATE -Wall)
    add_test(NAME fpga_transport_conformance COMMAND test_fpga_transport)

    add_executable(test_fpga_transport_negative
        tests/test_fpga_transport_negative.cpp
    )
    target_link_libraries(test_fpga_transport_negative PRIVATE caduceus_runtime_core)
    target_compile_features(test_fpga_transport_negative PRIVATE cxx_std_17)
    target_compile_options(test_fpga_transport_negative PRIVATE -Wall)
    add_test(NAME fpga_transport_negative COMMAND test_fpga_transport_negative)

    # --- RTL transport conformance tests (fake fixture) ---

    add_executable(test_rtl_transport
        tests/test_rtl_transport.cpp
    )
    target_link_libraries(test_rtl_transport PRIVATE caduceus_runtime_core)
    target_compile_features(test_rtl_transport PRIVATE cxx_std_17)
    target_compile_options(test_rtl_transport PRIVATE -Wall)
    add_test(NAME rtl_transport_conformance COMMAND test_rtl_transport)

    add_executable(test_rtl_transport_negative
        tests/test_rtl_transport_negative.cpp
    )
    target_link_libraries(test_rtl_transport_negative PRIVATE caduceus_runtime_core)
    target_compile_features(test_rtl_transport_negative PRIVATE cxx_std_17)
    target_compile_options(test_rtl_transport_negative PRIVATE -Wall)
    add_test(NAME rtl_transport_negative COMMAND test_rtl_transport_negative)

    # --- W3-T1: End-to-end FM submit via Host Runtime ---

    add_executable(test_fm_e2e_submit
        tests/test_fm_e2e_submit.c
    )
    target_include_directories(test_fm_e2e_submit PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_fm_e2e_submit PRIVATE
        caduceus_runtime_core
        caduceus_command_ir
    )
    target_compile_features(test_fm_e2e_submit PRIVATE c_std_11)
    target_compile_options(test_fm_e2e_submit PRIVATE -Wall -Wextra)

    # W3-T8a: Broken socket → FM transport error context in stderr
    add_test(NAME fm_e2e_broken_socket COMMAND
        bash -c "$<TARGET_FILE:test_fm_e2e_submit> fm://unix?path=/tmp/nonexistent_caduceus.sock 2>&1 | grep -qE 'FM transport|transport'"
    )

    # --- W3-T8: FM transport error context (broken socket) ---

    add_executable(test_fm_transport_error
        tests/test_fm_transport_error.c
    )
    target_link_libraries(test_fm_transport_error PRIVATE caduceus_runtime_core)
    target_compile_features(test_fm_transport_error PRIVATE c_std_11)
    target_compile_options(test_fm_transport_error PRIVATE -Wall -Wextra)
    add_test(NAME fm_transport_error COMMAND test_fm_transport_error)

    # --- W3-T3: Single non-NOP MMUL end-to-end hard gate (happy path) ---

    add_executable(test_fm_e2e_mmul
        tests/test_fm_e2e_mmul.c
    )
    target_include_directories(test_fm_e2e_mmul PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_fm_e2e_mmul PRIVATE
        caduceus_runtime_core
        caduceus_command_ir
    )
    target_compile_features(test_fm_e2e_mmul PRIVATE c_std_11)
    target_compile_options(test_fm_e2e_mmul PRIVATE -Wall -Wextra)

    # --- W3-T4: MMUL negative-path tests (--negative flag) -----------
    # Exercises corrupted weight, zero-dimension, fence timeout, and
    # device reset recovery through the real fm:// transport.

    add_test(NAME mmul_negative COMMAND
        bash "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_mmul_negative_test.sh"
        "$<TARGET_FILE:test_fm_e2e_mmul>"
    )

    # --- W3-T3: Single non-NOP MMUL end-to-end hard gate (happy path) ---
    add_test(NAME fm_e2e_mmul COMMAND
        bash "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_mmul_happy_test.sh"
        "$<TARGET_FILE:test_fm_e2e_mmul>"
    )

    # --- W4-T1: 4-op chain end-to-end hard gate (MMUL→SFU→VADD→DMA_COPY) ---

    add_executable(test_fm_e2e_chain
        tests/test_fm_e2e_chain.c
    )
    target_include_directories(test_fm_e2e_chain PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_fm_e2e_chain PRIVATE
        caduceus_runtime_core
        caduceus_command_ir
    )
    target_compile_features(test_fm_e2e_chain PRIVATE c_std_11)
    target_compile_options(test_fm_e2e_chain PRIVATE -Wall -Wextra)
    add_test(NAME test_fm_e2e_chain COMMAND
        bash "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_chain_test.sh"
        "$<TARGET_FILE:test_fm_e2e_chain>"
    )

    # --- W3-T5: Buffer lifecycle edge-case tests (mock + FM-compatible) ---

    add_executable(test_buffer_lifecycle_fm
        tests/test_buffer_lifecycle_fm.c
    )
    target_link_libraries(test_buffer_lifecycle_fm PRIVATE caduceus_runtime_core)
    target_compile_features(test_buffer_lifecycle_fm PRIVATE c_std_11)
    target_compile_options(test_buffer_lifecycle_fm PRIVATE -Wall -Wextra)
    # Default: mock:// (runtime-level validation before transport)
    add_test(NAME buffer_edge_cases COMMAND test_buffer_lifecycle_fm mock://)
    # Intentional use-after-free test (test_use_after_free)
    set_tests_properties(buffer_edge_cases PROPERTIES
        ENVIRONMENT "ASAN_OPTIONS=halt_on_error=0")

    # ── ExecuTorch NPU backend tests ───────────────────────────────

    add_subdirectory(executorch)

    # --- W5-T2: Execution stats (FM server returns real op/byte counts) ---

    add_executable(test_execution_stats
        tests/test_execution_stats.c
    )
    target_include_directories(test_execution_stats PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../gen
    )
    target_link_libraries(test_execution_stats PRIVATE
        caduceus_runtime_core
        caduceus_command_ir
    )
    target_compile_features(test_execution_stats PRIVATE c_std_11)
    target_compile_options(test_execution_stats PRIVATE -Wall -Wextra)
    add_test(NAME execution_stats COMMAND
        bash "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/run_execution_stats_test.sh"
        "$<TARGET_FILE:test_execution_stats>"
    )
endif()

# ── Install rules ────────────────────────────────────────────────────

include(GNUInstallDirs)

# Public headers
install(DIRECTORY include/caduceus/
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/caduceus
    FILES_MATCHING PATTERN "*.h" PATTERN "*.hpp"
)

# Compiler headers (public command IR API)
install(FILES
    compiler/command_ir.h
    compiler/command_ir_internal.h
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/caduceus
)

# Static runtime library
install(TARGETS caduceus_runtime_core
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
)

# Shared runtime library (for Python ctypes, dynamic loading)
install(TARGETS caduceus_runtime_shared
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
)

# Static command IR library
install(TARGETS caduceus_command_ir
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
)

# Shared command IR library
if(CADUCEUS_BUILD_SHARED)
    install(TARGETS caduceus_command_ir_shared
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    )
endif()

# Python binding — copy to install prefix for pip/setup.py
install(FILES
    python/caduceus_runtime.py
    DESTINATION ${CMAKE_INSTALL_DATAROOTDIR}/caduceus/python
)
