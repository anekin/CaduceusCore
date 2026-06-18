# CaduceusCore NPU backend for ggml
# Phase 1: skeleton — logs graph ops, routes to Python model

ggml_add_backend_library(ggml-npu
    ggml-npu.cpp
)

target_include_directories(ggml-npu PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/ggml/include   # ggml-backend.h, ggml.h
    ${CMAKE_SOURCE_DIR}/ggml/src       # ggml-backend-impl.h, ggml-impl.h
)

target_compile_features(ggml-npu PRIVATE cxx_std_17)
