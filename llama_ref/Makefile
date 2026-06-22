CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra

LLAMA_ROOT ?= llama.cpp
BUILD_DIR ?= $(LLAMA_ROOT)/build
BIN_DIR ?= $(BUILD_DIR)/bin

INCLUDES = \
    -I$(LLAMA_ROOT)/include \
    -I$(LLAMA_ROOT)/common \
    -I$(LLAMA_ROOT)/ggml/include

LIBS = \
    -L$(BIN_DIR) \
    -lllama -lllama-common -lggml -lggml-base -lggml-cpu \
    -Wl,-rpath,'$$ORIGIN/$(BIN_DIR)'

TARGET = dump_hidden_states

.PHONY: all clean

all: $(TARGET)

$(TARGET): dump_hidden_states.cpp
	$(CXX) $(CXXFLAGS) $(INCLUDES) $< -o $@ $(LIBS)

clean:
	rm -f $(TARGET)
