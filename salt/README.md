# Salt Mirror — Hydra MLX-Observability Automation

This directory is an intentional **Salt Stack mirror** of the Ansible automation under
`ansible/`. Both tool-sets produce the same end-state on an Apple-silicon LLM node.
The duplication is deliberate: it lets you evaluate both tools before standardising.

---

## Quick start

```bash
# From the repo root of this worktree
# -----------------------------------------------------------------------
# 1. Update the two absolute paths in salt/master to match your checkout:
sed -i "s|/home/dk/Documents/git/hydra/.worktrees/salt|$(pwd)|g" salt/master

# 2. Full highstate on every node in the roster
salt-ssh -c salt/ --roster-file salt/roster '*' state.highstate

# 3. Single formula on one node (faster iteration)
salt-ssh -c salt/ --roster-file salt/roster 'hydra-exp-01' state.apply llm-common
salt-ssh -c salt/ --roster-file salt/roster 'hydra-exp-01' state.apply llm-mlx
salt-ssh -c salt/ --roster-file salt/roster 'hydra-large-*' state.apply llm-exo
salt-ssh -c salt/ --roster-file salt/roster 'hydra-large-*' state.apply mlx-allreduce-probe
salt-ssh -c salt/ --roster-file salt/roster '*' state.apply monitoring-common

# 4. Dry-run (test=True — show what would change without applying)
salt-ssh -c salt/ --roster-file salt/roster 'hydra-exp-01' state.highstate test=True

# 5. Inspect rendered pillar for a node
salt-ssh -c salt/ --roster-file salt/roster 'hydra-exp-01' pillar.items
```

### Prerequisites on the control node
- `salt-ssh` ≥ 3006 (install: `pip install salt`)
- SSH key in `~/.ssh/id_ed25519` with `sudo` access on the targets
- No salt-minion required on targets — salt-ssh is fully agentless

---

## Port / metric contract

These values are fixed in `salt/pillar/llm.sls` and must match the Ansible
`group_vars/all/main.yml` to keep the same Prometheus rules and Grafana dashboards
working regardless of which tool deploys the node.

| Service | Port | Notes |
|---|---|---|
| mlx public API / vllm-mlx | **11500** | `mlx_port` |
| mlx engine loopback (sidecar active) | **11510** | `mlx_internal_port` |
| mlx perf-proxy `/metrics` | **11501** | `mlx_perf_scrape_port` |
| mlx log-exporter `/metrics` | **11502** | `mlx_logexporter_scrape_port` |
| mlx AllReduce fabric exporter | **11503** | `mlx_allreduce_fabric_scrape_port` |
| exo public chat API | **52416** | `exo_api_port` |
| exo engine loopback (sidecar active) | **52426** | `exo_internal_api_port` |
| exo perf-proxy `/metrics` | **52417** | `exo_perf_scrape_port` |

Metric prefixes: `mlx:` (mlx-lm pure path), `exo:` (exo path) — identical to Ansible.

Discovered-provider names that the OTEL relabels key off:
`mlx`, `mlx_perf`, `mlx_errors`, `exo`, `mlx_fabric` — identical to Ansible.

---

## Ansible role → Salt state mapping

| Ansible role / file | Salt state / file |
|---|---|
| `group_vars/all/main.yml` | `salt/pillar/llm.sls` (global defaults) |
| host_vars / inventory variables | `salt/pillar/nodes/<minion-id>.sls` |
| `roles/llm-common/tasks/main.yml` | `salt/states/llm-common/init.sls` |
| `roles/llm-common/files/llm_perf_proxy.py` | `salt/states/llm-common/files/llm_perf_proxy.py` (verbatim copy) |
| `roles/llm-common/files/hydra-logrotate.conf` | `salt/states/llm-common/files/hydra-logrotate.conf` (verbatim copy) |
| `roles/llm-mlx/tasks/main.yml` + handlers | `salt/states/llm-mlx/init.sls` |
| `roles/llm-mlx/templates/com.hydra.mlx-server.plist.j2` | `salt/states/llm-mlx/com.hydra.mlx-server.plist.j2` |
| `roles/llm-mlx/templates/com.hydra.mlx-perf.plist.j2` | `salt/states/llm-mlx/com.hydra.mlx-perf.plist.j2` |
| `roles/llm-mlx/templates/com.hydra.mlx-logexporter.plist.j2` | `salt/states/llm-mlx/com.hydra.mlx-logexporter.plist.j2` |
| `roles/llm-exo/tasks/main.yml` + handlers | `salt/states/llm-exo/init.sls` |
| `roles/llm-exo/templates/com.hydra.exo.plist.j2` | `salt/states/llm-exo/com.hydra.exo.plist.j2` |
| `roles/llm-exo/templates/com.hydra.exo-perf.plist.j2` | `salt/states/llm-exo/com.hydra.exo-perf.plist.j2` |
| `roles/mlx-allreduce-probe/tasks/main.yml` + handlers | `salt/states/mlx-allreduce-probe/init.sls` |
| `roles/mlx-allreduce-probe/files/mlx_allreduce_probe.py` | `salt/states/mlx-allreduce-probe/files/mlx_allreduce_probe.py` (verbatim copy) |
| `roles/mlx-allreduce-probe/files/mlx_fabric_exporter.py` | `salt/states/mlx-allreduce-probe/files/mlx_fabric_exporter.py` (verbatim copy) |
| `roles/mlx-allreduce-probe/templates/com.hydra.mlx-allreduce.plist.j2` | `salt/states/mlx-allreduce-probe/com.hydra.mlx-allreduce.plist.j2` |
| `roles/mlx-allreduce-probe/templates/com.hydra.mlx-fabric-exporter.plist.j2` | `salt/states/mlx-allreduce-probe/com.hydra.mlx-fabric-exporter.plist.j2` |
| `roles/mlx-allreduce-probe/templates/run-allreduce-probe.sh.j2` | `salt/states/mlx-allreduce-probe/run-allreduce-probe.sh.j2` |
| `roles/llm-discovery/tasks/main.yml` + `templates/discovered.yml.j2` | `salt/states/monitoring-common/discovered.yml.j2` |
| `roles/monitoring-common/templates/otel-agent-config.yaml.j2` (LLM-scrape section) | `salt/states/monitoring-common/otel-agent-config.yaml.j2` |

### Omissions (intentional)

The following Ansible roles have **no Salt counterpart** in this mirror because they
are out of scope for the MLX-observability workload:

| Ansible role | Why omitted |
|---|---|
| `otel-mac-agent` | Binary install + host-metrics scrapers — not LLM-specific |
| `apple-silicon-exporter` | Hardware exporter daemon — not LLM-specific |
| `mlx-rdma-exporter` | RDMA metrics — fabric-layer, not LLM inference |
| `base`, `hw-exporter` | Base OS / hardware setup |

The OTEL config in `monitoring-common/otel-agent-config.yaml.j2` covers only the
LLM-scrape pipeline. It uses the same `prometheus/llm_<name>` receiver pattern and
identical processor/exporter blocks so the existing Prometheus alerting rules and
Grafana dashboards work without modification. The omitted scrapers (host-metrics,
apple-silicon-exporter, RDMA) continue to be managed by Ansible or can be added to
this Salt config later.

---

## Ansible vs Salt — comparison for this workload

This is an honest assessment for the specific Hydra MLX-observability use case
(agentless push to macOS Apple-silicon nodes, ~5-20 targets, air-gapped).

### Agentless model
Both tools use SSH push with no persistent daemon on targets.
- **Ansible**: native agentless — designed around it. Zero extra config.
- **Salt (salt-ssh)**: agentless is supported but not the primary model. It
  requires the `salt-ssh` package (not the standard `salt-master/minion` pair),
  and the roster is a separate file from inventory. Operationally equivalent but
  slightly more setup overhead.

**Verdict**: Ansible has a slight edge here.

### Jinja2 templating parity
Both use Jinja2. Template syntax is nearly identical.
- **Ansible**: variables come from group_vars/host_vars and role vars directly by name.
- **Salt**: variables must be prefixed with `pillar.get(...)` or `grains.get(...)`.
  This is more verbose but also more explicit about the data source.

For plist templates (XML), both work equally well. The Salt templates in this mirror
are ~5% longer due to `pillar.get(...)` calls but functionally identical.

**Verdict**: Parity; Ansible is marginally less verbose.

### macOS launchd handling
Neither tool has a first-class macOS launchd state module that handles
`bootstrap`/`bootout` idiomatically.
- **Ansible**: `command: launchctl bootstrap …` in tasks, with `notify` → handler
  that does `bootout` + `bootstrap`. Clean separation between install and restart.
- **Salt**: `file.managed` for the plist + `cmd.run` with `onchanges` for restart.
  The `onchanges` trigger is semantically equivalent to Ansible's `notify`/handler.
  Slightly more boilerplate per daemon (three states vs two task+handler pairs).

**Verdict**: Ansible wins on conciseness; Salt's `onchanges` is equivalent in
correctness.

### Secrets management
- **Ansible**: ansible-vault (`vault.yml` encrypted at rest, decrypted at run time).
- **Salt**: salt-pillar supports `gpg` renderer or external pillars (HashiCorp
  Vault, AWS Secrets Manager). No built-in encrypted-file equivalent to vault.yml
  without additional configuration.

For this air-gapped cluster, both approaches rely on pre-encrypted files anyway.
**Verdict**: Ansible-vault is simpler for a self-contained repo with no external
secret store.

### Idempotency
Both achieve idempotency:
- **Ansible**: `state: directory/present/file`, pip `state: present`, `creates:`
  guards on command tasks.
- **Salt**: `file.directory`, `pip.installed`, `unless:` / `onlyif:` guards.

Idiom parity is high; Salt's declarative states can be slightly more self-documenting.

**Verdict**: Tie.

### Learning curve
- **Ansible**: YAML playbooks, roles, tasks/handlers — very widely documented. Most
  macOS/Linux admins have encountered it.
- **Salt**: Steeper curve. Separate concepts (states, pillar, grains, modules,
  formulas). salt-ssh adds another layer. `cmd.run` abuse is a common pitfall.

**Verdict**: Ansible wins decisively.

### Recommendation for this workload

**Standardise on Ansible.**

Rationale:
1. The cluster is small (≤20 nodes), air-gapped, macOS-only — Ansible's agentless
   model is a perfect fit with zero extra infrastructure.
2. The ansible-vault secret management is already in place (`vault.yml`) and simpler
   than setting up a Salt GPG renderer or external pillar.
3. The existing team is almost certainly more familiar with Ansible given the
   extensive playbook coverage across all roles.
4. Salt shines at scale (1000+ nodes) and when you need event-driven reactors
   (`salt-reactor`). Neither benefit applies here.

**When to reconsider Salt**: if the fleet grows to 50+ nodes and you want push-on-
event (e.g. re-configure all nodes the moment a new model is published to MinIO),
Salt's reactor + mine system has no Ansible equivalent.
