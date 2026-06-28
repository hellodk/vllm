# salt/states/llm-exo/init.sls
# Parallel of ansible/roles/llm-exo — deploys exo P2P distributed inference.
# Always enables the perf-proxy sidecar (exo has no native Prometheus metrics).
{%- set llm_base_dir     = pillar.get('llm_base_dir', '/opt/hydra/llm') %}
{%- set llm_log_dir      = pillar.get('llm_log_dir',  '/var/log/hydra') %}
{%- set exo_dir          = pillar.get('exo_dir',  llm_base_dir ~ '/exo') %}
{%- set exo_venv         = pillar.get('exo_venv', exo_dir ~ '/venv') %}
{%- set hb_prefix        = pillar.get('homebrew_prefix', '/opt/homebrew') %}
{%- set pyver            = pillar.get('python_version', '3.11') %}
{%- set brew_python      = hb_prefix ~ '/opt/python@' ~ pyver ~ '/bin/python' ~ pyver %}
{%- set exo_model_path   = pillar.get('exo_model_local_path', '/opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit') %}
{%- set exo_pkgs         = pillar.get('exo_packages', []) %}
{%- set sidecar_active   = pillar.get('exo_sidecar_enabled', True) %}

# ── Directories ───────────────────────────────────────────────────────────────
exo-dir:
  file.directory:
    - name: {{ exo_dir }}
    - mode: "0755"
    - makedirs: True

# ── Model directory preflight check ─────────────────────────────────────────
exo-model-check:
  file.exists:
    - name: {{ exo_model_path }}
    - failhard: True
    - require:
      - file: exo-dir

# ── Virtual environment ───────────────────────────────────────────────────────
exo-venv:
  cmd.run:
    - name: {{ brew_python }} -m venv {{ exo_venv }}
    - unless: test -f {{ exo_venv }}/bin/python
    - require:
      - file: exo-dir

# ── Python packages (offline wheels only — exo grpcio needs arm64 source) ─────
exo-pip:
  pip.installed:
    - pkgs: {{ exo_pkgs | tojson }}
    - bin_env: {{ exo_venv }}
    - extra_args: --no-index --find-links {{ exo_dir }}/wheels
    - require:
      - cmd: exo-venv

# ── exo LaunchDaemon ─────────────────────────────────────────────────────────
exo-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.exo.plist
    - source: salt://llm-exo/com.hydra.exo.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - pip: exo-pip

exo-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.exo.plist
    - unless: launchctl list com.hydra.exo 2>/dev/null
    - require:
      - file: exo-plist

exo-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.exo 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.exo.plist
    - onchanges:
      - file: exo-plist

# ── Perf-proxy sidecar ────────────────────────────────────────────────────────
{% if sidecar_active %}
exo-perf-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.exo-perf.plist
    - source: salt://llm-exo/com.hydra.exo-perf.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - pip: exo-pip

exo-perf-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.exo-perf.plist
    - unless: launchctl list com.hydra.exo-perf 2>/dev/null
    - require:
      - file: exo-perf-plist

exo-perf-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.exo-perf 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.exo-perf.plist
    - onchanges:
      - file: exo-perf-plist
{% endif %}
