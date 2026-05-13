# BitNet

Placeholder for [BitNet](https://github.com/microsoft/BitNet) — Microsoft's 1-bit LLM inference framework.

## What goes here

- BitNet model weights (1-bit quantized)
- Inference server configuration
- Benchmark results against llama.cpp and vLLM on Hydra hardware

## Status

**Planned** — not yet deployed on Hydra cluster.

## Relevant hardware context

BitNet's 1-bit quantization targets CPU inference with minimal VRAM. On the Hydra cluster this is relevant for:
- Apple Silicon: unified memory can serve larger models at 1-bit vs Q4
- i7/RTX 3050 node: CPU-only path with 64 GB DDR5 becomes viable for 70B+ models at 1-bit

## References

- [microsoft/BitNet](https://github.com/microsoft/BitNet)
- [BitNet paper](https://arxiv.org/abs/2402.17764)
- See `../benchmarkings.md` for Phase 1 benchmark methodology
