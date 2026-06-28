base:
  # Experiment node: single MLX node running mlx_lm.server + perf-proxy sidecar
  'hydra-exp-01':
    - llm-common
    - llm-mlx
    - monitoring-common

  # Large-pool tensor-parallel pair: exo P2P + AllReduce probe on every member
  'hydra-large-*':
    - llm-common
    - llm-exo
    - mlx-allreduce-probe
    - monitoring-common
