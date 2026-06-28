# salt/states/mlx-allreduce-probe/init.sls
# Parallel of ansible/roles/mlx-allreduce-probe.
# Deploys a periodic mlx.core.distributed AllReduce benchmark across each
# tensor_parallel group + an always-on textfile exporter the OTEL agent scrapes.
#
# Every group member gets the scripts + fabric-exporter daemon.
# The periodic probe LaunchDaemon is installed only on the group leader
# (first member in tensor_parallel_groups[<group>]).
{%- set llm_base_dir      = pillar.get('llm_base_dir', '/opt/hydra/llm') %}
{%- set llm_log_dir       = pillar.get('llm_log_dir',  '/var/log/hydra') %}
{%- set allreduce_dir     = pillar.get('allreduce_dir', llm_base_dir ~ '/allreduce') %}
{%- set tf_dir            = pillar.get('allreduce_textfile_dir', '/var/lib/hydra/textfile') %}
{%- set tf_file           = pillar.get('allreduce_textfile', tf_dir ~ '/mlx_allreduce.prom') %}
{%- set runner            = pillar.get('allreduce_runner', allreduce_dir ~ '/run-allreduce-probe.sh') %}
{%- set allreduce_enabled = pillar.get('mlx_allreduce_enabled', False) %}

# Resolve this minion's tensor-parallel group membership
{%- set minion_id   = grains.get('id', '') %}
{%- set tp_groups   = pillar.get('tensor_parallel_groups', {}) %}
{%- set my_group    = '' %}
{%- set group_members = [] %}
{%- set is_leader   = False %}
{%- set peer_hosts  = [] %}
{%- for gname, members in tp_groups.items() %}
{%-   if minion_id in members %}
{%-     set my_group = gname %}
{%-     set group_members = members %}
{%-     set is_leader = (members[0] == minion_id) %}
{%-   endif %}
{%- endfor %}

{% if not allreduce_enabled or my_group == '' %}
# AllReduce probe skipped: either mlx_allreduce_enabled is false or this node
# is not in any tensor_parallel_groups entry. Nothing to deploy.
allreduce-noop:
  test.nop: []

{% else %}

# ── Directories ───────────────────────────────────────────────────────────────
allreduce-dirs:
  file.directory:
    - names:
      - {{ allreduce_dir }}
      - {{ tf_dir }}
    - mode: "0755"
    - makedirs: True

# ── Scripts (exact copies of the Ansible files/) ──────────────────────────────
allreduce-probe-script:
  file.managed:
    - name: {{ allreduce_dir }}/mlx_allreduce_probe.py
    - source: salt://mlx-allreduce-probe/files/mlx_allreduce_probe.py
    - mode: "0755"
    - require:
      - file: allreduce-dirs

allreduce-fabric-exporter-script:
  file.managed:
    - name: {{ allreduce_dir }}/mlx_fabric_exporter.py
    - source: salt://mlx-allreduce-probe/files/mlx_fabric_exporter.py
    - mode: "0755"
    - require:
      - file: allreduce-dirs

# ── Always-on fabric exporter (every group member) ───────────────────────────
allreduce-fabric-exporter-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.mlx-fabric-exporter.plist
    - source: salt://mlx-allreduce-probe/com.hydra.mlx-fabric-exporter.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - file: allreduce-fabric-exporter-script

allreduce-fabric-exporter-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-fabric-exporter.plist
    - unless: launchctl list com.hydra.mlx-fabric-exporter 2>/dev/null
    - require:
      - file: allreduce-fabric-exporter-plist

allreduce-fabric-exporter-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-fabric-exporter 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-fabric-exporter.plist
    - onchanges:
      - file: allreduce-fabric-exporter-plist

# ── Periodic probe (group leader only) ───────────────────────────────────────
{% if is_leader %}
allreduce-runner-script:
  file.managed:
    - name: {{ runner }}
    - source: salt://mlx-allreduce-probe/run-allreduce-probe.sh.j2
    - template: jinja
    - mode: "0755"
    - require:
      - file: allreduce-dirs

allreduce-probe-plist:
  file.managed:
    - name: /Library/LaunchDaemons/com.hydra.mlx-allreduce.plist
    - source: salt://mlx-allreduce-probe/com.hydra.mlx-allreduce.plist.j2
    - template: jinja
    - user: root
    - group: wheel
    - mode: "0644"
    - require:
      - file: allreduce-runner-script

allreduce-probe-bootstrap:
  cmd.run:
    - name: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-allreduce.plist
    - unless: launchctl list com.hydra.mlx-allreduce 2>/dev/null
    - require:
      - file: allreduce-probe-plist

allreduce-probe-reload:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-allreduce 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-allreduce.plist
    - onchanges:
      - file: allreduce-probe-plist
{% endif %}

{% endif %}
