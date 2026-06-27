// Standard assertion macros for VCS / Questa / Xcelium simulators
// Adapted from OpenTitan prim_assert_standard_macros.svh
// License: Apache-2.0

// ASSERT_I: immediate assertion (glitch-sensitive)
`define ASSERT_I(__name, __prop) \
  __name: assert (__prop) else begin \
    `ASSERT_ERROR(__name) \
  end

// ASSERT_INIT: assertion in initial block (e.g. parameter check)
`define ASSERT_INIT(__name, __prop) \
  initial begin \
    __name: assert (__prop) else begin \
      `ASSERT_ERROR(__name) \
    end \
  end

// ASSERT_INIT_NET: assertion for initial net value
`define ASSERT_INIT_NET(__name, __prop) \
  `ASSERT_INIT(__name, __prop)

// ASSERT_FINAL: assertion in final block
`define ASSERT_FINAL(__name, __prop) \
  final begin \
    __name: assert (__prop) else begin \
      `ASSERT_ERROR(__name) \
    end \
  end

// ASSERT: concurrent assertion (module/interface body item)
`define ASSERT(__name, __prop, __clk = `ASSERT_DEFAULT_CLK, __rst = `ASSERT_DEFAULT_RST) \
  __name: assert property ( \
    @(posedge __clk) disable iff (__rst !== '0) \
    (__prop) \
  ) else begin \
    `ASSERT_ERROR(__name) \
  end

// ASSERT_NEVER: property should NEVER happen
`define ASSERT_NEVER(__name, __prop, __clk = `ASSERT_DEFAULT_CLK, __rst = `ASSERT_DEFAULT_RST) \
  `ASSERT(__name``Never, not (__prop), __clk, __rst)

// ASSERT_KNOWN: signal has known value after reset
`define ASSERT_KNOWN(__name, __sig, __clk = `ASSERT_DEFAULT_CLK, __rst = `ASSERT_DEFAULT_RST) \
  `ASSERT(__name``Known, !$isunknown(__sig), __clk, __rst)

// COVER: coverage property
`define COVER(__name, __prop, __clk = `ASSERT_DEFAULT_CLK, __rst = `ASSERT_DEFAULT_RST) \
  __name: cover property ( \
    @(posedge __clk) disable iff (__rst !== '0) \
    (__prop) \
  )

// ASSUME: concurrent assumption
`define ASSUME(__name, __prop, __clk = `ASSERT_DEFAULT_CLK, __rst = `ASSERT_DEFAULT_RST) \
  __name: assume property ( \
    @(posedge __clk) disable iff (__rst !== '0) \
    (__prop) \
  )

// ASSUME_I: immediate assumption
`define ASSUME_I(__name, __prop) \
  __name: assume (__prop)
