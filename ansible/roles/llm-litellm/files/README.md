# llm-litellm/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-litellm/files/wheels/   ← Python wheels for LiteLLM + deps
```

## Download (on a Mac with internet)

```bash
pip download \
  "litellm[proxy]>=1.40.0" \
  "uvicorn[standard]==0.32.0" \
  "python-dotenv>=1.0.0" \
  "pydantic>=2.7.0" \
  "httpx>=0.27.0" \
  "redis>=5.0.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-litellm/files/wheels/
```

## Model name routing

Clients call the gateway with `model: "<pool>/<model>"`:

| Model name | Pool | Nodes | Backend |
|---|---|---|---|
| `fast/mistral-7b` | fast_pool | 10 | mlx :11434 |
| `reason/qwen2.5-14b` | reason_pool | 4 | mlx :11434 |
| `largepool/qwen2.5-32b` | large_pool | 2 pairs | exo :52416 |
| `vision/llava-7b` | vision_pool | 3 | mlx :11434 |
| `embed/nomic-embed` | embed_pool | 2 | mlx :11434 |
| `speech/whisper-large` | speech_pool | 1 | mlx :11434 |

## Example call

```bash
curl http://hydra-gw-01.hydra.local:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-hydra-master-changeme" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "fast/mistral-7b",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

## Rotating the master key

Update `litellm_master_key` in `ansible/roles/llm-litellm/vars/main.yml` or per-host in `host_vars/`, then re-run the playbook. Vault integration is planned for v2.
