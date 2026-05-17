# model-registry — Offline Dependencies

No internet on target. Pre-stage these binaries before running the playbook.

## Required files

```bash
# MinIO server (darwin arm64) — object storage for model artifacts
curl -LO https://dl.min.io/server/minio/release/darwin-arm64/minio
chmod +x minio
mv minio ansible/roles/model-registry/files/minio_darwin_arm64

# MinIO client (mc) — bucket management
curl -LO https://dl.min.io/client/mc/release/darwin-arm64/mc
chmod +x mc
mv mc ansible/roles/model-registry/files/mc_darwin_arm64
```

## Registering a new model (operator workflow — air-gapped)

```bash
# ── On internet-connected machine ──────────────────────────────────────────
# 1. Download model
wget https://huggingface.co/.../Qwen2.5-32B-Instruct-Q4_K_M.gguf

# 2. Compute sha256
sha256sum Qwen2.5-32B-Instruct-Q4_K_M.gguf
# → abc123... Qwen2.5-32B-Instruct-Q4_K_M.gguf

# ── Transfer to cluster (USB or internal NAS) ────────────────────────────
scp Qwen2.5-32B-Instruct-Q4_K_M.gguf dk@192.168.10.12:/tmp/

# ── On cluster (hydra-store-01) ──────────────────────────────────────────
# 3. Upload to MinIO
mc alias set local http://localhost:9000 hydra-admin changeme-v2
mc cp /tmp/Qwen2.5-32B-Instruct-Q4_K_M.gguf local/hydra-models/gguf/

# ── Via Registry API ─────────────────────────────────────────────────────
# 4. Register
curl -X POST http://192.168.10.12:8100/api/v1/models/register \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "qwen25-32b-q4",
    "version": "1.0",
    "format": "GGUF",
    "quantization": "Q4_K_M",
    "size_bytes": 20000000000,
    "sha256": "abc123...",
    "minio_path": "gguf/Qwen2.5-32B-Instruct-Q4_K_M.gguf",
    "pool_assignment": "largepool"
  }'

# 5. Approve for deployment
curl -X POST http://192.168.10.12:8100/api/v1/models/qwen25-32b-q4/approve \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 6. List available models
curl http://192.168.10.12:8100/api/v1/models | python3 -m json.tool
```

## Deploy

```bash
cd ansible
ansible-playbook site.yml --tags model-registry --limit hydra-store-01
```
