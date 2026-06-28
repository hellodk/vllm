# salt/states/llm-common/init.sls
# Parallel of ansible/roles/llm-common — applied to every LLM node first.
# Creates the base directory tree, installs the shared perf-proxy exporter,
# checks that Homebrew Python is present, and deploys the log-rotation config.
{%- set llm_base_dir = pillar.get('llm_base_dir', '/opt/hydra/llm') %}
{%- set llm_log_dir  = pillar.get('llm_log_dir',  '/var/log/hydra') %}
{%- set llm_bin_dir  = pillar.get('llm_bin_dir',  llm_base_dir ~ '/bin') %}
{%- set perf_proxy   = pillar.get('llm_perf_proxy_path', llm_bin_dir ~ '/llm_perf_proxy.py') %}
{%- set hb_prefix    = pillar.get('homebrew_prefix', '/opt/homebrew') %}
{%- set pyver        = pillar.get('python_version', '3.11') %}
{%- set brew_python  = hb_prefix ~ '/opt/python@' ~ pyver ~ '/bin/python' ~ pyver %}

# ── Base directory tree ───────────────────────────────────────────────────────
llm-common-dirs:
  file.directory:
    - names:
      - {{ llm_base_dir }}
      - {{ llm_bin_dir }}
      - {{ llm_log_dir }}
      - /var/log/hydra
    - mode: "0755"
    - makedirs: True

# ── Shared perf-proxy exporter (used by both mlx and exo roles) ───────────────
llm-perf-proxy:
  file.managed:
    - name: {{ perf_proxy }}
    - source: salt://llm-common/files/llm_perf_proxy.py
    - mode: "0755"
    - makedirs: True
    - require:
      - file: llm-common-dirs

# ── Homebrew Python presence check ───────────────────────────────────────────
# Mirrors the Ansible stat + fail tasks. Salt will hard-fail the highstate if
# Python is missing; the message guides the operator.
llm-brew-python-check:
  file.exists:
    - name: {{ brew_python }}
    - require:
      - file: llm-common-dirs
    - failhard: True

# ── macOS log rotation (newsyslog) ───────────────────────────────────────────
llm-logrotate:
  file.managed:
    - name: /etc/newsyslog.d/hydra.conf
    - source: salt://llm-common/files/hydra-logrotate.conf
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - file: llm-common-dirs
