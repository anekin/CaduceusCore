# Makefile for Spike MMIO plugin
#
# Builds npu_mmio_plugin.so from npu_mmio_plugin.cc using the Spike
# source headers directly (no Spike core modifications).

CXX      ?= g++
CXXFLAGS  = -std=c++17 -fPIC -O2 -Wall
INCLUDES  = -I../riscv

PLUGIN    = npu_mmio_plugin.so
SRC       = npu_mmio_plugin.cc

.PHONY: all clean

all: $(PLUGIN)

$(PLUGIN): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCLUDES) -shared -o $@ $<

clean:
	rm -f $(PLUGIN)
