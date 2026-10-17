# AI-HDL / From Prompts to Silicon — primary source record

**Source**: `arXiv:2610.18891v1` — *From Prompts to Silicon: Scaling LLM-Assisted Chip Design with the AI Hardware Design League*
**Submitted**: Fri, 9 Oct 2026 01:52:40 UTC · **Length**: 5 pages · **PDF**: 3.0 MB (local: `~/Documents/Obsidian_Vault/论文归档/From_Prompts_to_Silicon_AI-HDL_arXiv2610.18891.pdf`)
**Authors**: Parsa Mirfasihi, Harish Kumar Dharavath, Muhtasim Alam Chowdhury, Sujan Ghimire, Banafsheh Saber Latibari, Rozhin Yasaei, Pratik Satam, **Soheil Salehi**∗ (∗corresponding, last author)
**Affiliation**: School of Electrical, Computing, and Software Engineering + College of Information Sciences, University of Arizona
**Program host (external)**: UA Center for Semiconductor Manufacturing — `https://csm.arizona.edu/AIHDL`
**Code/artifacts**: `https://github.com/prismlabarizona/AIHDL-2026` (created 2025-09-12, pushed 2026-09-28, ★13) · `.../AIHDL-2027` (created 2026-09-02, ★9)
**Verified**: 2026-10-17, by PDF text extraction + grep of full context + Table I cell-by-cell comparison (no fixed-window sampling).
**Trigger**: Chinese-language article (公众号「麒芯说AI」《AI 把芯片设计的起跑线拉平了，终点线没动》, `ct=1792195200` → 2026-10-17 08:00) shared by user; primary-source verification required.
**Chinese-source verification note**: `~/Documents/Obsidian_Vault/阅读存档/2026-10-17-AI把芯片设计起跑线拉平了-麒芯说AI中文稿核实.md`

---

## 1. Verbatim claim (abstract)

> "The semiconductor workforce shortage has renewed interest in making chip design accessible to students without conventional very-large-scale integration (VLSI) training. We present the AI Hardware Design League (AI-HDL), an international, project-based competition in which students use Large Language Models (LLMs) to generate and iteratively refine Verilog while simulation, synthesis, timing analysis, security review, and physical verification provide the engineering feedback needed to validate the result. Across two annual cycles, AI-HDL grew from 172 to 604 participants and from 40 to 108 teams, reaching 30 universities, six community colleges, and 19 countries. In the 2026 cohort, 68.7% of participants reported no prior Verilog experience. Twenty-two teams completed the four design phases and reached final evaluation. Two division-winning designs completed physical sign-off in the SkyWater 130 nm (Sky130) process, passing design-rule checking and layout-versus-schematic verification with zero violations before being submitted for fabrication. These results show that an LLM-assisted, tool-grounded workflow can be deployed at substantial scale while also revealing that **completion, rather than initial access, remains the principal challenge**. AI-HDL materials and finalist artifacts are released to support replication and future study."

Three stated contributions (§I):
1. A reproducible **four-phase structure** for LLM-assisted hardware design in which EDA feedback closes the loop between natural-language specification and verified implementation.
2. Two annual deployments totalling **776 participants**, quantifying growth, prior experience, and progression.
3. Physical implementation results for **two selected 2026 designs** that reached clean Sky130 sign-off — showing the workflow can terminate in **fabrication-ready hardware rather than only simulated RTL**.

## 2. Program structure (§II)

| Phase | Content |
|---|---|
| DP1 | functional RTL, testbench development, baseline synthesis |
| DP2 | timing and PPA analysis, followed by RTL or micro-architectural revision |
| DP3 | security evaluation and threat mitigation |
| DP4 | floorplanning, placement, clock-tree synthesis, routing, DRC and LVS sign-off |

Each phase ≈ one month, ends in a **graded artifact**; teams failing the phase rubric do not advance.

- Pilot cycle: **Oct 2024 – Mar 2025**, 172 participants / 40 teams, design = a digital watch, FPGA prototype + PPA/security optimization + tape-out prep.
- Second cycle: **Jan – May 2026**, 604 participants / 108 teams, *"a more demanding **common RISC-V base design**. Teams extended the processor with a custom peripheral."*
- Timeline (§V): webinars Nov–Dec; DP1–DP4 in January, February, March, April; final submission/evaluation; **winners announced in May**; award ceremony at the University of Arizona (§VI).

**Central rule (verbatim)**: *"teams generate the hardware by prompting an LLM and submit prompt logs with their design artifacts. Manual corrections are permitted but must be documented. **Model output is never treated as evidence of correctness.**"*
**Tool chain (verbatim)**: simulation = **Icarus Verilog** + **Verilator**; synthesis = **Yosys**; physical implementation = **OpenLane** on **Sky130**; final sign-off = DRC/LVS.
**Feedback framing (verbatim)**: *"Tool reports therefore provide an **external corrective signal**, which includes syntax errors, width mismatches, timing violations, and physical-design failures that must be resolved before a design can progress."*
**Evaluation policy (§II)**: *"Prior experience is self-reported. … Because AI-HDL is an operating program rather than a controlled intervention, the results characterize reach, progression, and engineering outcomes, and they do not isolate the causal contribution of the LLM from mentoring, competition incentives, templates, or the open-source toolchain."*

## 3. Industry partners (§II) — the variable the Chinese article dropped

- **Intel, Arm, NVIDIA** — participant recognition and awards.
- **Cadence, Synopsys** — *"partnerships with Cadence and Synopsys provide participants with free access to industry-standard EDA tools, **removing a major practical barrier to hands-on chip design**."*
- **ChipMango** — curriculum development partner; free beginner/intermediate VLSI design certificate programmes.
- **ChipFoundry** — tape-out partner, path from physical sign-off to fabricated silicon.

## 4. Scale, access, progression (§III)

| Metric | Pilot | 2026 cycle |
|---|---|---|
| Participants | 172 | **604** |
| Teams | 40 | **108** |
| Universities | 6 | **30** |
| Community colleges | 2 | **6** |
| Countries | 5 | **19** |
| Degree programmes reported | — | 19 |

Cumulative across both cycles: **776 participants**; enrolment **3.5×**, teams **2.7×**.

Prior experience (2026 cohort, denominator 604):
- **415 (68.7%)** — no prior Verilog experience; **+150** beginner-level ⇒ **565 (93.5%)** at no/beginner level.
- LLM familiarity: **166 (27.5%)** none, **278 (46.0%)** beginner ⇒ **444 (73.5%)** little/none; 133 (22.0%) knowledgeable; 27 (4.5%) expert.

Progression (§III + Fig.3): of **108 registered teams**, **22** completed all four phases and reached final evaluation = **20.4%** of teams, **74 participants**.
Fig.3 split: winning teams **2 (1.9%)**; finalist teams **20 (18.5%)**; did not complete phases **86 (79.6%)**. Participants: reached final evaluation **74 (12.3%)**; did not **530 (87.7%)**.
§III framing (verbatim): *"Open registration makes initial participation intentionally low-friction, whereas later phases require functional verification, timing closure, security review, and physical implementation. The resulting gap between entry and completion is therefore a program outcome rather than a statistic to hide."*

## 5. Physical implementation outcomes (§IV, Table I)

> "SELECTED POST-LAYOUT RESULTS FOR THE TWO 2026 DIVISION-WINNING DESIGNS **AT 1.8 V**. POWER VALUES ARE POST-LAYOUT ESTIMATES FROM THE IMPLEMENTATION FLOW."

| Metric | SPI + CRC-16 | AES-128 |
|---|---|---|
| Core area (µm²) | **37,488** | **149,082** |
| Standard cells | **2,392** | **6,492** |
| Clock frequency (MHz) | **71.43** | **57.10** |
| Estimated total power | **2.29 µW** | **5.68 mW** |
| DRC/LVS violations | **0** | **0** |

Caveat (verbatim): *"The values should be interpreted as **two implementation case studies rather than a head-to-head comparison** because the blocks serve different functions and were optimized under different design objectives."* Both are integrated with their respective RISC-V systems.

**Fig.4 — composite layout (the "2 : 9" evidence)**: *"Composite layout visualization of **eleven designs** submitted for fabrication through the AI-HDL tapeout effort. All designs completed physical sign-off, including **two AI-HDL 2026 division-winning student designs** and **nine additional designs finalized by mentors and professional engineers**."* Layout labels read (verified visually at 2806×2207): `UofA BMU`, `UofA BMI`, `UofA - VEC` (×2), `UofA - BP`, `UofA - MUL`, `UofA - GPIO`, `SLM SoC`, plus two rotated small labels **`ITIMS`** and **`NECRL`** — these match the public winner-design directory names `ITIMS-Boys_main` / `NECRL---AI-HDL-main`. Callout functions: Bit Manipulation Coprocessor / SIMD Vector Coprocessor (4×8-bit lanes) / **AES-128 Encryption (128-bit blocks and keys, pipelined round processing, composite-field S-box)** / Neural Branch Predictor (PicoRV32 + perceptron, trace-based history training) / RISC-V GPIO SoC; left side: Bit Manipulation Unit, **SLM Inference SoC (RV32IM + INT8 GEMM accelerator + Softmax/RMSNorm + KV cache/DMA)**, SIMD Vector Processor, Hardware Multiplier.

**Explicit anti-overclaim (verbatim)**: *"The result is **not** that an LLM autonomously designed a correct chip. Rather, students used an LLM to generate and revise RTL while simulators, synthesis, timing analysis, and physical verification determined whether each revision was acceptable."*

## 6. Discussion and limitations (§VII)

> "Registration growth is much larger than completion growth, and only 20.4% of 2026 teams reached final evaluation. **Lowering the barrier to a first RTL implementation therefore does not remove the difficulty of timing closure, verification, security analysis, or physical design.**"

Five stated constraints:
1. **No non-LLM control group** — the effect of prompting cannot be separated from mentoring, phase gates, templates, or fabrication incentives.
2. Prior experience was **self-reported**.
3. **No pre/post-program assessments** — learning gains not directly measured.
4. Teams **freely chose their LLM**; prompt logs cannot prove that no unreported manual authoring occurred.
5. Physical results come from **two selected designs on a single open process node** — not representative of every participating team or of larger industrial designs.

Future work (verbatim): *"track phase-level attrition more closely, add direct learning measures, and use the accumulated prompt logs to study how novice design strategies change as EDA feedback becomes more demanding."* **Note**: silicon bring-up / post-fab measurement is **not** listed as future work — the paper stops at "sent for fabrication". (The Chinese article's second "future work" item is its own extrapolation.)

## 7. Workforce-gap citation — separated from the paper's own numbers

Paper §I cites **only one** figure: *"The Semiconductor Industry Association and Oxford Economics project that roughly **67,000** U.S. semiconductor positions could remain unfilled by 2030 at current degree-completion rates [1]."* Reference [1] = SIA × Oxford Economics, *"Chipping Away: Assessing and Addressing the Labor Market Gap Facing the U.S. Semiconductor Industry"*, **Tech. Rep., Jul. 2023**.

The fuller breakdown quoted by the Chinese article comes from that report's own Fig.1 (PDF verified locally, 33 pages, 71,213 chars): *"Industry to add **114,800** jobs by 2030"*; *"**27,300** engineers / **26,400** technicians / **13,400** computer scientists"*; *"67,000, or 58%, of new jobs across manufacturing and design will risk going unfilled by 2030"*; *"of the unfilled jobs, 39% will be technicians … 35% will be engineers with four-year degrees or computer scientists; and 26% will be engineers at the master's or PhD level."* Local copy: `~/Documents/Obsidian_Vault/temp/sia_chipping_away.pdf`.

## 8. Released artifacts (verified via GitHub API)

```
prismlabarizona/AIHDL-2026  (created 2025-09-12, pushed 2026-09-28, ★13)
├── AI-HDL-Tape-out-Chipfoundry-main/
├── AI_HDL_2026_Winner_Designs/
│   ├── ITIMS-Boys_main/          → ITIMS_ Boy-challenge-1/2/3
│   └── NECRL---AI-HDL-main/      → AES128/ , DP1..DP4_submission/ ,
│                                   cpu.v decode.v alu.v mem_ctrl.v peripheral.v
│                                   latch_reg.v falling_edge_detector.sv
│                                   alu.sby alu_verify.sv mul_verify.sv
│                                   lint.sh pnr_signoff.sdc base.sdc
│                                   Makefile config.json core_instr.py THE_plan.txt
├── challenges/Design Phase 1..4/
├── examples/  submissions/  docs/  tool_setup/
└── tinyqv-full-peripheral-template/     # TinyQV RISC-V core + peripheral template
prismlabarizona/AIHDL-2027  (created 2026-09-02, ★9)   # third cycle already open
```

## 9. Relevance to RTL-EVAL / harness thesis

- **Entry vs exit must be measured separately**: 68.7% zero-experience entry ↔ 20.4% team completion is a clean, citable pair for "the model raises the floor; the engineering gates set the ceiling".
- **The 2 : 9 fabrication roster is a reusable counter-example** to any "AI autonomously completed end-to-end chip design" claim, alongside Build-A-Chip-Bench (Claude Fable 5.1 score 16.6%, GPT-6 Astra 8.7%).
- **Acceptance-clause precedent**: phase gates + mandatory prompt logs + "model output is never treated as evidence of correctness" + tool reports as external corrective signal — a non-ad-hoc, scaled (776 participants) precedent for agent-output acceptance criteria.
- **Fixture source**: the public repo yields a rubric-bearing, multi-phase long-horizon task set (four phase specs + TinyQV template + two real completed designs with formal properties and PnR sign-off constraints).
- **Known gap**: the paper did not collect phase-level attrition data — an open opportunity for an independent replication.
