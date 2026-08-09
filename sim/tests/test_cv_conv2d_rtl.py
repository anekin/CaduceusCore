import os, sys, struct, time; from pathlib import Path; import numpy as np
REPO = Path("/home/prj/zhengs/caduceuscore/CaduceusCore")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "sim"))
import cocotb; from cocotb_bridge import CocotbBridge; from regmap import Addr, DOORBELL

VD=REPO/"rtl/test_vectors/soc_e2e/cv_conv2d_rtl"; ED=REPO/"build/evidence"
ACT_ADDR=0x80010000; WGT_ADDR=0x80063000; OUT_ADDR=0x80066000
SCALE_ADDR=0x80064000; DESC_ADDR=0x80010080

def cs(a,b):
    af=a.astype(np.float64).ravel(); bf=b.astype(np.float64).ravel()
    d=float(np.dot(af,bf)); na=float(np.linalg.norm(af)); nb=float(np.linalg.norm(bf))
    return d/(na*nb) if na>1e-12 and nb>1e-12 else 0.0

@cocotb.test()
async def test_cv_conv2d_rtl(dut):
    b=CocotbBridge(dut); await b.start_clock(); await b.reset(5)
    inp=np.load(str(VD/"input.npz"),allow_pickle=True)
    exp=np.load(str(VD/"expected.npz"),allow_pickle=True)
    M,K,N=int(inp["M"]),int(inp["K"]),int(inp["N"]); gi=exp["golden_i32"]
    ar=inp["act_reformatted"].tobytes(); wp=inp["w_packed"].tobytes()
    sp=inp["w_scales"].tobytes(); isize=int(inp["input_size"]); wsize=int(inp["weight_size"])
    print(f"[CV] M={M} K={K} N={N} act={len(ar)}B wt={len(wp)}B sc={len(sp)}B")
    await b._dram_backdoor_write(ACT_ADDR,ar)
    await b._dram_backdoor_write(WGT_ADDR,wp)
    await b._dram_backdoor_write(SCALE_ADDR,sp)
    desc=struct.pack("<15I",ACT_ADDR,WGT_ADDR,OUT_ADDR,SCALE_ADDR,0,0,0,0,isize,wsize,M*N*4,len(sp),M,K,N)
    await b._dram_backdoor_write(DESC_ADDR,desc)
    cmd=struct.pack("<8I",0,DESC_ADDR,0,0,0,0,0,0)
    await b._dram_backdoor_write(Addr.DRAM_BASE,cmd)
    await b._doorbell_backdoor_write(Addr.DOORBELL+DOORBELL.HOST_TAIL,1)
    for cyc in range(200000):
        if await b._doorbell_backdoor_read(Addr.DOORBELL+DOORBELL.NPU_HEAD)==1:
            wcyc=cyc; break
        await b.wait_cycles(10)
    ob=await b._dram_backdoor_read(OUT_ADDR,M*N*4)
    ro=np.frombuffer(ob,dtype=np.int32).reshape(M,N)
    ok=np.array_equal(ro,gi); c=cs(ro.astype(np.float32),gi.astype(np.float32))
    print(f"[CV] match={ok} cos_sim={c:.6f}")
    os.makedirs(str(ED),exist_ok=True); ts=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
    v="PASS" if ok else "FAIL"
    header="W3-RTL Task 19: MobileNetV3 Single Conv2D RTL Verification"
    lines=[header,"="*len(header),
        f"Timestamp: {ts}","Simulator: ibex (RTL SoC)",
        f"Dims: M={M}, K={K}, N={N}","",
        f"RTL INT32 bit-exact: {ok}",
        f"RTL cos_sim vs golden: {c:.6f}","",
        "Threshold: cos_sim >= 0.99",
        f"Result: {v}","="*len(header)]
    with open(ED/"w3-rtl-cv-conv2d.txt","w") as f:
        f.write("\n".join(lines)+"\n")
    print(f"[CV] Evidence: {v}")
    assert ok,"INT32 mismatch"
