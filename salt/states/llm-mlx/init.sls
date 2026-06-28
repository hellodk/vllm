# salt/states/llm-mlx/init.sls
# Parallel of ansible/roles/llm-mlx — deploys Apple MLX inference server.
# Backend selector (hydra_mlx_backend pillar) mirrors the Ansible logic exactly:
#   vllm_mlx  → vllm-mlx binary, native vllm:* metrics on port 11500 (no sidecar)
#   mlx_lm    → pure mlx_lm.server, perf-proxy sidecar on 11501, engine on 11510
{%- set llm_base_dir = pillar.get('llm_base_dir', '/opt/hydra/llm') %}
{%- set llm_log_dir  = pillar.get('llm_log_dir',  '/var/log/hydra') %}
{%- set mlx_dir      = pillar.get('mlx_dir',  llm_base_dir ~ '/mlx') %}
{%- set mlx_venv     = pillar.get('mlx_venv', mlx_dir ~ '/venv') %}
{%- set hb_prefix    = pillar.get('homebrew_prefix', '/opt/homebrew') %}
{%- set pyver        = pillar.get('python_version', '3.11') %}
{%- set brew_python  = hb_prefix ~ '/opt/python@' ~ pyver ~ '/bin/python' ~ pyver %}
{%- set mlx_backend  = pillar.get('hydra_mlx_backend', 'vllm_mlx') %}
{%- set mlx_sidecar  = mlx_backend != 'vllm_mlx' %}
{%- set use_jfrog    = pillar.get('mlx_use_jfrog', True) %}
{%- set jfrog_idx    = pillar.get('jfrog_pypi_index', '') %}
{%- set jfrog_host   = pillar.get('jfrog_pypi_trusted_host', '') %}
{%- set mlx_pkgs     = pillar.get('mlx_packages', []) %}
{%- set vllm_pkgs    = pillar.get('mlx_vllm_packages', []) %}
{%- set tel_pkgs     = pillar.get('mlx_telemetry_packages', []) %}
{%- set mlx_model    = pillar.get('mlx_default_model', '/opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit') %}

# ── Directories ───────────────────────────────────────────────────────────────
mlx-dir:
  file.directory:
    - name: {{ mlx_dir }}
    - mode: "0755"
    - makedirs: True

# ── Model directory preflight check ─────────────────────────────────────────
mlx-model-check:
  file.exists:
    - name: {{ mlx_model }}
    - failhard: True
    - require:
      - file: mlx-dir

# ── Virtual environment ───────────────────────────────────────────────────────
mlx-venv:
  cmd.run:
    - name: {{ brew_python }} -m venv {{ mlx_venv }}
    - unless: test -f {{ mlx_venv }}/bin/python
    - require:
      - file: mlx-dir

# ── Python packages (JFrog Artifactory primary path) ─────────────────────────
{% if use_jfrog %}
mlx-pip-core:
  pip.installed:
    - pkgs: {{ mlx_pkgs | tojson }}
    - bin_env: {{ mlx_venv }}
    - extra_args: >-
        --index-url {{ jfrog_idx }}
        --trusted-host {{ jfrog_host }}
    - require:
      - cmd: mlx-venv

mlx-pip-telemetry:
  pip.installed:
    - pkgs: {{ tel_pkgs | tojson }}
    - bin_env: {{ mlx_venv }}
    - extra_args: >-
        --index-url {{ jfrog_idx }}
        --trusted-host {{ jfrog_host }}
    - require:
      - pip: mlx-pip-core

{% if mlx_backend == 'vllm_mlx' %}
mlx-pip-vllm:
  pip.installed:
    - pkgs: {{ vllm_pkgs | tojson }}
    - bin_env: {{ mlx_venv }}
    - extra_args: >-
        --index-url {{ jfrog_idx }}
        --trusted-host {{ jfrog_host }}
    - require:
      - pip: mlx-pip-core
{% endif %}

{% else %}
# ── Offline wheels fallback ───────────────────────────────────────────────────
mlx-pip-core:
  pip.installed:
    - pkgs: {{ mlx_pkgs | tojson }}
    - bin_env: {{ mlx_venv }}
    - extra_args: --no-index --find-links {{ mlx_dir }}/wheels
    - require:
      - cmd: mlx-venv

mlx-pip-telemetry:
  pip.installed:
    - pkgs: {{ tel_pkgs | tojson }}
    - bin_env: {{ mlx_venv }}
    - extra_args: --no-index --find-links {{ mlx_dir }}/wheels
    - require:
      - pip: mlx-pip-core
{% endif %}

# ── mlx-server LaunchDaemon ───────────────────────────────────────────────────
mlx-server-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.mlx-server.plist
    - source: salt://llm-mlx/com.hydra.mlx-server.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - pip: mlx-pip-core

# First-time bootstrap (idempotent: skipped if already loaded)
mlx-server-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-server.plist
    - unless: launchctl list com.hydra.mlx-server 2>/dev/null
    - require:
      - file: mlx-server-plist

# Reload on plist change (mirrors Ansible restart mlx-server handler)
mlx-server-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-server 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-server.plist
    - onchanges:
      - file: mlx-server-plist

# ── Log exporter LaunchDaemon ─────────────────────────────────────────────────
mlx-log-exporter-script:
  file.managed:
    - name: {{ mlx_dir }}/mlx_log_exporter.py
    - source: salt://llm-mlx/mlx_log_exporter.py
    - mode: "0755"
    - require:
      - file: mlx-dir

mlx-logexporter-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist
    - source: salt://llm-mlx/com.hydra.mlx-logexporter.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - file: mlx-log-exporter-script

mlx-logexporter-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist
    - unless: launchctl list com.hydra.mlx-logexporter 2>/dev/null
    - require:
      - file: mlx-logexporter-plist

mlx-logexporter-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-logexporter 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist
    - onchanges:
      - file: mlx-logexporter-plist

# ── Perf-proxy sidecar (pure-MLX backends only) ───────────────────────────────
{% if mlx_sidecar %}
mlx-perf-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.mlx-perf.plist
    - source: salt://llm-mlx/com.hydra.mlx-perf.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - pip: mlx-pip-telemetry

mlx-perf-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-perf.plist
    - unless: launchctl list com.hydra.mlx-perf 2>/dev/null
    - require:
      - file: mlx-perf-plist

mlx-perf-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-perf 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-perf.plist
    - onchanges:
      - file: mlx-perf-plist

{% else %}
# vllm_mlx serves native metrics — remove the sidecar plist if it exists
mlx-perf-bootout:
  cmd.run:
    - name: launchctl bootout system/com.hydra.mlx-perf 2>/dev/null || true
    - onlyif: launchctl list com.hydra.mlx-perf 2>/dev/null

mlx-perf-plist-absent:
  file.absent:
    - name: /Library/LaunchDaemons/com.hydra.mlx-perf.plist
    - require:
      - cmd: mlx-perf-bootout
{% endif %}
