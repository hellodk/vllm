base:
  # Global defaults applied to every node
  '*':
    - llm

  # Experiment node — single MLX node
  'hydra-exp-01':
    - nodes.hydra-exp-01

  # Large-pool tensor-parallel pair (exo P2P distributed inference)
  'hydra-large-*':
    - nodes.hydra-large
