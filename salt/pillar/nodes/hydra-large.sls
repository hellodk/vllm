# salt/pillar/nodes/hydra-large.sls
# Per-node overrides for large_pool tensor-parallel pair (exo P2P).
# Applied to both hydra-large-01 and hydra-large-02 via glob in pillar/top.sls.
# For peer-specific settings (exo_peers), override in per-minion pillar files or
# set exo_peers in host grains.

hydra_gpu_provider: apple
mc_chip: "M2 Ultra"
mc_pool: largepool
hydra_pools:
  - largepool
llm_frameworks:
  - exo

# Tensor-parallel: both nodes participate in AllReduce probe (leader = large-01)
mlx_allreduce_enabled: true

# exo 32B model for tensor-parallel pair
exo_default_model: mlx-community/Qwen2.5-32B-Instruct-4bit
exo_model_local_path: /opt/hydra/models/mlx/Qwen2.5-32B-Instruct-4bit

# exo sidecar always on (exo has no native Prometheus metrics)
exo_sidecar_enabled: true

# NOTE: Set exo_peers per-minion since each node has a different peer:
#   hydra-large-01 → exo_peers: ["192.168.10.41"]
#   hydra-large-02 → exo_peers: ["192.168.10.40"]
# Add per-node pillar files or use grains for this.
