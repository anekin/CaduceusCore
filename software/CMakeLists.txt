cmake_minimum_required(VERSION 3.14)
project(caduceus-runtime LANGUAGES C CXX)

option(CADUCEUS_BUILD_TESTS "Build runtime ABI tests" ON)

# ── Header-only library (ABI contract) ─────────────────────────────

add_library(caduceus_runtime_headers INTERFACE)
target_include_directories(caduceus_runtime_headers INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# ── Runtime stubs (minimal implementations for ABI testing) ────────

add_library(caduceus_runtime_stubs STATIC
    src/runtime_stubs.c
)
target_include_directories(caduceus_runtime_stubs PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)
target_compile_features(caduceus_runtime_stubs PUBLIC c_std_11)
target_link_libraries(caduceus_runtime_stubs PUBLIC caduceus_runtime_headers)

# ── Tests ──────────────────────────────────────────────────────────

if(CADUCEUS_BUILD_TESTS)
    enable_testing()

    # Happy-path ABI tests
    add_executable(test_runtime_abi
        tests/test_runtime_abi.cpp
    )
    target_link_libraries(test_runtime_abi PRIVATE caduceus_runtime_stubs)
    target_compile_features(test_runtime_abi PRIVATE cxx_std_17)
    add_test(NAME runtime_abi COMMAND test_runtime_abi)

    # Negative-path ABI tests
    add_executable(test_runtime_abi_negative
        tests/test_runtime_abi_negative.cpp
    )
    target_link_libraries(test_runtime_abi_negative PRIVATE caduceus_runtime_stubs)
    target_compile_features(test_runtime_abi_negative PRIVATE cxx_std_17)
    add_test(NAME runtime_abi_negative COMMAND test_runtime_abi_negative)
endif()
