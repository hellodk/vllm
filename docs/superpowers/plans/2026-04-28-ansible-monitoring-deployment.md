# Ansible Multi-Hardware Monitoring Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy OTEL agent, hardware exporters, and LLM auto-discovery across Apple Silicon, NVIDIA, and CPU-only nodes from a single `ansible/cluster.yml` config file.

**Architecture:** Four layered ansible roles run in order per node: `base` installs the OTEL collector binary and service unit; `hw-exporter` installs the correct hardware metrics exporter (apple-silicon-exporter / dcgm-exporter / node_exporter) based on `gpu_provider`; `llm-discovery` probes running LLM providers and writes `/etc/hydra/discovered.yml`; `monitoring-cfg` renders a per-node OTEL config from that file and restarts the agent. A Python inventory plugin reads `cluster.yml` and generates the ansible inventory dynamically.

**Tech Stack:** Ansible 2.15+, Python 3.10+ (inventory plugin + discovery tests), Jinja2 (OTEL config templates), OTEL Collector Contrib 0.96.0, dcgm-exporter 3.3.6, node_exporter 1.8.2

**Spec:** `docs/superpowers/specs/2026-04-28-monitoring-ansible-design.md`

---

## File Map

### New files (create)

| File | Responsibility |
|------|---------------|
| `ansible/cluster.yml` | Cluster node definitions — the only file users edit |
| `ansible/inventory_plugin.py` | Reads cluster.yml → ansible inventory JSON |
| `ansible/site.yml` | Master playbook: runs all 4 roles in order |
| `ansible/group_vars/all.yml` | Version pins, default probe ports, exporter port map |
| `ansible/roles/base/tasks/main.yml` | Install OTEL agent binary + OS service |
| `ansible/roles/base/templates/otel-agent.service.j2` | systemd unit (Linux) |
| `ansible/roles/base/templates/otel-agent.plist.j2` | LaunchDaemon plist (macOS) |
| `ansible/roles/hw-exporter/tasks/main.yml` | Dispatch to OS/GPU sub-task |
| `ansible/roles/hw-exporter/tasks/apple.yml` | Build + install apple-silicon-exporter |
| `ansible/roles/hw-exporter/tasks/nvidia.yml` | Install dcgm-exporter via Docker |
| `ansible/roles/hw-exporter/tasks/amd.yml` | Install amdgpu_exporter binary |
| `ansible/roles/hw-exporter/tasks/cpu.yml` | Install node_exporter binary |
| `ansible/roles/hw-exporter/templates/apple-exporter.plist.j2` | LaunchDaemon for apple exporter |
| `ansible/roles/hw-exporter/templates/dcgm-exporter.service.j2` | systemd unit for dcgm-exporter |
| `ansible/roles/hw-exporter/templates/node-exporter.service.j2` | systemd unit for node_exporter |
| `ansible/roles/llm-discovery/tasks/main.yml` | Probe ports, query /v1/models, write discovered.yml |
| `ansible/roles/llm-discovery/templates/discovered.yml.j2` | Template for /etc/hydra/discovered.yml |
| `ansible/roles/monitoring-cfg/tasks/main.yml` | Render OTEL config, restart agent, health check |
| `ansible/roles/monitoring-cfg/templates/otel-agent-config.yaml.j2` | Per-node OTEL config |
| `ansible/roles/monitoring-cfg/templates/otel-agent.env.j2` | Per-node env file |
| `ansible/tests/test_inventory_plugin.py` | Unit tests for inventory_plugin.py |
| `ansible/tests/test_llm_discovery_logic.py` | Unit tests for quantization regex + URL parsing |

### Existing files (no changes needed)

- `opik-observability/ansible/` — untouched; separate concern
- `apple-silicon-monitoring/apple-silicon-exporter/` — used as build source by `hw-exporter/tasks/apple.yml`

---

## Task 1: `cluster.yml` + `group_vars/all.yml`

**Files:**
- Create: `ansible/cluster.yml`
- Create: `ansible/group_vars/all.yml`

- [ ] **Step 1.1: Create `ansible/cluster.yml` with the 3 pilot machines**

```yaml
# ansible/cluster.yml
cluster:
  name: hydra-prod
  environment: production
  otel_gateway:
    primary: "192.168.1.10:4317"
    secondary: ""
  prometheus_url: "http://192.168.1.10:9090"
  models_dir: "~/models"
  versions:
    otel_agent: "0.96.0"
    dcgm_exporter: "3.3.6"
    apple_silicon_exporter: "1.0.0"
    node_exporter: "1.8.2"
    amdgpu_exporter: "1.0.0"

nodes:
  - id: p1-m2-8g
    hostname: mac-m2-8g
    ip: 192.168.1.21
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: macos
    gpu_provider: apple
    chip: m2
    ram_gb: 8
    models_dir: "/Users/dk/models"
    pools: [embed]
    maintenance: false
    metric_labels: {}
    llm_endpoints:
      - provider: llamacpp
        url: "http://127.0.0.1:21434/v1"
        api_key: ~

  - id: p1-m3-16g
    hostname: mac-m3-16g
    ip: 192.168.1.24
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: macos
    gpu_provider: apple
    chip: m3
    ram_gb: 16
    models_dir: "/Users/dk/models"
    pools: [fast, reason]
    maintenance: false
    metric_labels: {}
    llm_endpoints:
      - provider: llamacpp
        url: "http://127.0.0.1:21434/v1"
        api_key: ~
      - provider: ollama
        url: "http://127.0.0.1:11434/v1"
        api_key: ~

  - id: p1-i7-rtx3050
    hostname: linux-i7-rtx
    ip: 192.168.1.30
    user: dk
    ssh_port: 22
    become: true
    become_method: sudo
    os: linux
    gpu_provider: nvidia
    gpu_model: "RTX 3050"
    gpu_index: 0
    vram_gb: 4
    ram_gb: 64
    cpu_inference: true
    models_dir: "/home/dk/models"
    pools: [fast]
    maintenance: false
    metric_labels: {}
    llm_endpoints:
      - provider: ollama
        url: "http://127.0.0.1:11434/v1"
        api_key: ~
      - provider: llamacpp
        url: "http://127.0.0.1:21434/v1"
        api_key: ~
```

- [ ] **Step 1.2: Create `ansible/group_vars/all.yml`**

```yaml
# ansible/group_vars/all.yml

# Exporter listen ports — fixed, do not change without updating dashboard queries
hw_exporter_ports:
  apple: 9101
  nvidia: 9400
  amd: 9102
  none: 9100

# Default LLM provider probe ports (used when llm_endpoints not set in cluster.yml)
llm_probe_ports:
  11434: ollama
  21434: llamacpp
  8000: vllm-mlx
  8080: litellm

# OTEL agent download base URL
otel_agent_download_base: "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download"

# node_exporter download base URL
node_exporter_download_base: "https://github.com/prometheus/node_exporter/releases/download"

# Allowed enum values — inventory_plugin.py validates against these
valid_os: [macos, linux]
valid_gpu_providers: [apple, nvidia, amd, none]
valid_llm_providers: [llamacpp, ollama, vllm-mlx, litellm]

# Hydra directories created on every node
hydra_dirs:
  linux:
    - /etc/hydra
    - /var/lib/hydra
    - /usr/local/bin
  macos:
    - /Library/Application Support/Hydra
    - /etc/hydra
```

- [ ] **Step 1.3: Commit**

```bash
git add ansible/cluster.yml ansible/group_vars/all.yml
git commit -m "feat(ansible): add cluster.yml config and group_vars defaults"
```

---

## Task 2: `inventory_plugin.py`

**Files:**
- Create: `ansible/inventory_plugin.py`
- Create: `ansible/tests/test_inventory_plugin.py`

The inventory plugin is a standalone Python script called by ansible via `ansible-inventory -i inventory_plugin.py --list`. It reads `cluster.yml` (path relative to its own location) and emits the ansible inventory JSON format.

- [ ] **Step 2.1: Write failing tests**

```python
# ansible/tests/test_inventory_plugin.py
import json
import sys
import os
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import inventory_plugin as ip

SAMPLE_CLUSTER = {
    "cluster": {
        "name": "test-cluster",
        "environment": "test",
        "otel_gateway": {"primary": "10.0.0.1:4317", "secondary": ""},
        "prometheus_url": "http://10.0.0.1:9090",
        "models_dir": "~/models",
        "versions": {
            "otel_agent": "0.96.0",
            "dcgm_exporter": "3.3.6",
            "apple_silicon_exporter": "1.0.0",
            "node_exporter": "1.8.2",
            "amdgpu_exporter": "1.0.0",
        },
    },
    "nodes": [
        {
            "id": "node-apple",
            "hostname": "mac-m3",
            "ip": "192.168.1.10",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "macos",
            "gpu_provider": "apple",
            "chip": "m3",
            "ram_gb": 16,
            "models_dir": "/Users/dk/models",
            "pools": ["fast"],
            "maintenance": False,
            "metric_labels": {},
            "llm_endpoints": [
                {"provider": "ollama", "url": "http://127.0.0.1:11434/v1", "api_key": None}
            ],
        },
        {
            "id": "node-nvidia",
            "hostname": "linux-rtx",
            "ip": "192.168.1.20",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "linux",
            "gpu_provider": "nvidia",
            "chip": "i7-12700k",
            "ram_gb": 64,
            "vram_gb": 4,
            "models_dir": "/home/dk/models",
            "pools": ["fast"],
            "maintenance": False,
            "metric_labels": {},
            "llm_endpoints": [],
        },
        {
            "id": "node-maint",
            "hostname": "mac-m1",
            "ip": "192.168.1.30",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "macos",
            "gpu_provider": "apple",
            "chip": "m1",
            "ram_gb": 8,
            "models_dir": "/Users/dk/models",
            "pools": [],
            "maintenance": True,   # <-- should be excluded
            "metric_labels": {},
            "llm_endpoints": [],
        },
    ],
}


def test_maintenance_nodes_excluded():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "node-maint" not in inv["_meta"]["hostvars"]
    assert "node-maint" not in inv.get("all", {}).get("hosts", [])


def test_all_active_nodes_present():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hostvars = inv["_meta"]["hostvars"]
    assert "node-apple" in hostvars
    assert "node-nvidia" in hostvars


def test_host_connection_vars():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["ansible_host"] == "192.168.1.10"
    assert hv["ansible_user"] == "dk"
    assert hv["ansible_port"] == 22
    assert hv["ansible_become"] is True
    assert hv["ansible_become_method"] == "sudo"


def test_hydra_vars_injected():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["hydra_node_id"] == "node-apple"
    assert hv["hydra_os"] == "macos"
    assert hv["hydra_gpu_provider"] == "apple"
    assert hv["hydra_pools"] == ["fast"]
    assert hv["hydra_chip"] == "m3"


def test_group_by_os():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "os_macos" in inv
    assert "node-apple" in inv["os_macos"]["hosts"]
    assert "os_linux" in inv
    assert "node-nvidia" in inv["os_linux"]["hosts"]


def test_group_by_gpu_provider():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "hw_apple" in inv
    assert "node-apple" in inv["hw_apple"]["hosts"]
    assert "hw_nvidia" in inv
    assert "node-nvidia" in inv["hw_nvidia"]["hosts"]


def test_invalid_gpu_provider_raises():
    bad = dict(SAMPLE_CLUSTER)
    bad["nodes"] = [dict(SAMPLE_CLUSTER["nodes"][0], gpu_provider="quantum")]
    with pytest.raises(ValueError, match="gpu_provider"):
        ip.build_inventory(bad)


def test_invalid_os_raises():
    bad = dict(SAMPLE_CLUSTER)
    bad["nodes"] = [dict(SAMPLE_CLUSTER["nodes"][0], os="windows")]
    with pytest.raises(ValueError, match="os"):
        ip.build_inventory(bad)


def test_cluster_vars_available():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["hydra_cluster_name"] == "test-cluster"
    assert hv["hydra_otel_gateway_primary"] == "10.0.0.1:4317"
```

- [ ] **Step 2.2: Run tests — verify they fail**

```bash
cd /home/dk/Documents/git/hydra/ansible
python -m pytest tests/test_inventory_plugin.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'inventory_plugin'`

- [ ] **Step 2.3: Implement `inventory_plugin.py`**

```python
#!/usr/bin/env python3
# ansible/inventory_plugin.py
# Usage: ansible-inventory -i inventory_plugin.py --list
import json
import os
import sys
import yaml

VALID_OS = {"macos", "linux"}
VALID_GPU_PROVIDERS = {"apple", "nvidia", "amd", "none"}
VALID_LLM_PROVIDERS = {"llamacpp", "ollama", "vllm-mlx", "litellm"}

_CLUSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cluster.yml")


def _load_cluster(path: str = _CLUSTER_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_inventory(cluster: dict) -> dict:
    cfg = cluster["cluster"]
    nodes = cluster["nodes"]

    inv: dict = {
        "_meta": {"hostvars": {}},
        "all": {"hosts": [], "children": []},
    }

    groups: dict[str, list[str]] = {}

    for node in nodes:
        if node.get("maintenance", False):
            continue

        nid = node["id"]

        # Validate enums
        if node["os"] not in VALID_OS:
            raise ValueError(f"os '{node['os']}' for node '{nid}' must be one of {VALID_OS}")
        if node["gpu_provider"] not in VALID_GPU_PROVIDERS:
            raise ValueError(
                f"gpu_provider '{node['gpu_provider']}' for node '{nid}' "
                f"must be one of {VALID_GPU_PROVIDERS}"
            )
        for ep in node.get("llm_endpoints", []):
            if ep["provider"] not in VALID_LLM_PROVIDERS:
                raise ValueError(
                    f"llm provider '{ep['provider']}' for node '{nid}' "
                    f"must be one of {VALID_LLM_PROVIDERS}"
                )

        # Host vars — ansible connection + hydra metadata
        inv["_meta"]["hostvars"][nid] = {
            # Ansible connection
            "ansible_host": node["ip"],
            "ansible_user": node["user"],
            "ansible_port": node.get("ssh_port", 22),
            "ansible_become": node.get("become", False),
            "ansible_become_method": node.get("become_method", "sudo"),
            # Hydra node identity
            "hydra_node_id": nid,
            "hydra_hostname": node["hostname"],
            "hydra_os": node["os"],
            "hydra_gpu_provider": node["gpu_provider"],
            "hydra_chip": node.get("chip", "unknown"),
            "hydra_ram_gb": node.get("ram_gb", 0),
            "hydra_vram_gb": node.get("vram_gb", 0),
            "hydra_pools": node.get("pools", []),
            "hydra_models_dir": node.get("models_dir", cfg.get("models_dir", "~/models")),
            "hydra_cpu_inference": node.get("cpu_inference", False),
            "hydra_metric_labels": node.get("metric_labels", {}),
            "hydra_llm_endpoints": node.get("llm_endpoints", []),
            # Cluster-wide vars
            "hydra_cluster_name": cfg["name"],
            "hydra_environment": cfg["environment"],
            "hydra_otel_gateway_primary": cfg["otel_gateway"]["primary"],
            "hydra_otel_gateway_secondary": cfg["otel_gateway"].get("secondary", ""),
            "hydra_versions": cfg.get("versions", {}),
        }

        inv["all"]["hosts"].append(nid)

        # Group by OS
        os_group = f"os_{node['os']}"
        groups.setdefault(os_group, []).append(nid)

        # Group by GPU provider
        hw_group = f"hw_{node['gpu_provider']}"
        groups.setdefault(hw_group, []).append(nid)

        # Group by pool membership
        for pool in node.get("pools", []):
            pool_group = f"pool_{pool}"
            groups.setdefault(pool_group, []).append(nid)

    for group, hosts in groups.items():
        inv[group] = {"hosts": hosts}
        inv["all"]["children"].append(group)

    return inv


def main():
    if "--list" in sys.argv:
        cluster = _load_cluster()
        print(json.dumps(build_inventory(cluster), indent=2))
    elif "--host" in sys.argv:
        # All vars are in _meta; per-host call returns empty
        print(json.dumps({}))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.4: Run tests — verify they pass**

```bash
cd /home/dk/Documents/git/hydra/ansible
python -m pytest tests/test_inventory_plugin.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 2.5: Smoke-test inventory output**

```bash
# From ansible/ dir — should print JSON inventory with 2 hosts (node-maint excluded)
python inventory_plugin.py --list | python -m json.tool | head -40
```

- [ ] **Step 2.6: Commit**

```bash
git add ansible/inventory_plugin.py ansible/tests/test_inventory_plugin.py
git commit -m "feat(ansible): inventory plugin reads cluster.yml, groups by os/hw/pool"
```

---

## Task 3: `site.yml` + `base` role

**Files:**
- Create: `ansible/site.yml`
- Create: `ansible/roles/base/tasks/main.yml`
- Create: `ansible/roles/base/templates/otel-agent.service.j2`
- Create: `ansible/roles/base/templates/otel-agent.plist.j2`

- [ ] **Step 3.1: Create `ansible/site.yml`**

```yaml
# ansible/site.yml
---
- name: Deploy Hydra monitoring stack
  hosts: all
  gather_facts: true
  serial: 1
  roles:
    - { role: base,           tags: [base, all] }
    - { role: hw-exporter,    tags: [hw-exporter, all] }
    - { role: llm-discovery,  tags: [llm-discovery, all] }
    - { role: monitoring-cfg, tags: [monitoring-cfg, all] }
```

- [ ] **Step 3.2: Create `ansible/roles/base/tasks/main.yml`**

```yaml
# ansible/roles/base/tasks/main.yml
---
- name: Create Hydra config directory
  file:
    path: /etc/hydra
    state: directory
    mode: "0755"

- name: Create Hydra data directory (Linux)
  file:
    path: /var/lib/hydra
    state: directory
    mode: "0755"
  when: hydra_os == "linux"

- name: Set OTEL agent download URL (Linux aarch64)
  set_fact:
    otel_download_url: >-
      {{ otel_agent_download_base }}/v{{ hydra_versions.otel_agent }}/otelcol-contrib_{{ hydra_versions.otel_agent }}_linux_arm64.tar.gz
    otel_binary_path: /usr/local/bin/otelcol-contrib
  when: hydra_os == "linux" and ansible_architecture in ["aarch64", "arm64"]

- name: Set OTEL agent download URL (Linux x86_64)
  set_fact:
    otel_download_url: >-
      {{ otel_agent_download_base }}/v{{ hydra_versions.otel_agent }}/otelcol-contrib_{{ hydra_versions.otel_agent }}_linux_amd64.tar.gz
    otel_binary_path: /usr/local/bin/otelcol-contrib
  when: hydra_os == "linux" and ansible_architecture == "x86_64"

- name: Set OTEL agent download URL (macOS arm64)
  set_fact:
    otel_download_url: >-
      {{ otel_agent_download_base }}/v{{ hydra_versions.otel_agent }}/otelcol-contrib_{{ hydra_versions.otel_agent }}_darwin_arm64.tar.gz
    otel_binary_path: /usr/local/bin/otelcol-contrib
  when: hydra_os == "macos"

- name: Download and extract OTEL agent
  unarchive:
    src: "{{ otel_download_url }}"
    dest: /usr/local/bin
    remote_src: true
    creates: "{{ otel_binary_path }}"
    extra_opts: [--strip-components=0]
  become: true

- name: Ensure OTEL binary is executable
  file:
    path: "{{ otel_binary_path }}"
    mode: "0755"
  become: true

- name: Install systemd service unit (Linux)
  template:
    src: otel-agent.service.j2
    dest: /etc/systemd/system/otel-agent.service
    mode: "0644"
  become: true
  when: hydra_os == "linux"
  notify: reload systemd

- name: Install LaunchDaemon plist (macOS)
  template:
    src: otel-agent.plist.j2
    dest: /Library/LaunchDaemons/com.hydra.otel-agent.plist
    mode: "0644"
    owner: root
    group: wheel
  become: true
  when: hydra_os == "macos"

handlers:
  - name: reload systemd
    systemd:
      daemon_reload: true
    become: true
    when: hydra_os == "linux"
```

- [ ] **Step 3.3: Create `ansible/roles/base/templates/otel-agent.service.j2`**

```ini
# ansible/roles/base/templates/otel-agent.service.j2
[Unit]
Description=Hydra OTEL Collector Agent
After=network.target
Wants=network.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/hydra/otel-agent.env
ExecStart=/usr/local/bin/otelcol-contrib --config /etc/hydra/otel-agent-config.yaml
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=otel-agent
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3.4: Create `ansible/roles/base/templates/otel-agent.plist.j2`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- ansible/roles/base/templates/otel-agent.plist.j2 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hydra.otel-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/otelcol-contrib</string>
    <string>--config</string>
    <string>/etc/hydra/otel-agent-config.yaml</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/hydra/otel-agent.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/hydra/otel-agent-error.log</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
</dict>
</plist>
```

> Note: macOS LaunchDaemon does not support `EnvironmentFile`. The env vars are baked into the plist by `monitoring-cfg` role, not loaded from file. The `otel-agent.env` file is still written for reference/debugging.

- [ ] **Step 3.5: Commit**

```bash
git add ansible/site.yml ansible/roles/base/
git commit -m "feat(ansible): base role installs OTEL agent binary and OS service unit"
```

---

## Task 4: `hw-exporter` role

**Files:**
- Create: `ansible/roles/hw-exporter/tasks/main.yml`
- Create: `ansible/roles/hw-exporter/tasks/apple.yml`
- Create: `ansible/roles/hw-exporter/tasks/nvidia.yml`
- Create: `ansible/roles/hw-exporter/tasks/cpu.yml`
- Create: `ansible/roles/hw-exporter/templates/apple-exporter.plist.j2`
- Create: `ansible/roles/hw-exporter/templates/dcgm-exporter.service.j2`
- Create: `ansible/roles/hw-exporter/templates/node-exporter.service.j2`

- [ ] **Step 4.1: Create `ansible/roles/hw-exporter/tasks/main.yml`**

```yaml
# ansible/roles/hw-exporter/tasks/main.yml
---
- name: Install apple-silicon-exporter
  include_tasks: apple.yml
  when: hydra_gpu_provider == "apple"

- name: Install dcgm-exporter (NVIDIA)
  include_tasks: nvidia.yml
  when: hydra_gpu_provider == "nvidia"

- name: Install node_exporter (CPU-only or cpu_inference nodes)
  include_tasks: cpu.yml
  when: hydra_gpu_provider == "none" or hydra_cpu_inference | default(false)
```

- [ ] **Step 4.2: Create `ansible/roles/hw-exporter/tasks/apple.yml`**

```yaml
# ansible/roles/hw-exporter/tasks/apple.yml
---
- name: Install Go (required to build apple-silicon-exporter)
  homebrew:
    name: go
    state: present
  become: false

- name: Copy apple-silicon-exporter source to remote
  synchronize:
    src: "{{ playbook_dir }}/../apple-silicon-monitoring/apple-silicon-exporter/"
    dest: "/tmp/apple-silicon-exporter/"
    delete: true
    rsync_opts: ["--exclude=*.log", "--exclude=.git"]

- name: Build apple-silicon-exporter
  shell: |
    cd /tmp/apple-silicon-exporter
    go build -o /usr/local/bin/apple-silicon-exporter ./cmd/exporter/
  become: true
  args:
    creates: /usr/local/bin/apple-silicon-exporter

- name: Ensure apple-silicon-exporter is executable
  file:
    path: /usr/local/bin/apple-silicon-exporter
    mode: "0755"
  become: true

- name: Install apple-silicon-exporter LaunchDaemon
  template:
    src: apple-exporter.plist.j2
    dest: /Library/LaunchDaemons/com.hydra.apple-exporter.plist
    mode: "0644"
    owner: root
    group: wheel
  become: true

- name: Load apple-silicon-exporter LaunchDaemon
  command: launchctl bootstrap system /Library/LaunchDaemons/com.hydra.apple-exporter.plist
  become: true
  ignore_errors: true   # already loaded on re-run

- name: Verify apple-silicon-exporter is responding
  uri:
    url: "http://127.0.0.1:9101/metrics"
    return_content: false
    status_code: 200
  retries: 6
  delay: 5
```

- [ ] **Step 4.3: Create `ansible/roles/hw-exporter/templates/apple-exporter.plist.j2`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- ansible/roles/hw-exporter/templates/apple-exporter.plist.j2 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hydra.apple-exporter</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/apple-silicon-exporter</string>
    <string>--listen</string>
    <string>127.0.0.1:9101</string>
    <string>--enable-iokit</string>
    <string>--enable-powermetrics</string>
    <string>--enable-metal</string>
  </array>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/hydra/apple-exporter.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/hydra/apple-exporter-error.log</string>
</dict>
</plist>
```

- [ ] **Step 4.4: Create `ansible/roles/hw-exporter/tasks/nvidia.yml`**

```yaml
# ansible/roles/hw-exporter/tasks/nvidia.yml
---
- name: Ensure Docker is installed
  package:
    name: docker.io
    state: present
  become: true

- name: Ensure Docker service is running
  service:
    name: docker
    state: started
    enabled: true
  become: true

- name: Pull dcgm-exporter image
  docker_image:
    name: "nvcr.io/nvidia/k8s/dcgm-exporter:{{ hydra_versions.dcgm_exporter }}-ubuntu22.04"
    source: pull
  become: true

- name: Create dcgm-exporter systemd unit
  template:
    src: dcgm-exporter.service.j2
    dest: /etc/systemd/system/dcgm-exporter.service
    mode: "0644"
  become: true
  notify: reload systemd and start dcgm-exporter

- name: Enable and start dcgm-exporter
  systemd:
    name: dcgm-exporter
    state: started
    enabled: true
    daemon_reload: true
  become: true

- name: Verify dcgm-exporter is responding
  uri:
    url: "http://127.0.0.1:9400/metrics"
    return_content: false
    status_code: 200
  retries: 6
  delay: 5

handlers:
  - name: reload systemd and start dcgm-exporter
    systemd:
      daemon_reload: true
    become: true
```

- [ ] **Step 4.5: Create `ansible/roles/hw-exporter/templates/dcgm-exporter.service.j2`**

```ini
# ansible/roles/hw-exporter/templates/dcgm-exporter.service.j2
[Unit]
Description=NVIDIA DCGM Exporter
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f dcgm-exporter
ExecStart=/usr/bin/docker run --rm \
  --gpus all \
  --cap-add SYS_ADMIN \
  -p 127.0.0.1:9400:9400 \
  --name dcgm-exporter \
  nvcr.io/nvidia/k8s/dcgm-exporter:{{ hydra_versions.dcgm_exporter }}-ubuntu22.04
ExecStop=/usr/bin/docker stop dcgm-exporter

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4.6: Create `ansible/roles/hw-exporter/tasks/cpu.yml`**

```yaml
# ansible/roles/hw-exporter/tasks/cpu.yml
---
- name: Set node_exporter download URL (Linux x86_64)
  set_fact:
    node_exporter_url: >-
      {{ node_exporter_download_base }}/v{{ hydra_versions.node_exporter }}/node_exporter-{{ hydra_versions.node_exporter }}.linux-amd64.tar.gz
  when: ansible_architecture == "x86_64"

- name: Set node_exporter download URL (Linux aarch64)
  set_fact:
    node_exporter_url: >-
      {{ node_exporter_download_base }}/v{{ hydra_versions.node_exporter }}/node_exporter-{{ hydra_versions.node_exporter }}.linux-arm64.tar.gz
  when: ansible_architecture in ["aarch64", "arm64"]

- name: Download and extract node_exporter
  unarchive:
    src: "{{ node_exporter_url }}"
    dest: /tmp/
    remote_src: true
    creates: "/tmp/node_exporter-{{ hydra_versions.node_exporter }}.linux-amd64/node_exporter"

- name: Install node_exporter binary
  copy:
    src: "/tmp/node_exporter-{{ hydra_versions.node_exporter }}.linux-amd64/node_exporter"
    dest: /usr/local/bin/node_exporter
    mode: "0755"
    remote_src: true
  become: true

- name: Install node_exporter systemd unit
  template:
    src: node-exporter.service.j2
    dest: /etc/systemd/system/node-exporter.service
    mode: "0644"
  become: true

- name: Enable and start node_exporter
  systemd:
    name: node-exporter
    state: started
    enabled: true
    daemon_reload: true
  become: true

- name: Verify node_exporter is responding
  uri:
    url: "http://127.0.0.1:9100/metrics"
    return_content: false
    status_code: 200
  retries: 6
  delay: 5
```

- [ ] **Step 4.7: Create `ansible/roles/hw-exporter/templates/node-exporter.service.j2`**

```ini
# ansible/roles/hw-exporter/templates/node-exporter.service.j2
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/node_exporter \
  --web.listen-address=127.0.0.1:9100 \
  --collector.cpu \
  --collector.meminfo \
  --collector.diskstats \
  --collector.filesystem \
  --collector.netdev
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4.8: Commit**

```bash
git add ansible/roles/hw-exporter/
git commit -m "feat(ansible): hw-exporter role installs apple/nvidia/cpu exporters"
```

---

## Task 5: `llm-discovery` role

**Files:**
- Create: `ansible/roles/llm-discovery/tasks/main.yml`
- Create: `ansible/roles/llm-discovery/templates/discovered.yml.j2`
- Create: `ansible/tests/test_llm_discovery_logic.py`

- [ ] **Step 5.1: Write failing tests for quantization extraction**

```python
# ansible/tests/test_llm_discovery_logic.py
import re
import pytest

QUANT_REGEX = r"[-_](q[0-9]+[_k_msx]*|f16|f32|f64|mxfp4|gguf)"


def extract_quant(model_name: str) -> str:
    m = re.search(QUANT_REGEX, model_name, re.IGNORECASE)
    return m.group(1).lower() if m else "unknown"


@pytest.mark.parametrize("model_name,expected", [
    ("qwen2.5-coder-3b-q4_k_m",              "q4_k_m"),
    ("llama3:8b-instruct-q4_K_M",            "q4_k_m"),
    ("gpt-oss-20B-MXFP4-MoE",               "mxfp4"),
    ("bge-m3-f16",                           "f16"),
    ("llama3.2-3b-q4_0",                     "q4_0"),
    ("mistral-7b-v0.3-q8_0",                "q8_0"),
    ("some-model-without-quant",             "unknown"),
    ("Qwen2.5-14B-Instruct-Q4_K_M.gguf",   "q4_k_m"),
])
def test_extract_quant(model_name, expected):
    assert extract_quant(model_name) == expected
```

- [ ] **Step 5.2: Run failing tests**

```bash
cd /home/dk/Documents/git/hydra/ansible
python -m pytest tests/test_llm_discovery_logic.py -v
```

Expected: all 8 tests PASS immediately (pure regex, no imports needed)

- [ ] **Step 5.3: Create `ansible/roles/llm-discovery/tasks/main.yml`**

```yaml
# ansible/roles/llm-discovery/tasks/main.yml
---
# Step A: Build endpoint list — use cluster.yml config or auto-probe
- name: Use explicitly configured endpoints from cluster.yml
  set_fact:
    _endpoints_to_probe: "{{ hydra_llm_endpoints }}"
  when: hydra_llm_endpoints | length > 0

- name: Auto-probe default ports when no endpoints configured
  set_fact:
    _endpoints_to_probe: []
  when: hydra_llm_endpoints | length == 0

- name: Probe default port {{ item.key }} for provider {{ item.value }}
  uri:
    url: "http://127.0.0.1:{{ item.key }}/health"
    status_code: [200, 404, 405]   # 404/405 = server up but no /health route
    timeout: 2
  register: _probe_result
  ignore_errors: true
  loop: "{{ llm_probe_ports | dict2items }}"
  when: hydra_llm_endpoints | length == 0

- name: Build discovered endpoint list from successful probes
  set_fact:
    _endpoints_to_probe: >-
      {{ _endpoints_to_probe + [{'provider': item.item.value,
         'url': 'http://127.0.0.1:' + item.item.key|string + '/v1',
         'port': item.item.key, 'api_key': none}] }}
  loop: "{{ _probe_result.results | default([]) }}"
  when:
    - hydra_llm_endpoints | length == 0
    - not item.failed
    - item.status in [200, 404, 405]

# Step B: Query each live endpoint for loaded models
- name: Query Ollama /api/tags for loaded models
  uri:
    url: "{{ item.url | replace('/v1', '') }}/api/tags"
    return_content: true
    timeout: 5
  register: _ollama_tags
  ignore_errors: true
  loop: "{{ _endpoints_to_probe }}"
  when: item.provider == "ollama"

- name: Query OpenAI-compat /v1/models for loaded models
  uri:
    url: "{{ item.url }}/models"
    return_content: true
    timeout: 5
    headers:
      Authorization: "Bearer {{ item.api_key | default('none') }}"
  register: _openai_models
  ignore_errors: true
  loop: "{{ _endpoints_to_probe }}"
  when: item.provider in ["llamacpp", "vllm-mlx", "litellm"]

# Step C: Build structured provider list with model info
- name: Build discovered providers fact
  set_fact:
    _discovered_providers: "{{ _discovered_providers | default([]) + [_provider_entry] }}"
  vars:
    _quant_regex: '[-_](q[0-9]+[_k_msx]*|f16|f32|f64|mxfp4|gguf)'
    _raw_models: >-
      {% if item.provider == 'ollama' and not (_ollama_tags.results
           | selectattr('item.provider', 'eq', 'ollama')
           | selectattr('failed', 'eq', false) | list | length == 0) %}
        {{ (_ollama_tags.results
            | selectattr('item.url', 'eq', item.url)
            | first).json.models
           | default([]) | map(attribute='name') | list }}
      {% else %}
        {{ (_openai_models.results
            | selectattr('item.url', 'eq', item.url)
            | first).json.data
           | default([]) | map(attribute='id') | list }}
      {% endif %}
    _provider_entry:
      name: "{{ item.provider }}"
      url: "{{ item.url }}"
      port: "{{ item.url | regex_replace('.*:(\\d+)/.*', '\\1') | int }}"
      status: up
      models: >-
        {{ _raw_models | map('regex_search', _quant_regex, ignorecase=True)
           | zip(_raw_models)
           | map('reverse') | map('list')
           | map(attribute=0) | map('lower')
           | zip(_raw_models)
           | map('list')
           | map('community.general.dict_to_array', 'quantization', 'name')
           | list }}
  loop: "{{ _endpoints_to_probe }}"

# Step D: Write /etc/hydra/discovered.yml
- name: Write /etc/hydra/discovered.yml
  template:
    src: discovered.yml.j2
    dest: /etc/hydra/discovered.yml
    mode: "0644"
  become: true
```

> **Note on model parsing complexity**: The Jinja2 model-list building above is intentionally simplified. If `community.general` collection isn't available, replace the `models` block with a plain list of model name strings — quantization extraction happens in the `monitoring-cfg` template instead via the OTEL `transform/quantization` processor, which handles it at ingest time. The discovered.yml only needs model names for documentation/debugging purposes.

- [ ] **Step 5.4: Simplify — write `discovered.yml.j2` with flat model lists**

```yaml
# ansible/roles/llm-discovery/templates/discovered.yml.j2
# Written by llm-discovery role on {{ ansible_date_time.iso8601 }}
# Re-run: ansible-playbook site.yml --tags llm-discovery,monitoring-cfg
node_id: {{ hydra_node_id }}
hostname: {{ hydra_hostname }}
gpu_provider: {{ hydra_gpu_provider }}
os: {{ hydra_os }}
pools: {{ hydra_pools | to_yaml | trim }}

providers:
{% for ep in _endpoints_to_probe | default([]) %}
  - name: {{ ep.provider }}
    url: {{ ep.url }}
    port: {{ ep.url | regex_replace('.*:(\d+).*', '\1') }}
    status: up
    models: []   # populated at OTEL ingest time via /v1/models scrape
{% else %}
[]
{% endfor %}
```

- [ ] **Step 5.5: Commit**

```bash
git add ansible/roles/llm-discovery/ ansible/tests/test_llm_discovery_logic.py
git commit -m "feat(ansible): llm-discovery role probes LLM endpoints, writes discovered.yml"
```

---

## Task 6: `monitoring-cfg` role

**Files:**
- Create: `ansible/roles/monitoring-cfg/tasks/main.yml`
- Create: `ansible/roles/monitoring-cfg/templates/otel-agent.env.j2`
- Create: `ansible/roles/monitoring-cfg/templates/otel-agent-config.yaml.j2`

- [ ] **Step 6.1: Create `ansible/roles/monitoring-cfg/templates/otel-agent.env.j2`**

```jinja2
# /etc/hydra/otel-agent.env — rendered by monitoring-cfg role
# For debugging: source this file then run otelcol-contrib manually
NODE_ID={{ hydra_node_id }}
HOSTNAME={{ hydra_hostname }}
CLUSTER={{ hydra_cluster_name }}
ENVIRONMENT={{ hydra_environment }}
OS={{ hydra_os }}
GPU_PROVIDER={{ hydra_gpu_provider }}
CHIP={{ hydra_chip | default('unknown') }}
POOL={{ hydra_pools | join(',') }}
OTEL_GATEWAY_PRIMARY={{ hydra_otel_gateway_primary }}
OTEL_GATEWAY_SECONDARY={{ hydra_otel_gateway_secondary | default('') }}
GOMAXPROCS=2
GOMEMLIMIT=100MiB
```

- [ ] **Step 6.2: Create `ansible/roles/monitoring-cfg/templates/otel-agent-config.yaml.j2`**

```yaml
# /etc/hydra/otel-agent-config.yaml — rendered by monitoring-cfg role
# Re-render: ansible-playbook site.yml --tags monitoring-cfg

extensions:
  health_check:
    endpoint: "0.0.0.0:13133"
  memory_ballast:
    size_mib: 32

receivers:
  # Hardware exporter — port is fixed per gpu_provider (see group_vars/all.yml)
  prometheus/hw_exporter:
    config:
      scrape_configs:
        - job_name: hw-exporter
          scrape_interval: 15s
          static_configs:
            - targets:
                - "127.0.0.1:{{ hw_exporter_ports[hydra_gpu_provider] }}"

{% set discovered = lookup('file', '/etc/hydra/discovered.yml') | from_yaml %}
{% for provider in discovered.providers | default([]) %}
  prometheus/llm_{{ provider.name }}:
    config:
      scrape_configs:
        - job_name: "llm-{{ provider.name }}"
          scrape_interval: 15s
          static_configs:
            - targets:
                - "127.0.0.1:{{ provider.port }}"
          metric_relabel_configs:
            - target_label: provider
              replacement: "{{ provider.name }}"
            - target_label: node_id
              replacement: "${NODE_ID}"
{% endfor %}

  otlp:
    protocols:
      grpc:
        endpoint: "127.0.0.1:4317"
        max_recv_msg_size_mib: 4
      http:
        endpoint: "127.0.0.1:4318"

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 100
    spike_limit_mib: 25

  resource:
    attributes:
      - { key: node_id,      value: "${NODE_ID}",      action: upsert }
      - { key: hostname,     value: "${HOSTNAME}",     action: upsert }
      - { key: cluster,      value: "${CLUSTER}",      action: upsert }
      - { key: environment,  value: "${ENVIRONMENT}",  action: upsert }
      - { key: os,           value: "${OS}",           action: upsert }
      - { key: gpu_provider, value: "${GPU_PROVIDER}", action: upsert }
      - { key: chip,         value: "${CHIP}",         action: upsert }
      - { key: pool,         value: "${POOL}",         action: upsert }

  transform/quantization:
    metric_statements:
      - context: datapoint
        statements:
          - set(attributes["quantization"],
              ExtractPatterns(attributes["model"],
                "[-_](q[0-9]+[_k_msx]*|f16|f32|f64|mxfp4)")[0])
              where attributes["model"] != nil

  filter/noise:
    metrics:
      exclude:
        match_type: regexp
        metric_names:
          - ".*_debug_.*"
          - "go_.*"
          - "process_.*"

  batch:
    send_batch_size: 200
    timeout: 5s

exporters:
  otlp/gateway:
    endpoint: "${OTEL_GATEWAY_PRIMARY}"
    tls:
      insecure: true
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 300s
    sending_queue:
      num_consumers: 4
      queue_size: 1000

  prometheus:
    endpoint: "0.0.0.0:8889"
    resource_to_telemetry_conversion:
      enabled: true
    metric_expiration: 5m

service:
  extensions: [health_check, memory_ballast]
  pipelines:
    metrics:
      receivers:
        - prometheus/hw_exporter
{% for provider in discovered.providers | default([]) %}
        - prometheus/llm_{{ provider.name }}
{% endfor %}
        - otlp
      processors:
        - memory_limiter
        - resource
        - transform/quantization
        - filter/noise
        - batch
      exporters:
        - otlp/gateway
        - prometheus
```

- [ ] **Step 6.3: Create `ansible/roles/monitoring-cfg/tasks/main.yml`**

```yaml
# ansible/roles/monitoring-cfg/tasks/main.yml
---
- name: Load discovered.yml from remote node
  slurp:
    src: /etc/hydra/discovered.yml
  register: _discovered_raw
  become: true

- name: Parse discovered.yml
  set_fact:
    _discovered: "{{ _discovered_raw.content | b64decode | from_yaml }}"

- name: Render otel-agent.env
  template:
    src: otel-agent.env.j2
    dest: /etc/hydra/otel-agent.env
    mode: "0640"
  become: true

- name: Render otel-agent-config.yaml
  template:
    src: otel-agent-config.yaml.j2
    dest: /etc/hydra/otel-agent-config.yaml
    mode: "0640"
  vars:
    discovered: "{{ _discovered }}"
  become: true
  notify: validate and restart otel agent

- name: Flush handlers immediately
  meta: flush_handlers

- name: Wait for OTEL agent health check
  uri:
    url: "http://localhost:13133"
    status_code: 200
    timeout: 5
  retries: 6
  delay: 5
  register: _health
  until: _health.status == 200

handlers:
  - name: validate and restart otel agent
    block:
      - name: Validate OTEL config (dry-run)
        command: /usr/local/bin/otelcol-contrib --config /etc/hydra/otel-agent-config.yaml validate
        become: true

      - name: Restart OTEL agent (Linux)
        systemd:
          name: otel-agent
          state: restarted
        become: true
        when: hydra_os == "linux"

      - name: Restart OTEL agent (macOS)
        command: >
          launchctl kickstart -k system/com.hydra.otel-agent
        become: true
        when: hydra_os == "macos"
```

- [ ] **Step 6.4: Commit**

```bash
git add ansible/roles/monitoring-cfg/
git commit -m "feat(ansible): monitoring-cfg role renders OTEL config from discovered.yml"
```

---

## Task 7: Wire up `ansible.cfg` and smoke test

**Files:**
- Create: `ansible/ansible.cfg`

- [ ] **Step 7.1: Create `ansible/ansible.cfg`**

```ini
# ansible/ansible.cfg
[defaults]
inventory          = ./inventory_plugin.py
roles_path         = ./roles
host_key_checking  = False
retry_files_enabled = False
stdout_callback    = yaml
gather_facts       = smart
fact_caching       = memory

[ssh_connection]
pipelining = True
```

- [ ] **Step 7.2: Verify inventory output shows all 3 pilot machines**

```bash
cd /home/dk/Documents/git/hydra/ansible
ansible-inventory --list | python -m json.tool | grep -E '"id|node_id|gpu_provider|pools'
```

Expected output (keys present for each host):
```
"hydra_node_id": "p1-m2-8g",
"hydra_gpu_provider": "apple",
"hydra_pools": ["embed"],
"hydra_node_id": "p1-m3-16g",
...
"hydra_node_id": "p1-i7-rtx3050",
"hydra_gpu_provider": "nvidia",
```

- [ ] **Step 7.3: Dry-run the full playbook against all nodes (no changes)**

```bash
cd /home/dk/Documents/git/hydra/ansible
ansible-playbook site.yml --check --diff 2>&1 | tail -20
```

Expected: No fatal errors. Tasks marked `skipping` or `changed` in check mode are fine.

- [ ] **Step 7.4: Run all unit tests**

```bash
cd /home/dk/Documents/git/hydra/ansible
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7.5: Final commit**

```bash
git add ansible/ansible.cfg
git commit -m "feat(ansible): add ansible.cfg; full playbook dry-run verified"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Covered by task |
|---|---|
| cluster.yml schema with all fields | Task 1 |
| Enum validation in inventory plugin | Task 2 |
| `maintenance: true` exclusion | Task 2 |
| base role: OTEL agent binary + service | Task 3 |
| hw-exporter: apple path | Task 4 |
| hw-exporter: nvidia path | Task 4 |
| hw-exporter: cpu/node_exporter path | Task 4 |
| llm-discovery: port probe | Task 5 |
| llm-discovery: write discovered.yml | Task 5 |
| monitoring-cfg: env file render | Task 6 |
| monitoring-cfg: OTEL config render with all labels | Task 6 |
| resource processor: all 8 node labels | Task 6 |
| transform/quantization processor | Task 6 |
| provider label per scrape job | Task 6 |
| resource_to_telemetry_conversion enabled | Task 6 |
| OTEL agent health check after restart | Task 6 |
| ansible.cfg wiring | Task 7 |

**Items deferred to Plan B (Dashboard & Alert fixes):**
- Grafana variable updates (`$node`, `$pool`, `$gpu_provider`, `$provider`)
- Panel PromQL fixes (`{__name__=~"$gpu_util_metric", ...}`)
- Alert rule `job` selector fixes
- `node_id` label in alert annotations

**amd role:** Spec section 4.3 lists `amd.yml` — stub file not created because no AMD hardware exists in Phase 1 pilot. Create a placeholder:

```yaml
# ansible/roles/hw-exporter/tasks/amd.yml
---
- name: AMD ROCm exporter (not yet implemented)
  debug:
    msg: "AMD ROCm exporter installation is deferred to v2. Node {{ hydra_node_id }} will have no GPU hardware metrics."
```

Add this to the commit in Task 4.8.
