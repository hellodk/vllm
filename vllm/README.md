# vLLM

vLLM inference configuration for the Hydra cluster. Uses **vLLM-MLX** (Apple Silicon backend) and standard **vLLM** (NVIDIA/Linux backend).

## Contents

- `vllm.code-workspace` — VS Code workspace for vLLM development

## Deployment

vLLM is deployed per-node by the ansible `monitoring-cfg` role. See `../ansible/` for the playbooks and `../ansible/cluster.yml` to configure which nodes run vLLM.

## Backends in use

| Node type | Backend | Runtime |
|-----------|---------|---------|
| Apple Silicon (M1/M2/M3/M4) | vLLM-MLX | Metal GPU via MLX |
| NVIDIA Linux | vLLM | CUDA |
| CPU-only | llama.cpp | CPU (vLLM not used) |

## Key configuration

- OTEL metrics exported at `:8000/metrics` (same port as inference server)
- OpenAI-compatible API at `:8000/v1`
- Prometheus scrape configured automatically via `ansible/roles/monitoring-cfg`

## Comparison with llama.cpp

| Feature | vLLM-MLX | llama.cpp |
|---------|----------|-----------|
| Continuous batching | ✓ | ✗ |
| PagedAttention | ✓ (MLX port) | ✗ |
| Metal GPU | ✓ | ✓ |
| CUDA | ✗ | ✓ |
| Best for | Production serving | Benchmarking + dev |

## References

- [vLLM project](https://github.com/vllm-project/vllm)
- [vLLM-MLX fork](https://github.com/ml-explore/mlx-lm)
- See `../benchmarkings.md` for vLLM-MLX vs llama.cpp benchmark results
- See `../commands.md` for deployment commands
