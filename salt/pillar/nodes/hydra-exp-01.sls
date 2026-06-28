# salt/pillar/nodes/hydra-exp-01.sls
# Per-node overrides for 192.168.1.23 — experiment box running pure mlx_lm.server.
# Mirrors: set `hydra_mlx_backend: mlx_lm` in Ansible inventory host_vars.

hydra_node_id: hydra-exp-01
hydra_hostname: hydra-exp-01.hydra.local
mc_chip: "M4 Pro"
mc_pool: fast
hydra_pools:
  - fast
llm_frameworks:
  - mlx

# Backend: pure mlx_lm.server — enables the perf-proxy sidecar on port 11501.
hydra_mlx_backend: mlx_lm

# Model: per task spec
mlx_default_model: /opt/hydra/models/mlx/Qwen3.5-4B-MLX-8bit
