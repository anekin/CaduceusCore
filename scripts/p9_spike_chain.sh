#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"
p9_ssh "PYTHONPATH=sim python3 -m sim.spike_host --mode chain --ops mmul,sfu,vector,dma_copy 2>&1 | tee build/evidence/ph9-spike-abi.txt"
