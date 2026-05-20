# llm-mlx/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-mlx/files/wheels/   ← Python wheels for mlx + mlx-lm + deps
```

## Download (on a Mac with internet)

```bash
pip download \
  "mlx>=0.16.0" "mlx-lm>=0.19.0" \
  "huggingface-hub>=0.23.0" "transformers>=4.41.0" \
  "sentencepiece>=0.2.0" "protobuf>=4.25.0" "numpy>=1.26.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-mlx/files/wheels/
```

## Model files

Place quantized MLX model directories under a shared NFS/SMB path or copy directly onto each node. Set `mlx_default_model` in host_vars or group_vars to a local absolute path, e.g.:

```yaml
mlx_default_model: /opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit
```
