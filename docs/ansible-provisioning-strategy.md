# Hydra Ansible Provisioning Strategy

**Status:** active · **Owner:** Platform/Ansible · **Tickets:** ANS-1, ANS-3, ANS-4, ANS-5, ANS-6, ANS-7, ANS-9

This document is the single source of truth for *which playbook owns which node and
which file*. It exists to kill the ANS-1 class of bug: two playbooks rendering the
same `/etc/hydra/otel-agent-config.yaml` and installing the same LaunchDaemons from
different templates, producing nondeterministic last-run-wins drift.

---

## 1. One playbook per OS

| OS | Canonical playbook | Inventory | Roles |
|---|---|---|---|
| **macOS** (Apple Silicon Mac Mini) | `ansible/mac-monitoring.yml` | `cluster.yml` (dynamic, `os_macos` group) | `llm-discovery` → `otel-mac-agent` (+ `apple-silicon-exporter`) |
| **Linux** (NVIDIA / AMD / CPU) | `ansible/site.yml` | `cluster.yml` (dynamic, gated `hydra_os == "linux"`) | `base` → `hw-exporter` → `llm-discovery` → `monitoring-cfg` |
| **LLM engines** (all pools) | `ansible/llm-frameworks.yml` | `inventories/llm/hosts.yml` (static) | `llm-common` + per-framework roles |

`site.yml` gates `base`, `hw-exporter`, `llm-discovery`, and `monitoring-cfg` behind
`when: hydra_os == "linux"`. Running `site.yml` against a mixed inventory is therefore
a no-op on macOS hosts — `mac-monitoring.yml` is the only thing that touches them.

## 2. One template per artifact

The OTEL agent config is rendered from a **single** template shared by both OS paths:

```
ansible/roles/monitoring-common/templates/otel-agent-config.yaml.j2   # managed-by: monitoring-common
```

* `otel-mac-agent/tasks/configure.yml` renders it (macOS).
* `monitoring-cfg/tasks/main.yml` renders it (Linux).
* Both slurp `/etc/hydra/discovered.yml` first so the discovered-provider LLM scrape
  jobs render identically on both OSes.

The two previously divergent templates
(`monitoring-cfg/.../otel-agent-config.yaml.j2` and
`otel-mac-agent/.../otel-agent-config.yaml.j2`) were **deleted**.

### Unified template flag matrix

| Flag | Default | macOS | Linux | Purpose |
|---|---|---|---|---|
| `enable_hostmetrics` | `true` | ✅ | ✅ | CPU/mem/disk/fs/load/paging + OS-specific network scraper (SRE-5) |
| `enable_apple_hw` | `gpu_provider == apple` | ✅ | ❌ | scrape apple-silicon-exporter (`apple_*`) |
| `enable_llm_scrape` | `true` | ✅ | ✅ | one `prometheus/llm_<provider>` per discovered endpoint (ANS-3/-6) |
| `enable_otlp_ingest` | `true` | ✅ | ✅ | OTLP metrics **and traces** receiver (LLM-6) |
| `enable_rdma_scrape` | `false` | ⚙️ | ⚙️ | scrape mlx-rdma-exporter (SRE-3), enabled by that role |
| `enable_fleet_otlp` | `mc_fleet_otlp != ""` | ✅ | ✅ | Fleet Platform node-context fan-out |
| `otel_tls_insecure` | `false` | ✅ | ✅ | secure-by-default mTLS to gateway/VM/fleet (ANS-5) |

Network scraper is OS-specific:
* **macOS:** `^en[0-9]+$`, `^bridge[0-9]+$` (en0 10GbE + Thunderbolt RoCE bridge).
* **Linux:** `^(eth|en)[0-9].*$`, `^ib[0-9].*$` (Ethernet + InfiniBand/RoCE uplinks).

## 3. LaunchDaemon ownership (macOS)

Each plist has exactly one owning role. No two roles may install the same
`/Library/LaunchDaemons/*.plist`.

| Plist | Owning role |
|---|---|
| `com.hydra.otel-agent.plist` | `otel-mac-agent` |
| `com.hydra.apple-exporter.plist` | `apple-silicon-exporter` (ANS-7) |
| `com.hydra.mlx-server.plist` / `com.hydra.mlx-logexporter.plist` | `llm-mlx` |
| `com.hydra.*` (other engines) | the engine's own role |

`hw-exporter/tasks/apple.yml` is **deprecated** (Linux-only role now); it no longer
builds or installs the Apple exporter — that moved to the dedicated
`apple-silicon-exporter` role with air-gapped per-arch staged binaries.

## 4. Supply-chain & secrets (ANS-4 / ANS-5)

* All binary installs verify a sha256 from `group_vars/all/artifacts.yml`
  (`hydra_artifact_checksums`) and assert `--version` against `hydra_versions`.
* Secrets live in `group_vars/all/vault.yml` (ansible-vault) — no plaintext
  `changeme*` in `cluster.yml` / `inventories/llm/hosts.yml`.
* TLS material is staged air-gapped by the `otel-tls` role into `/etc/hydra/tls/`.

## 5. Inventory source of truth (ANS-9)

`cluster.yml` (+ `inventory_plugin.py`) is authoritative for host/group/endpoint
facts. `inventories/llm/hosts.yml` is the deployment-framework view and is kept
consistent (provider names and LiteLLM port reconciled). A CI parity check guards
against future drift.
