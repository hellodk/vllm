# llm-exo/files — Offline Wheel Prep

## Required directory

```
ansible/roles/llm-exo/files/wheels/   ← Python wheels for exo + deps
```

## Download (on a Mac with internet)

```bash
# grpcio needs a source wheel for arm64 — build it first:
pip download grpcio grpcio-tools \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  -d ansible/roles/llm-exo/files/wheels/ 2>/dev/null || \
pip download grpcio grpcio-tools \
  -d ansible/roles/llm-exo/files/wheels/

# Then download the rest:
pip download \
  "exo-explore>=0.0.1" \
  "mlx>=0.16.0" "mlx-lm>=0.19.0" \
  "protobuf>=4.25.0" "numpy>=1.26.0" \
  "huggingface-hub>=0.23.0" "transformers>=4.41.0" \
  --platform macosx_14_0_arm64 \
  --python-version 311 \
  --only-binary=:all: \
  -d ansible/roles/llm-exo/files/wheels/
```

## Peer topology (large_pool)

Each large_pool host has `exo_peers` set in inventory pointing to its partner:

```
hydra-large-01 ←→ hydra-large-02   (Pair A — 192.168.10.31/32)
hydra-large-03 ←→ hydra-large-04   (Pair B — 192.168.10.33/34)
```

exo uses the 10 GbE interface for tensor-parallel communication (~2 μs AllReduce via RDMA/optimised TCP).
