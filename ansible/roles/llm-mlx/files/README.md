# llm-mlx/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-mlx/files/wheels/   ← Python wheels for mlx + mlx-lm + deps
```

## Dependency delivery

Packages install from the on-prem JFrog Artifactory PyPI repo by default
(`mlx_use_jfrog: true`). No wheels need to be vendored for that path.

### Fully air-gapped fallback

Set `mlx_use_jfrog: false` and stage wheels into `files/wheels/`:

    pip download "mlx-lm==0.31.3" "mlx>=0.22.0" "huggingface-hub>=0.27.0" \
      "transformers>=4.48.0" "sentencepiece>=0.2.0" "protobuf>=4.25.0" \
      "numpy>=1.26.0,<2.3.0" \
      --platform macosx_14_0_arm64 --python-version 311 \
      --only-binary=:all: -d ansible/roles/llm-mlx/files/wheels/

## Model files

Place quantized MLX model directories under a shared NFS/SMB path or copy directly onto each node. Set `mlx_default_model` in host_vars or group_vars to a local absolute path, e.g.:

```yaml
mlx_default_model: /opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit
```
