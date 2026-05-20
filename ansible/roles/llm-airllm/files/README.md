# llm-airllm/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-airllm/files/wheels/   ← Python wheels for AirLLM + deps
```

## Download (on a Mac with internet)

```bash
pip download \
  "airllm>=0.2.4" "torch>=2.3.0" \
  "transformers>=4.41.0" "huggingface-hub>=0.23.0" \
  "fastapi==0.115.0" "uvicorn[standard]==0.32.0" \
  "numpy>=1.26.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-airllm/files/wheels/
```

## Notes

- AirLLM streams model layers from disk on each forward pass — suits 16 GB RAM nodes with large models.
- `AIRLLM_COMPRESSION=0.16` aggressively quantizes layer cache; raise to 0.5 for better quality at higher RAM cost.
- Model path must be a local HuggingFace-format directory (not a GGUF file).
