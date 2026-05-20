# llm-vllm/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-vllm/files/wheels/   ← Python wheels for vLLM + deps
```

## Download (on a Mac with internet)

```bash
pip download \
  "vllm>=0.4.0" "torch>=2.3.0" \
  "numpy>=1.26.0" "transformers>=4.41.0" \
  "huggingface-hub>=0.23.0" "sentencepiece>=0.2.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-vllm/files/wheels/
```

> **Note:** vLLM uses `--device metal` on Apple Silicon. No CUDA required.
> The model path (`vllm_default_model`) must be a local directory reachable by the daemon.
