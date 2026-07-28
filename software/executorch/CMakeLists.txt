# CaduceusCore ExecuTorch Delegate — Build Integration
#
# Builds the runtime backend library that wraps the shared Host Runtime
# for ExecuTorch delegate execution.  Reuses:
#   - Todo 11 command IR (caduceus_command_ir)
#   - Todo 3/7 Host Runtime (caduceus_runtime_core)
#
# Pin: ExecuTorch v1.2.0 (see deps/executorch.lock)

# ── ExecuTorch NPU backend static library ────────────────────────────

add_library(caduceus_et_backend STATIC
    runtime/caduceus_npu_backend.cpp
)
target_include_directories(caduceus_et_backend PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/runtime
    ${CMAKE_CURRENT_SOURCE_DIR}/../include
    ${CMAKE_CURRENT_SOURCE_DIR}/../compiler
    ${CMAKE_CURRENT_SOURCE_DIR}/../../gen
)
target_compile_features(caduceus_et_backend PUBLIC cxx_std_17)
target_link_libraries(caduceus_et_backend PUBLIC
    caduceus_runtime_core
    caduceus_command_ir
)

# ── Tests ────────────────────────────────────────────────────────────

if(CADUCEUS_BUILD_TESTS)
    # C++ runtime backend tests
    add_executable(test_executorch_backend
        ${CMAKE_CURRENT_SOURCE_DIR}/../tests/test_executorch_backend.cpp
    )
    target_include_directories(test_executorch_backend PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/runtime
        ${CMAKE_CURRENT_SOURCE_DIR}/../include
        ${CMAKE_CURRENT_SOURCE_DIR}/../compiler
        ${CMAKE_CURRENT_SOURCE_DIR}/../../gen
        ${CMAKE_CURRENT_SOURCE_DIR}/../tests    # for doctest.h
    )
    target_compile_features(test_executorch_backend PRIVATE cxx_std_17)
    target_compile_options(test_executorch_backend PRIVATE -Wall)
    target_link_libraries(test_executorch_backend PRIVATE
        caduceus_et_backend
        caduceus_runtime_core
        caduceus_command_ir
    )
    add_test(NAME executorch_backend COMMAND test_executorch_backend)
endif()
