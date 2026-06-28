# salt/states/monitoring-common/init.sls
# Parallel of ansible/roles/llm-discovery + ansible/roles/monitoring-common.
# Renders /etc/hydra/discovered.yml (provider registry for the OTEL agent) and
# the OTEL agent config focused on the LLM/perf/fabric scrape path.
#
# NOTE: This state covers the LLM-scrape pipeline. Host-metrics, apple-silicon-
# exporter, and RDMA scraper setup are handled by the otel-mac-agent Ansible role
# (outside the scope of this Salt mirror). See README.md for the omission table.
{%- set llm_base_dir      = pillar.get('llm_base_dir', '/opt/hydra/llm') %}
{%- set mlx_backend       = pillar.get('hydra_mlx_backend', 'vllm_mlx') %}
{%- set mlx_sidecar       = mlx_backend != 'vllm_mlx' %}
{%- set frameworks        = pillar.get('llm_frameworks', []) %}
{%- set mlx_on_node       = 'mlx' in frameworks or 'vllm-mlx' in frameworks %}
{%- set exo_on_node       = 'exo' in frameworks %}
{%- set allreduce_enabled = pillar.get('mlx_allreduce_enabled', False) %}

# ── /etc/hydra directory ─────────────────────────────────────────────────────
hydra-etc-dir:
  file.directory:
    - name: /etc/hydra
    - mode: "0755"
    - makedirs: True

# ── /etc/hydra/discovered.yml ─────────────────────────────────────────────────
# Written by llm-discovery in Ansible; here it is a Salt-rendered template.
discovered-yml:
  file.managed:
    - name: /etc/hydra/discovered.yml
    - source: salt://monitoring-common/discovered.yml.j2
    - template: jinja
    - mode: "0640"
    - user: root
    - group: wheel
    - require:
      - file: hydra-etc-dir

# ── OTEL agent config (LLM-scrape path) ──────────────────────────────────────
otel-agent-config:
  file.managed:
    - name: /etc/hydra/otel-agent-config.yaml
    - source: salt://monitoring-common/otel-agent-config.yaml.j2
    - template: jinja
    - mode: "0644"
    - user: root
    - group: wheel
    - require:
      - file: discovered-yml

# Reload the OTEL agent when the config changes (assumes it is already running).
# The service label matches the launchd daemon deployed by otel-mac-agent role.
otel-agent-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.otel-agent 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.otel-agent.plist 2>/dev/null || true
    - onchanges:
      - file: otel-agent-config
