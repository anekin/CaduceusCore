//=============================================================================
// ibex_wrapper — Ibex RISC-V core (RV32IMC) with AXI4 + APB master bridges
// CaduceusCore SoC Phase 3-4 / Task 4
//
// Features:
//   - Instantiates ibex_top with RV32IMC, ICache=off, PMP=off, RV32B=off
//   - Instruction fetch from boot_rom (0x0000_0000, 64KB), 1-cycle reply
//   - Data access via AXI4 master (32-bit) + APB master + internal DMEM
//   - Address decode for data:
//       0x0000_0000~0x0000_FFFF → boot_rom (read-only, write → error)
//       0x0001_0000~0x0001_FFFF → internal 64KB DMEM (stack + .data/.bss)
//       0x2000_0000~0x3FFF_FFFF → AXI4 master (crossbar)
//       0x4000_0000~0x4FFF_FFFF → APB master (only 0x4000_0000~0x4000_6FFF valid)
//       0x8000_0000~0xFFFF_FFFF → AXI4 master (crossbar)
//       otherwise               → BUS_ERROR trap
//
// Instruction fetch protocol (Ibex ↔ boot_rom):
//   Ibex asserts instr_req_o → boot_rom reads word-addr → 1 cycle later
//   instr_rvalid_i=1 with instr_rdata_i=data from ROM.  instr_gnt_i is
//   asserted immediately (same cycle as instr_req_o).
//
// Data protocol (Ibex ↔ adapter FSM):
//   Ibex asserts data_req_o → FSM grants immediately if idle → FSM starts
//   AXI4/APB/DMEM transaction → FSM waits for response → asserts data_rvalid_i.
//   Single-outstanding transactions (Ibex LSU serialises).
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top ibex_wrapper \
//       -f CaduceusCore/rtl/cpu/ibex.flist CaduceusCore/rtl/cpu/ibex_wrapper.v \
//       -o simv_ibex_wrapper
//=============================================================================

`timescale 1ns / 1ps

module ibex_wrapper #(
    parameter int AXI_ADDR_WIDTH = 32,
    parameter int AXI_DATA_WIDTH = 32,
    parameter int AXI_ID_WIDTH   = 4
) (
    input  wire                     clk,
    input  wire                     rst_n,

    // ── Interrupt (from INTC) ──────────────────────────────────────────
    input  wire                     cpu_irq_i,

    // ── AXI4 Master (data memory access, 32-bit) ───────────────────────
    // Write Address
    output wire [AXI_ID_WIDTH-1:0]   m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_awaddr,
    output wire [7:0]                m_axi_awlen,
    output wire [2:0]                m_axi_awsize,
    output wire [1:0]                m_axi_awburst,
    output wire                      m_axi_awvalid,
    input  wire                      m_axi_awready,
    // Write Data
    output wire [AXI_DATA_WIDTH-1:0] m_axi_wdata,
    output wire [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
    output wire                      m_axi_wlast,
    output wire                      m_axi_wvalid,
    input  wire                      m_axi_wready,
    // Write Response
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_bid,
    input  wire [1:0]                m_axi_bresp,
    input  wire                      m_axi_bvalid,
    output wire                      m_axi_bready,
    // Read Address
    output wire [AXI_ID_WIDTH-1:0]   m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_araddr,
    output wire [7:0]                m_axi_arlen,
    output wire [2:0]                m_axi_arsize,
    output wire [1:0]                m_axi_arburst,
    output wire                      m_axi_arvalid,
    input  wire                      m_axi_arready,
    // Read Data
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0] m_axi_rdata,
    input  wire [1:0]                m_axi_rresp,
    input  wire                      m_axi_rlast,
    input  wire                      m_axi_rvalid,
    output wire                      m_axi_rready,

    // ── APB Master (MMIO at 0x4000_0000~0x4000_6FFF) ──────────────────
    output wire [31:0]               apb_paddr,
    output wire                      apb_psel,
    output wire                      apb_penable,
    output wire                      apb_pwrite,
    output wire [31:0]               apb_pwdata,
    input  wire [31:0]               apb_prdata,
    input  wire                      apb_pready,
    input  wire                      apb_pslverr
);

    // =========================================================================
    // Local Parameters
    // =========================================================================
    localparam int BOOT_ROM_SIZE = 16384;        // 64KB / 4B = 16384 words

    // =========================================================================
    // Ibex Signals
    // =========================================================================
    wire                    instr_req;
    wire                    instr_gnt;
    wire                    instr_rvalid;
    wire [31:0]             instr_addr;
    wire [31:0]             instr_rdata;
    wire                    instr_err;

    wire                    data_req;
    wire                    data_gnt;
    wire                    data_rvalid;
    wire                    data_we;
    wire [3:0]              data_be;
    wire [31:0]             data_addr;
    wire [31:0]             data_wdata;
    wire [31:0]             data_rdata;
    wire                    data_err;

    // ── Unused Ibex ports ─────────────────────────────────────────────────
    wire [6:0]  instr_rdata_intg_unused;
    wire [6:0]  data_wdata_intg_unused;
    wire [6:0]  data_rdata_intg_unused;
    wire [14:0] irq_fast_unused;
    wire        alert_minor_unused, alert_major_internal_unused, alert_major_bus_unused;
    wire        core_sleep_unused;
    wire        lockstep_cmp_en_unused;
    wire        data_req_shadow_unused, data_we_shadow_unused;
    wire [3:0]  data_be_shadow_unused;
    wire [31:0] data_addr_shadow_unused, data_wdata_shadow_unused;
    wire [6:0]  data_wdata_intg_shadow_unused;
    wire        instr_req_shadow_unused;
    wire [31:0] instr_addr_shadow_unused;
    wire        double_fault_seen_unused;
    wire [3:0]  lockstep_cmp_en_unused;  // ibex_mubi_t is 4-bit

    // =========================================================================
    // Boot ROM Signals
    // =========================================================================
    wire [13:0]             rom_addr;
    wire [31:0]             rom_instr;
    reg                     instr_req_d1;       // delayed instr_req for rvalid

    // Boot ROM data read port (for data loads from 0x0000_0000)
    wire [13:0]             rom_data_addr;
    wire [31:0]             rom_data_out;
    reg  [31:0]             rom_data_rdata;     // captured ROM data output

    // =========================================================================
    // Internal DMEM (64KB at 0x0001_0000)
    // =========================================================================
    reg  [31:0]             dmem [0:16383];     // 16384 × 32-bit = 64KB
    wire                    dmem_hit;

    assign dmem_hit = (data_addr >= 32'h0001_0000) && (data_addr <= 32'h0001_FFFF);

    // =========================================================================
    // Address Decode (data path)
    // =========================================================================
    wire                    is_boot_rom;
    wire                    is_axi4;
    wire                    is_apb;
    wire                    is_undefined;

    assign is_boot_rom  = (data_addr >= 32'h0000_0000) && (data_addr <= 32'h0000_FFFF);
    assign is_axi4      = ((data_addr >= 32'h2000_0000) && (data_addr <= 32'h3FFF_FFFF)) ||
                          ((data_addr >= 32'h8000_0000) && (data_addr <= 32'hFFFF_FFFF));
    assign is_apb       = (data_addr >= 32'h4000_0000) && (data_addr <= 32'h4FFF_FFFF);
    assign is_undefined = !is_boot_rom && !dmem_hit && !is_axi4 && !is_apb;

    // =========================================================================
    // Instruction Fetch Adapter — Ibex → boot_rom
    // =========================================================================
    //
    // Protocol: Ibex asserts instr_req_o → adapter immediately grants
    // (instr_gnt=1).  boot_rom reads the word-address in the same cycle.
    // One cycle later, adapter asserts instr_rvalid=1 with the ROM output.
    //
    // We register instr_req_o so that instr_rvalid fires on the cycle
    // after the request. The ROM output is already registered (1-cycle
    // read latency).

    // ROM word address: byte-address[15:2] → word-address[13:0]
    assign rom_addr = instr_addr[15:2];

    // Grant immediately when Ibex requests
    assign instr_gnt = instr_req;

    // Delay instr_req by 1 cycle to produce rvalid
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            instr_req_d1 <= 1'b0;
        else
            instr_req_d1 <= instr_req && instr_gnt;
    end

    assign instr_rvalid = instr_req_d1;
    assign instr_rdata  = rom_instr;
    assign instr_err    = 1'b0;

    // =========================================================================
    // Data Access State Machine — Register Declarations
    // =========================================================================
    // These registers are declared here for early reference by combinational
    // logic (dmem_waddr, rom_data_addr).  They are driven by the sequential
    // state machine below.

    typedef enum logic [2:0] {
        ST_IDLE     = 3'd0,
        ST_AXI_RD   = 3'd1,
        ST_AXI_WR   = 3'd2,
        ST_APB_RD   = 3'd3,
        ST_APB_WR   = 3'd4,
        ST_DMEM_ACC = 3'd5,
        ST_BOOT_RD  = 3'd6,
        ST_ERROR    = 3'd7
    } state_t;

    state_t state, state_next;
    reg [31:0]              req_addr;
    reg [31:0]              req_wdata;
    reg                     req_we;
    reg [3:0]               req_be;
    reg [31:0]              resp_rdata;
    reg                     resp_err;
    reg                     resp_valid;
    reg                     axi_arvalid;
    reg                     axi_awvalid;
    reg                     axi_wvalid;
    reg                     axi_bready;
    reg                     axi_rready;
    reg                     apb_psel_r;
    reg                     apb_penable_r;
    reg                     apb_pwrite_r;
    reg [31:0]              apb_paddr_r;
    reg [31:0]              apb_pwdata_r;

    // DMEM word address (captured req_addr → stable in ST_DMEM_ACC)
    wire [13:0]             dmem_waddr;
    assign dmem_waddr = req_addr[15:2];

    // =========================================================================
    // State Machine — Sequential Logic
    // =========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= ST_IDLE;
            req_addr    <= 32'h0;
            req_wdata   <= 32'h0;
            req_we      <= 1'b0;
            req_be      <= 4'h0;
            resp_rdata  <= 32'h0;
            resp_err    <= 1'b0;
            resp_valid  <= 1'b0;
        end else begin
            state <= state_next;

            case (state)
                ST_IDLE: begin
                    resp_valid <= 1'b0;
                    if (data_req) begin
                        // Capture the request
                        req_addr  <= data_addr;
                        req_wdata <= data_wdata;
                        req_we    <= data_we;
                        req_be    <= data_be;
                    end
                end

                ST_DMEM_ACC: begin
                    // DMEM access completes in 1 cycle
                    if (req_we) begin
                        // Write to DMEM (byte-enable aware)
                        if (req_be[0]) dmem[dmem_waddr][7:0]   <= req_wdata[7:0];
                        if (req_be[1]) dmem[dmem_waddr][15:8]  <= req_wdata[15:8];
                        if (req_be[2]) dmem[dmem_waddr][23:16] <= req_wdata[23:16];
                        if (req_be[3]) dmem[dmem_waddr][31:24] <= req_wdata[31:24];
                        resp_rdata <= 32'h0;
                        resp_err   <= 1'b0;
                    end else begin
                        // Read from DMEM
                        resp_rdata <= dmem[dmem_waddr];
                        resp_err   <= 1'b0;
                    end
                    resp_valid <= 1'b1;
                end

                ST_BOOT_RD: begin
                    // Boot ROM read has 1-cycle latency:
                    //   Cycle 1 (enter): rom_data_addr set, ROM reads internally
                    //   Cycle 2: data ready, capture and respond
                    if (req_we) begin
                        // Write to boot_rom → bus error
                        resp_rdata <= 32'h0;
                        resp_err   <= 1'b1;
                        resp_valid <= 1'b1;
                    end else begin
                        // Capture data that arrived this cycle
                        rom_data_rdata <= rom_data_out;
                        resp_rdata <= rom_data_out;
                        resp_err   <= 1'b0;
                        resp_valid <= 1'b1;
                    end
                end

                ST_ERROR: begin
                    resp_rdata <= 32'h0;
                    resp_err   <= 1'b1;
                    resp_valid <= 1'b1;
                end

                ST_AXI_RD: begin
                    resp_valid <= 1'b0;
                    // Wait for AXI read response
                    if (m_axi_rvalid && axi_rready) begin
                        resp_rdata <= m_axi_rdata;
                        resp_err   <= (m_axi_rresp == 2'b00) ? 1'b0 : 1'b1;
                        resp_valid <= 1'b1;
                    end
                end

                ST_AXI_WR: begin
                    resp_valid <= 1'b0;
                    // Wait for AXI write response
                    if (m_axi_bvalid && axi_bready) begin
                        resp_err   <= (m_axi_bresp == 2'b00) ? 1'b0 : 1'b1;
                        resp_valid <= 1'b1;
                    end
                end

                ST_APB_RD: begin
                    resp_valid <= 1'b0;
                    // APB read: psel=1, penable=1 → wait for pready
                    if (apb_pready) begin
                        resp_rdata <= apb_prdata;
                        resp_err   <= apb_pslverr;
                        resp_valid <= 1'b1;
                    end
                end

                ST_APB_WR: begin
                    resp_valid <= 1'b0;
                    // APB write: psel=1, penable=1 → wait for pready
                    if (apb_pready) begin
                        resp_err   <= apb_pslverr;
                        resp_valid <= 1'b1;
                    end
                end

                default: begin
                    resp_valid <= 1'b0;
                end
            endcase
        end
    end

    // ── Data response to Ibex ────────────────────────────────────────────
    assign data_gnt    = data_req && (state == ST_IDLE);
    assign data_rvalid = resp_valid;
    assign data_rdata  = resp_rdata;
    assign data_err    = resp_err;

    // =========================================================================
    // State Machine — Next-State Combinational Logic
    // =========================================================================
    always @(*) begin
        state_next = state;
        case (state)
            ST_IDLE: begin
                if (data_req) begin
                    if (is_undefined)
                        state_next = ST_ERROR;
                    else if (is_boot_rom)
                        state_next = ST_BOOT_RD;
                    else if (dmem_hit)
                        state_next = ST_DMEM_ACC;
                    else if (is_axi4)
                        state_next = data_we ? ST_AXI_WR : ST_AXI_RD;
                    else if (is_apb)
                        state_next = data_we ? ST_APB_WR : ST_APB_RD;
                    else
                        state_next = ST_ERROR;
                end
            end

            ST_DMEM_ACC: state_next = ST_IDLE;
            ST_BOOT_RD:  state_next = ST_IDLE;
            ST_ERROR:    state_next = ST_IDLE;

            ST_AXI_RD: begin
                // Wait for read completion
                if (m_axi_rvalid && axi_rready)
                    state_next = ST_IDLE;
            end

            ST_AXI_WR: begin
                // Wait for write completion
                if (m_axi_bvalid && axi_bready)
                    state_next = ST_IDLE;
            end

            ST_APB_RD: begin
                if (apb_pready)
                    state_next = ST_IDLE;
            end

            ST_APB_WR: begin
                if (apb_pready)
                    state_next = ST_IDLE;
            end

            default: state_next = ST_IDLE;
        endcase
    end

    // =========================================================================
    // AXI4 Master — Channel Drivers
    // =========================================================================
    // AXI read address channel: assert on entering ST_AXI_RD
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            axi_arvalid <= 1'b0;
        end else begin
            if (state == ST_IDLE && data_req && !data_we && is_axi4 && data_gnt)
                axi_arvalid <= 1'b1;
            else if (axi_arvalid && m_axi_arready)
                axi_arvalid <= 1'b0;
        end
    end

    assign m_axi_arid    = {AXI_ID_WIDTH{1'b0}};
    assign m_axi_araddr  = req_addr;
    assign m_axi_arlen   = 8'd0;        // single beat
    assign m_axi_arsize  = 3'd2;        // 4 bytes (32-bit)
    assign m_axi_arburst = 2'd1;        // INCR
    assign m_axi_arvalid = axi_arvalid;

    // AXI read data channel
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            axi_rready <= 1'b0;
        else if (state == ST_AXI_RD)
            axi_rready <= 1'b1;
        else
            axi_rready <= 1'b0;
    end
    assign m_axi_rready = axi_rready;

    // AXI write address channel
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            axi_awvalid <= 1'b0;
        end else begin
            if (state == ST_IDLE && data_req && data_we && is_axi4 && data_gnt)
                axi_awvalid <= 1'b1;
            else if (axi_awvalid && m_axi_awready)
                axi_awvalid <= 1'b0;
        end
    end

    assign m_axi_awid    = {AXI_ID_WIDTH{1'b0}};
    assign m_axi_awaddr  = req_addr;
    assign m_axi_awlen   = 8'd0;        // single beat
    assign m_axi_awsize  = 3'd2;        // 4 bytes
    assign m_axi_awburst = 2'd1;        // INCR
    assign m_axi_awvalid = axi_awvalid;

    // AXI write data channel
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            axi_wvalid <= 1'b0;
        end else begin
            if (state == ST_IDLE && data_req && data_we && is_axi4 && data_gnt)
                axi_wvalid <= 1'b1;
            else if (axi_wvalid && m_axi_wready)
                axi_wvalid <= 1'b0;
        end
    end

    // Map byte-enables to wstrb (4-bit be → 4-bit strb, both 32-bit bus)
    assign m_axi_wdata  = req_wdata;
    assign m_axi_wstrb  = req_be;
    assign m_axi_wlast  = 1'b1;        // single beat
    assign m_axi_wvalid = axi_wvalid;

    // AXI write response channel
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            axi_bready <= 1'b0;
        else if (state == ST_AXI_WR)
            axi_bready <= 1'b1;
        else
            axi_bready <= 1'b0;
    end
    assign m_axi_bready = axi_bready;

    // =========================================================================
    // APB Master — Channel Drivers
    // =========================================================================
    // APB: psel asserts first cycle, penable asserts second cycle (setup→access)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            apb_psel_r    <= 1'b0;
            apb_penable_r <= 1'b0;
            apb_pwrite_r  <= 1'b0;
            apb_paddr_r   <= 32'h0;
            apb_pwdata_r  <= 32'h0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (data_req && is_apb && data_gnt) begin
                        // Setup phase
                        apb_psel_r    <= 1'b1;
                        apb_penable_r <= 1'b0;
                        apb_pwrite_r  <= data_we;
                        apb_paddr_r   <= req_addr;
                        apb_pwdata_r  <= req_wdata;
                    end else begin
                        apb_psel_r    <= 1'b0;
                        apb_penable_r <= 1'b0;
                    end
                end

                ST_APB_RD, ST_APB_WR: begin
                    // Access phase: penable=1 with psel=1
                    apb_penable_r <= 1'b1;
                    if (apb_pready) begin
                        // Transaction complete, deassert
                        apb_psel_r    <= 1'b0;
                        apb_penable_r <= 1'b0;
                    end
                end

                default: begin
                    apb_psel_r    <= 1'b0;
                    apb_penable_r <= 1'b0;
                end
            endcase
        end
    end

    assign apb_paddr   = apb_paddr_r;
    assign apb_psel    = apb_psel_r;
    assign apb_penable = apb_penable_r;
    assign apb_pwrite  = apb_pwrite_r;
    assign apb_pwdata  = apb_pwdata_r;

    // =========================================================================
    // Ibex Top Instantiation — RV32IMC, no cache / no PMP / no RV32B
    // =========================================================================
    ibex_top #(
        .PMPEnable           (1'b0),
        .PMPGranularity      (0),
        .PMPNumRegions       (4),
        .MHPMCounterNum      (0),
        .MHPMCounterWidth    (40),
        .RV32E               (1'b0),
        .RV32M               (ibex_pkg::RV32MFast),       // M extension — fast mul/div
        .RV32B               (ibex_pkg::RV32BNone),       // No bitmanip
        .RV32ZC              (ibex_pkg::RV32ZcaZcbZcmp),  // C extension
        .RegFile             (ibex_pkg::RegFileFF),
        .BranchTargetALU     (1'b0),
        .WritebackStage      (1'b0),
        .ICache              (1'b0),
        .ICacheECC           (1'b0),
        .BranchPredictor     (1'b0),
        .DbgTriggerEn        (1'b0),
        .SecureIbex          (1'b0),
        .ICacheScramble      (1'b0)
    ) u_ibex_top (
        .clk_i                   (clk),
        .rst_ni                  (rst_n),

        .test_en_i               (1'b0),
        .ram_cfg_icache_tag_i    ('0),
        .ram_cfg_rsp_icache_tag_o(),
        .ram_cfg_icache_data_i   ('0),
        .ram_cfg_rsp_icache_data_o(),

        .hart_id_i               (32'h0),
        .boot_addr_i             (32'h0000_0000),

        // Instruction memory interface
        .instr_req_o             (instr_req),
        .instr_gnt_i             (instr_gnt),
        .instr_rvalid_i          (instr_rvalid),
        .instr_addr_o            (instr_addr),
        .instr_rdata_i           (instr_rdata),
        .instr_rdata_intg_i      ('0),
        .instr_err_i             (instr_err),

        // Data memory interface
        .data_req_o              (data_req),
        .data_gnt_i              (data_gnt),
        .data_rvalid_i           (data_rvalid),
        .data_we_o               (data_we),
        .data_be_o               (data_be),
        .data_addr_o             (data_addr),
        .data_wdata_o            (data_wdata),
        .data_wdata_intg_o       (data_wdata_intg_unused),
        .data_rdata_i            (data_rdata),
        .data_rdata_intg_i       ('0),
        .data_err_i              (data_err),

        // Interrupt inputs
        .irq_software_i          (1'b0),
        .irq_timer_i             (1'b0),
        .irq_external_i          (cpu_irq_i),
        .irq_fast_i              (15'h0),
        .irq_nm_i                (1'b0),

        // Scrambling interface (unused)
        .scramble_key_valid_i    (1'b0),
        .scramble_key_i          ('0),
        .scramble_nonce_i        ('0),
        .scramble_req_o          (),

        // Debug interface (unused)
        .debug_req_i             (1'b0),
        .crash_dump_o            (),
        .double_fault_seen_o     (double_fault_seen_unused),

        // CPU Control Signals
        .fetch_enable_i          (ibex_pkg::IbexMuBiOn),
        .mcounteren_writable_i   (ibex_pkg::IbexMuBiOn),
        .alert_minor_o           (alert_minor_unused),
        .alert_major_internal_o  (alert_major_internal_unused),
        .alert_major_bus_o       (alert_major_bus_unused),
        .core_sleep_o            (core_sleep_unused),

        // DFT bypass controls
        .scan_rst_ni             (1'b1),

        // Lockstep
        .lockstep_cmp_en_o       (lockstep_cmp_en_unused),

        // Shadow core outputs (unused)
        .data_req_shadow_o       (data_req_shadow_unused),
        .data_we_shadow_o        (data_we_shadow_unused),
        .data_be_shadow_o        (data_be_shadow_unused),
        .data_addr_shadow_o      (data_addr_shadow_unused),
        .data_wdata_shadow_o     (data_wdata_shadow_unused),
        .data_wdata_intg_shadow_o(data_wdata_intg_shadow_unused),
        .instr_req_shadow_o      (instr_req_shadow_unused),
        .instr_addr_shadow_o     (instr_addr_shadow_unused)
    );

    // =========================================================================
    // Boot ROM Instantiation
    // =========================================================================
    boot_rom u_boot_rom (
        .clk         (clk),
        .rst_n       (rst_n),
        .addr_i      (rom_addr),
        .instr_o     (rom_instr),
        .data_addr_i (rom_data_addr),
        .data_o      (rom_data_out)
    );

    // ROM data address: driven from req_addr when in ST_BOOT_RD
    assign rom_data_addr = req_addr[15:2];

endmodule
