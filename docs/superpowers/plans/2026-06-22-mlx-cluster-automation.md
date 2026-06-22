# MLX Cluster Automation — Upgrade, Scalability, Crash Reporting & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-06-22
**Status:** Draft — awaiting approval
**Scope:** `ansible/roles/llm-mlx`, `ansible/roles/salt-*`, `monitoring/`, `apple-silicon-monitoring/`

**Goal:** Bring the Apple MLX inference automation up to the latest `mlx-lm`, deliver all Python dependencies from the on-prem JFrog Artifactory (with an air-gapped wheel fallback), make the deployment horizontally scalable with first-class telemetry/tracing, and add crash detection + auto-remediation + SMTP reporting driven by SaltStack.

**Architecture:** Ansible keeps ownership of *provisioning* (venv, dependency install, LaunchDaemon, config templates, upgrades). SaltStack owns *runtime operations* — a `log` beacon on each minion detects Metal/SIGABRT crashes and fires a Salt event; a master-side reactor restarts the daemon, drains the node from the LiteLLM pool, and emails operators. Inference-layer metrics are exposed via a Prometheus `/metrics` endpoint (native with `vllm-mlx`, or via a sidecar for `mlx_lm.server`) and scraped by the existing `otel-mac-agent`. Alertmanager gains an SMTP receiver in parallel with Salt for rule-based emails.

**Tech Stack:** Ansible 2.16+, SaltStack (beacons + reactor), `mlx`/`mlx-lm` (Python 3.11), optional `vllm-mlx`, `prometheus_client`, otelcol-contrib, VictoriaMetrics, Prometheus, Alertmanager, Grafana, Loki, Tempo (optional), Opik.

---

## Global Constraints

- **Air-gapped targets.** No node has internet. All Python packages come from the on-prem **JFrog Artifactory PyPI virtual repo** (`{{ jfrog_pypi_index }}`), never from public PyPI. A vendored wheel directory remains as a fallback for fully-disconnected runs.
- **`pip` and `ruby` are out of scope** — they are provided/served by JFrog Artifactory and must not be re-vendored or reinstalled by these roles.
- **Python pin: 3.11** on all MLX nodes (the pasted crash log showed Python 3.14.6 SIGABRT; do not move MLX nodes off 3.11/3.12).
- **Latest MLX:** `mlx-lm==0.31.3` (released 2026-04-22) and the matching `mlx` it depends on. Verify the exact `mlx` pin against the JFrog index before committing.
- **Hardware:** 28 × Apple M4 / 16 GB unified memory. Model weight ceiling per node is ~9.6 GB (60% rule); KV cache budget 3.8–8 GB. OOM guardrails must respect this.
- **No port collisions.** Ollama owns `11434` cluster-wide. MLX must not use `11434`.
- **Idempotency:** every role must be safe to re-run; use `creates:`, `changed_when:`, and handlers consistently with the existing roles.
- **Secrets:** SMTP credentials and JFrog tokens come from `ansible/cluster.yml` (v1 plain-text convention, matching existing `litellm_master_key`); a `# TODO v2: move to vault` comment is required wherever a secret is referenced.
- **Test scope per task:** run only the test/validation commands named in that task. The full `pytest ansible/tests/` + `ansible-lint` sweep is the merge gate, not the per-task gate.

---

## Findings Recap (why each task exists)

| # | Finding (current state) | Evidence | Task |
|---|--------------------------|----------|------|
| 1 | Pins `mlx>=0.16.0`, `mlx-lm>=0.19.0` — stale (latest 0.31.3) | `ansible/roles/llm-mlx/vars/main.yml:9-16` | Task 1 |
| 2 | Deps vendored as wheels, not from JFrog | `ansible/roles/llm-mlx/tasks/main.yml:48-71` | Task 1 |
| 3 | `mlx_port: 11434` collides with Ollama | `vars/main.yml:5` vs `cluster.yml` ollama endpoints | Task 2 |
| 4 | No OOM guardrails → reproduces the pasted Metal SIGABRT | `templates/com.hydra.mlx-server.plist.j2` | Task 3 |
| 5 | `mlx_lm.server` has no `/metrics`, no batching ("not for production" upstream) | plist `ProgramArguments` | Tasks 4A/4B/4C |
| 6 | MLX `/metrics` not wired into discovery | `roles/llm-discovery/tasks/main.yml` | Task 5 |
| 7 | Alertmanager has Slack only, no SMTP | `monitoring/alertmanager/alertmanager.yml:24-37` | Task 6 |
| 8 | No `mlx_metal_failures_total` metric | n/a | Task 7 |
| 9 | No event-driven crash → email/restart/drain loop | salt roles have reactor scaffolding only | Task 8 |
| 10 | No Tempo trace backend; no MLX dashboard | `monitoring/docker-compose.yml` | Tasks 9–10 (optional) |

## Decision: Ansible vs Salt (resolved)

**Use both — do not consolidate.**

| Concern | Tool | Why |
|---------|------|-----|
| Install venv, deps, LaunchDaemon, configs, upgrades | **Ansible** | Push, agentless, air-gap friendly over SSH; already the project's provisioning layer |
| Continuous state enforcement, fleet telemetry, remote exec at scale | **Salt** | Persistent minion + grains already deployed (`salt-minion` role) |
| **Crash → report → auto-remediate** (this request) | **Salt** | Event bus + `log` beacon + master reactor give immediate, node-local, event-driven response that Ansible's run-once model cannot. |

Net: **Ansible provisions, Salt operates the crash/telemetry loop.**

## Server choice (all three documented per request)

- **Option 4A — `vllm-mlx` (recommended):** continuous batching, paged KV cache, prefix caching, native `--enable-metrics` `/metrics`, OpenAI + Anthropic APIs. Delivers scalability *and* the missing MLX telemetry in one move.
- **Option 4B — keep `mlx_lm.server` + `llm-telemetry` sidecar:** lowest behavioural change; adds a Python sidecar that wraps requests and exposes `/metrics`. Choose if `vllm-mlx` (currently `v0.4.0rc1`, pre-release) is deemed too new for production.
- **Option 4C — keep `mlx_lm.server` as-is:** versions/port/OOM/crash tasks (1,2,3,6,7,8) still apply; no inference-layer token metrics. Documented as the minimal path.

Implement **4A** by default; 4B and 4C are mutually-exclusive alternatives gated by a single `mlx_server_backend` variable.

---

## File Structure

```
ansible/
  cluster.yml                                  ← MODIFY: add jfrog + smtp + mlx_server_backend vars
  roles/llm-mlx/
    vars/main.yml                              ← MODIFY: versions, port, backend toggle, guardrails
    tasks/main.yml                             ← MODIFY: JFrog install + fallback; dispatch by backend
    tasks/install_vllm_mlx.yml                 ← CREATE: Option 4A install
    tasks/install_mlx_lm.yml                   ← CREATE: Option 4B/4C install
    templates/com.hydra.mlx-server.plist.j2    ← MODIFY: guardrails, port; vllm-mlx variant
    templates/com.hydra.mlx-telemetry.plist.j2 ← CREATE (4B): sidecar daemon
    templates/com.hydra.mlx-logexporter.plist.j2 ← CREATE (Task 7)
    files/mlx_log_exporter.py                  ← CREATE (Task 7): metal_failures exporter
    files/README.md                            ← MODIFY: JFrog + fallback instructions
    handlers/main.yml                          ← MODIFY: restart sidecar + logexporter
  roles/salt-minion/
    templates/beacons.conf.j2                  ← CREATE (Task 8): log beacon
    tasks/main.yml                             ← MODIFY: deploy beacon config
  roles/salt-master/
    templates/reactor-mlx_crash.sls.j2         ← CREATE (Task 8)
    templates/mlx_crash_handler.sls.j2         ← CREATE (Task 8): email + restart + drain
    templates/salt-master.conf.j2              ← MODIFY: register reactor + smtp returner
    tasks/main.yml                             ← MODIFY: deploy new states
  roles/llm-discovery/tasks/main.yml           ← MODIFY (Task 5): probe mlx /metrics port
  tests/test_mlx_role.py                       ← CREATE: rendering/idempotence assertions

monitoring/
  alertmanager/alertmanager.yml                ← MODIFY (Task 6): email_configs receiver
  prometheus/rules/mlx-alerts.yml              ← CREATE (Task 6): MLX alert rules
  docker-compose.yml                           ← MODIFY (Task 9, optional): tempo service
  tempo/tempo-config.yml                       ← CREATE (Task 9, optional)
  grafana/provisioning/datasources/datasources.yml ← MODIFY (Task 9, optional): Tempo ds
  grafana/dashboards/mlx-inference.json        ← CREATE (Task 10, optional)
```

---

## Task 1: Bump MLX to latest + install from JFrog (offline fallback)

**Files:**
- Modify: `ansible/roles/llm-mlx/vars/main.yml:9-16`
- Modify: `ansible/roles/llm-mlx/tasks/main.yml:46-71`
- Modify: `ansible/cluster.yml` (add `jfrog` block)
- Modify: `ansible/roles/llm-mlx/files/README.md`

**Interfaces:**
- Produces: `jfrog_pypi_index` (str), `mlx_use_jfrog` (bool), `mlx_packages` (list) consumed by Tasks 4A/4B.

- [ ] **Step 1: Add JFrog config to `cluster.yml`**

Add under the top-level config (near the `model_registry` block):

```yaml
# ── JFrog Artifactory (on-prem package mirror) ───────────────────────────────
jfrog:
  pypi_index: "https://artifactory.hydra.local/artifactory/api/pypi/pypi-remote/simple"
  pypi_trusted_host: "artifactory.hydra.local"
  # TODO v2: move token to vault
  token: "changeme-jfrog-token"
```

- [ ] **Step 2: Update `vars/main.yml`**

```yaml
# ansible/roles/llm-mlx/vars/main.yml
---
mlx_dir: "{{ llm_base_dir }}/mlx"
mlx_venv: "{{ mlx_dir }}/venv"
mlx_port: 11500          # moved off 11434 (Ollama) — see Task 2
mlx_metrics_port: 11501  # /metrics scrape target — see Task 5
mlx_workers: 2
mlx_default_model: /opt/hydra/models/mlx/Mistral-7B-Instruct-v0.3-4bit
mlx_models_dir: /opt/hydra/models/mlx

# Backend selector: vllm_mlx (4A, default) | mlx_lm_sidecar (4B) | mlx_lm (4C)
mlx_server_backend: vllm_mlx

# Dependency delivery
mlx_use_jfrog: true
jfrog_pypi_index: "{{ jfrog.pypi_index }}"
jfrog_pypi_trusted_host: "{{ jfrog.pypi_trusted_host }}"

# Pinned latest — verify exact mlx pin against the JFrog index before commit
mlx_packages:
  - "mlx-lm==0.31.3"
  - "mlx>=0.22.0"
  - "huggingface-hub>=0.27.0"
  - "transformers>=4.48.0"
  - "sentencepiece>=0.2.0"
  - "protobuf>=4.25.0"
  - "numpy>=1.26.0,<2.3.0"

mlx_vllm_packages:
  - "vllm-mlx==0.4.0rc1"

mlx_telemetry_packages:
  - "llm-telemetry>=0.1.0"
  - "prometheus-client>=0.20.0"
```

- [ ] **Step 3: Replace the wheels section of `tasks/main.yml` (lines 46-71)**

Replace the `# ── Wheels ──` and `# ── Virtualenv ──` blocks with a JFrog-first, wheel-fallback install. Keep the venv creation:

```yaml
# ── Virtualenv ────────────────────────────────────────────────────────────────
- name: Create MLX virtualenv
  command: >
    {{ homebrew_prefix }}/opt/python@{{ python_version }}/bin/python{{ python_version }}
    -m venv {{ mlx_venv }}
  args:
    creates: "{{ mlx_venv }}/bin/python"
  become: true

# ── Dependencies: JFrog Artifactory (primary) ─────────────────────────────────
- name: Install MLX packages from JFrog Artifactory
  pip:
    name: "{{ mlx_packages }}"
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--index-url {{ jfrog_pypi_index }} --trusted-host {{ jfrog_pypi_trusted_host }}"
    state: present
  become: true
  when: mlx_use_jfrog | bool
  register: _mlx_pip_jfrog
  retries: 2
  delay: 5
  until: _mlx_pip_jfrog is succeeded

# ── Dependencies: vendored wheels (fallback for full air-gap) ─────────────────
- name: Sync MLX wheels (offline fallback)
  synchronize:
    src: "{{ role_path }}/files/wheels/"
    dest: "{{ mlx_dir }}/wheels/"
    delete: false
  become: true
  when: not (mlx_use_jfrog | bool)

- name: Install MLX packages from local wheels (offline fallback)
  pip:
    name: "{{ mlx_packages }}"
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--no-index --find-links {{ mlx_dir }}/wheels"
    state: present
  become: true
  when: not (mlx_use_jfrog | bool)
```

Also delete the now-obsolete preflight "Assert MLX wheels directory exists" / "Fail if MLX wheels are missing" tasks (lines 7-22) **only when `mlx_use_jfrog` is true** — guard them with `when: not (mlx_use_jfrog | bool)` instead of removing, so the fallback path keeps its safety check.

- [ ] **Step 4: Update `files/README.md`**

Replace the "Download (on a Mac with internet)" section with:

```markdown
## Dependency delivery

Packages install from the on-prem JFrog Artifactory PyPI repo by default
(`mlx_use_jfrog: true`). No wheels need to be vendored for that path.

### Fully air-gapped fallback

Set `mlx_use_jfrog: false` and stage wheels into `files/wheels/`:

    pip download "mlx-lm==0.31.3" "mlx>=0.22.0" "huggingface-hub>=0.27.0" \
      "transformers>=4.48.0" "sentencepiece>=0.2.0" "protobuf>=4.25.0" \
      "numpy>=1.26.0,<2.3.0" \
      --platform macosx_14_0_arm64 --python-version 311 \
      --only-binary=:all: -d ansible/roles/llm-mlx/files/wheels/
```

- [ ] **Step 5: Validate render + syntax**

Run: `cd ansible && ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml --tags mlx --syntax-check`
Expected: `playbook: llm-frameworks.yml` with no errors.

- [ ] **Step 6: Commit**

```bash
git add ansible/roles/llm-mlx/vars/main.yml ansible/roles/llm-mlx/tasks/main.yml ansible/cluster.yml ansible/roles/llm-mlx/files/README.md
git commit -m "feat(llm-mlx): bump to mlx-lm 0.31.3, install from JFrog with wheel fallback"
```

---

## Task 2: Fix the MLX/Ollama port collision

**Files:**
- Modify: `ansible/roles/llm-mlx/templates/com.hydra.mlx-server.plist.j2`
- Modify: `ansible/roles/llm-mlx/tasks/main.yml` (health check URL)

**Interfaces:**
- Consumes: `mlx_port` (now `11500`) from Task 1.

- [ ] **Step 1: Confirm no other role uses 11500**

Run: `rg -n "11500|11501" ansible/ monitoring/`
Expected: matches only in `llm-mlx` files added by Task 1.

- [ ] **Step 2: Update the plist port reference**

In `com.hydra.mlx-server.plist.j2`, the `--port` arg already renders `{{ mlx_port }}`; no change needed there. Confirm the health check in `tasks/main.yml` uses the variable (it does: `http://localhost:{{ mlx_port }}/v1/models`). No literal `11434` should remain.

- [ ] **Step 3: Verify**

Run: `rg -n "11434" ansible/roles/llm-mlx/`
Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add ansible/roles/llm-mlx/
git commit -m "fix(llm-mlx): move MLX server off port 11434 (Ollama collision) to 11500"
```

---

## Task 3: OOM guardrails to prevent the Metal SIGABRT

**Files:**
- Modify: `ansible/roles/llm-mlx/templates/com.hydra.mlx-server.plist.j2`
- Modify: `ansible/roles/llm-mlx/vars/main.yml`

**Rationale:** the pasted crash (`MetalAllocator::malloc` -> `Convolution::eval_gpu` -> `abort`) is unified-memory exhaustion. On a 16 GB M4 the weight ceiling is ~9.6 GB; cap context/cache and set the MLX memory limit so MLX returns a recoverable error instead of aborting the process.

**Interfaces:**
- Produces: `mlx_max_tokens`, `mlx_max_kv_size`, `mlx_memory_limit_mb` consumed by the plist.

- [ ] **Step 1: Add guardrail vars to `vars/main.yml`**

```yaml
# OOM guardrails (16 GB M4 — weight ceiling ~9.6 GB, KV budget 3.8–8 GB)
mlx_max_tokens: 2048          # default per-request output cap
mlx_max_kv_size: 4096         # rotating KV cache ceiling (tokens)
mlx_memory_limit_mb: 12000    # MLX wired-memory limit; abort-guard
```

- [ ] **Step 2: Add guardrail env vars to the plist `EnvironmentVariables` dict**

```xml
    <key>MLX_METAL_DEBUG</key>
    <string>0</string>
    <key>HYDRA_MLX_MAX_KV_SIZE</key>
    <string>{{ mlx_max_kv_size }}</string>
```

- [ ] **Step 3: Add the `--max-kv-size` arg to `ProgramArguments` (mlx_lm backend)**

After the `--port` arg block, append:

```xml
    <string>--max-tokens</string>
    <string>{{ mlx_max_tokens }}</string>
```

(For the `vllm_mlx` backend the equivalent flags are set in Task 4A.)

- [ ] **Step 4: Validate render**

Run: `cd ansible && ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml --tags mlx --syntax-check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/llm-mlx/
git commit -m "feat(llm-mlx): add memory/context guardrails to reduce Metal OOM SIGABRT"
```

---

## Task 4A: Migrate to `vllm-mlx` (recommended backend)

**Files:**
- Create: `ansible/roles/llm-mlx/tasks/install_vllm_mlx.yml`
- Modify: `ansible/roles/llm-mlx/tasks/main.yml` (dispatch by `mlx_server_backend`)
- Modify: `ansible/roles/llm-mlx/templates/com.hydra.mlx-server.plist.j2` (backend-conditional args)

**Interfaces:**
- Consumes: `mlx_server_backend`, `mlx_vllm_packages`, `mlx_port`, `mlx_metrics_port`, `mlx_max_tokens` from earlier tasks.
- Produces: a `/metrics` Prometheus endpoint on `mlx_port` (vllm-mlx serves metrics on the same port).

- [ ] **Step 1: Create `install_vllm_mlx.yml`**

```yaml
# ansible/roles/llm-mlx/tasks/install_vllm_mlx.yml
---
- name: Install vllm-mlx from JFrog
  pip:
    name: "{{ mlx_vllm_packages }}"
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--index-url {{ jfrog_pypi_index }} --trusted-host {{ jfrog_pypi_trusted_host }}"
    state: present
  become: true
  when: mlx_use_jfrog | bool

- name: Install vllm-mlx from local wheels (offline fallback)
  pip:
    name: "{{ mlx_vllm_packages }}"
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--no-index --find-links {{ mlx_dir }}/wheels"
    state: present
  become: true
  when: not (mlx_use_jfrog | bool)
```

- [ ] **Step 2: Dispatch by backend in `tasks/main.yml`**

After the dependency install block, add:

```yaml
- name: Install vllm-mlx backend
  include_tasks: install_vllm_mlx.yml
  when: mlx_server_backend == 'vllm_mlx'
```

- [ ] **Step 3: Make the plist `ProgramArguments` backend-conditional**

Replace the `ProgramArguments` array body with:

```xml
  <key>ProgramArguments</key>
  <array>
{% if mlx_server_backend == 'vllm_mlx' %}
    <string>{{ mlx_venv }}/bin/vllm-mlx</string>
    <string>serve</string>
    <string>{{ mlx_default_model }}</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>{{ mlx_port }}</string>
    <string>--enable-metrics</string>
    <string>--continuous-batching</string>
    <string>--use-paged-cache</string>
    <string>--max-request-tokens</string><string>{{ mlx_max_tokens }}</string>
    <string>--cache-memory-percent</string><string>0.20</string>
{% else %}
    <string>{{ mlx_venv }}/bin/python</string>
    <string>-m</string><string>mlx_lm.server</string>
    <string>--model</string><string>{{ mlx_default_model }}</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>{{ mlx_port }}</string>
    <string>--max-tokens</string><string>{{ mlx_max_tokens }}</string>
{% endif %}
  </array>
```

Note: with `vllm-mlx`, `/metrics` is served on `{{ mlx_port }}`, so set `mlx_metrics_port == mlx_port` in discovery (Task 5 handles both cases).

- [ ] **Step 4: Validate render for both backends**

Run:
```bash
cd ansible
ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml --tags mlx --syntax-check
ansible localhost -m template -a "src=roles/llm-mlx/templates/com.hydra.mlx-server.plist.j2 dest=/tmp/p.plist" -e mlx_server_backend=vllm_mlx -e @cluster.yml
```
Expected: rendered plist at `/tmp/p.plist` contains `vllm-mlx` and `--enable-metrics`.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/llm-mlx/
git commit -m "feat(llm-mlx): add vllm-mlx backend with batching, paged cache, native /metrics"
```

---

## Task 4B (alternative): `mlx_lm.server` + `llm-telemetry` sidecar

> Implement **only if** `mlx_server_backend == 'mlx_lm_sidecar'`. Mutually exclusive with 4A.

**Files:**
- Create: `ansible/roles/llm-mlx/templates/com.hydra.mlx-telemetry.plist.j2`
- Create: `ansible/roles/llm-mlx/tasks/install_mlx_lm.yml`
- Modify: `ansible/roles/llm-mlx/handlers/main.yml`

**Interfaces:**
- Consumes: `mlx_telemetry_packages`, `mlx_port`, `mlx_metrics_port`.
- Produces: a sidecar exposing `/metrics` on `mlx_metrics_port` that proxies/instruments calls to `mlx_lm.server` on `mlx_port`.

- [ ] **Step 1: Create `install_mlx_lm.yml`** (installs the telemetry SDK alongside mlx-lm)

```yaml
# ansible/roles/llm-mlx/tasks/install_mlx_lm.yml
---
- name: Install llm-telemetry sidecar deps from JFrog
  pip:
    name: "{{ mlx_telemetry_packages }}"
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--index-url {{ jfrog_pypi_index }} --trusted-host {{ jfrog_pypi_trusted_host }}"
    state: present
  become: true
  when:
    - mlx_use_jfrog | bool
    - mlx_server_backend == 'mlx_lm_sidecar'
```

- [ ] **Step 2: Create the sidecar LaunchDaemon template**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- /Library/LaunchDaemons/com.hydra.mlx-telemetry.plist — Managed by Ansible (llm-mlx role). -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hydra.mlx-telemetry</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{ mlx_venv }}/bin/python</string>
    <string>-m</string><string>llm_telemetry.proxy</string>
    <string>--upstream</string><string>http://127.0.0.1:{{ mlx_port }}</string>
    <string>--metrics-port</string><string>{{ mlx_metrics_port }}</string>
    <string>--model-name</string><string>{{ mlx_default_model | basename }}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OTEL_EXPORTER_OTLP_ENDPOINT</key><string>127.0.0.1:4317</string>
    <key>OTEL_SERVICE_NAME</key><string>mlx-{{ hydra_node_id | default('node') }}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>/var/log/hydra/mlx-telemetry.log</string>
  <key>StandardErrorPath</key><string>/var/log/hydra/mlx-telemetry-error.log</string>
</dict>
</plist>
```

- [ ] **Step 3: Add a restart handler**

In `handlers/main.yml` add:

```yaml
- name: restart mlx-telemetry
  shell: |
    launchctl bootout system/com.hydra.mlx-telemetry 2>/dev/null || true
    launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-telemetry.plist
  become: true
  ignore_errors: true
```

- [ ] **Step 4: Validate**

Run: `cd ansible && ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml --tags mlx --syntax-check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/llm-mlx/
git commit -m "feat(llm-mlx): add llm-telemetry sidecar option for mlx_lm.server metrics"
```

---

## Task 4C (alternative): keep `mlx_lm.server` as-is

> No new code. If `mlx_server_backend == 'mlx_lm'`, Tasks 4A/4B are skipped. Tasks 1,2,3,6,7,8 still apply. Inference-layer token metrics are unavailable; only host/GPU/power metrics and crash detection function. Document this limitation in `files/README.md`.

- [ ] **Step 1:** Add a note to `files/README.md` under a new "Server backends" heading listing the three `mlx_server_backend` values and their telemetry trade-offs.
- [ ] **Step 2: Commit** `git commit -am "docs(llm-mlx): document mlx_server_backend options"`

---

## Task 5: Wire MLX `/metrics` into discovery

**Files:**
- Modify: `ansible/roles/llm-discovery/tasks/main.yml`
- Modify: `ansible/roles/llm-mlx/vars/main.yml` (set effective metrics port)

**Interfaces:**
- Consumes: `mlx_server_backend`, `mlx_port`, `mlx_metrics_port`.
- Produces: an entry in `/etc/hydra/discovered.yml` with `name: mlx`, `port: <metrics port>` that the existing `otel-agent-config.yaml.j2` turns into a scrape job.

- [ ] **Step 1: Compute the effective metrics port**

In `vars/main.yml` add:

```yaml
# vllm-mlx serves /metrics on the main port; the sidecar uses a separate port.
mlx_effective_metrics_port: "{{ mlx_port if mlx_server_backend == 'vllm_mlx' else mlx_metrics_port }}"
```

- [ ] **Step 2: Append an MLX provider to discovery**

In `llm-discovery/tasks/main.yml`, after the "Build discovered endpoint list" block, add:

```yaml
- name: Add MLX /metrics endpoint to discovery (when MLX deployed on this node)
  set_fact:
    _endpoints_to_probe: >-
      {{ _endpoints_to_probe + [{'provider': 'mlx',
         'url': 'http://127.0.0.1:' + mlx_port | string + '/v1',
         'port': mlx_effective_metrics_port | string,
         'api_key': none}] }}
  when: "'mlx' in (llm_frameworks | default([]))"
```

- [ ] **Step 3: Verify the OTEL agent picks it up**

The existing `otel-agent-config.yaml.j2` already loops `discovered.providers` to build `prometheus/llm_<name>` scrape jobs and the metrics pipeline — no change needed. Confirm:

Run: `rg -n "discovered.providers" ansible/roles/monitoring-cfg/templates/otel-agent-config.yaml.j2`
Expected: matches in receivers and service.pipelines.

- [ ] **Step 4: Validate**

Run: `cd ansible && ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml --tags llm-discovery --syntax-check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add ansible/roles/llm-discovery/ ansible/roles/llm-mlx/vars/main.yml
git commit -m "feat(discovery): register MLX /metrics endpoint for OTEL scraping"
```

---

## Task 6: Alertmanager SMTP receiver + MLX alert rules

**Files:**
- Modify: `monitoring/alertmanager/alertmanager.yml`
- Modify: `monitoring/.env` (SMTP vars)
- Create: `monitoring/prometheus/rules/mlx-alerts.yml`

**Interfaces:**
- Produces: an `email-hydra` receiver and MLX alert rules `MLXServerDown`, `MLXMetalFailure`, `MLXMemoryPressure`.

- [ ] **Step 1: Add SMTP vars to `monitoring/.env`**

```bash
ALERTMANAGER_SMTP_SMARTHOST=smtp.hydra.local:587
ALERTMANAGER_SMTP_FROM=hydra-alerts@hydra.local
ALERTMANAGER_SMTP_TO=ops@hydra.local
ALERTMANAGER_SMTP_USER=hydra-alerts
ALERTMANAGER_SMTP_PASSWORD=changeme-smtp
```

- [ ] **Step 2: Add global SMTP + email receiver in `alertmanager.yml`**

Add to `global:`:

```yaml
global:
  resolve_timeout: 5m
  smtp_smarthost: "${ALERTMANAGER_SMTP_SMARTHOST}"
  smtp_from: "${ALERTMANAGER_SMTP_FROM}"
  smtp_auth_username: "${ALERTMANAGER_SMTP_USER}"
  smtp_auth_password: "${ALERTMANAGER_SMTP_PASSWORD}"
  smtp_require_tls: true
```

Add a receiver and route critical alerts to both Slack and email:

```yaml
receivers:
  - name: slack-hydra
    slack_configs: [ ... existing ... ]
  - name: email-hydra
    email_configs:
      - to: "${ALERTMANAGER_SMTP_TO}"
        send_resolved: true
        headers:
          subject: '[{{ .Status | toUpper }}] {{ .GroupLabels.alertname }} on {{ .CommonLabels.node_id }}'
  - name: slack-and-email
    slack_configs:
      - api_url: "${ALERTMANAGER_SLACK_WEBHOOK}"
        channel: "${ALERTMANAGER_SLACK_CHANNEL:-#hydra-alerts}"
    email_configs:
      - to: "${ALERTMANAGER_SMTP_TO}"
        send_resolved: true
```

Update the critical route receiver from `slack-hydra` to `slack-and-email`:

```yaml
    - match:
        severity: critical
      receiver: slack-and-email
      group_wait: 10s
      repeat_interval: 1h
```

- [ ] **Step 3: Create `monitoring/prometheus/rules/mlx-alerts.yml`**

```yaml
groups:
  - name: mlx-inference
    interval: 30s
    rules:
      - alert: MLXServerDown
        expr: up{job=~"llm-mlx"} == 0
        for: 1m
        labels: { severity: critical, team: ml-platform, category: mlx }
        annotations:
          summary: "MLX server down on {{ $labels.node_id }}"
          node_id: "{{ $labels.node_id }}"
          description: "The MLX inference server is not serving /metrics for 1m. launchd KeepAlive should restart it; if this fires repeatedly the process is crash-looping."

      - alert: MLXMetalFailure
        expr: increase(mlx_metal_failures_total[5m]) > 0
        for: 0m
        labels: { severity: critical, team: ml-platform, category: mlx, pagerduty: trigger }
        annotations:
          summary: "MLX Metal/GPU command-buffer failure on {{ $labels.node_id }}"
          node_id: "{{ $labels.node_id }}"
          description: "A Metal command buffer failure (check_error / MetalAllocator) was detected in the MLX error log. Likely unified-memory exhaustion. Reduce context/batch or move the model to a larger node."

      - alert: MLXMemoryPressure
        expr: apple_gpu_memory_used_bytes / apple_gpu_memory_total_bytes > 0.90
        for: 2m
        labels: { severity: warning, team: ml-platform, category: mlx }
        annotations:
          summary: "MLX unified-memory pressure >90% on {{ $labels.node_id }}"
          node_id: "{{ $labels.node_id }}"
          description: "Approaching the 16 GB ceiling — OOM SIGABRT risk. Pre-emptive: lower max-kv-size or drain node."
```

- [ ] **Step 4: Validate alert rules**

Run: `docker run --rm -v $(pwd)/monitoring/prometheus/rules:/rules prom/prometheus:v2.53.0 promtool check rules /rules/mlx-alerts.yml`
Expected: `SUCCESS: 3 rules found`.

- [ ] **Step 5: Validate alertmanager config**

Run: `docker run --rm -v $(pwd)/monitoring/alertmanager:/am prom/alertmanager:v0.27.0 amtool check-config /am/alertmanager.yml`
Expected: config OK (env vars are substituted at container start; use placeholder values to lint if needed).

- [ ] **Step 6: Commit**

```bash
git add monitoring/alertmanager/alertmanager.yml monitoring/.env monitoring/prometheus/rules/mlx-alerts.yml
git commit -m "feat(monitoring): add Alertmanager SMTP receiver and MLX alert rules"
```

---

## Task 7: `mlx_metal_failures_total` log exporter

**Files:**
- Create: `ansible/roles/llm-mlx/files/mlx_log_exporter.py`
- Create: `ansible/roles/llm-mlx/templates/com.hydra.mlx-logexporter.plist.j2`
- Modify: `ansible/roles/llm-mlx/tasks/main.yml` (deploy + bootstrap)
- Modify: `ansible/roles/llm-mlx/handlers/main.yml`

**Interfaces:**
- Consumes: `mlx_logexporter_port` (add to vars, e.g. `11502`), the MLX error log path `/var/log/hydra/mlx-server-error.log`.
- Produces: `mlx_metal_failures_total` and `mlx_gpu_errors_total` counters on `:{{ mlx_logexporter_port }}/metrics`. The Salt beacon (Task 8) reads the same log for event-driven action; this exporter feeds the `MLXMetalFailure` alert (Task 6).

- [ ] **Step 1: Add var**

In `vars/main.yml`: `mlx_logexporter_port: 11502`

- [ ] **Step 2: Create `files/mlx_log_exporter.py`**

```python
#!/usr/bin/env python3
"""Tail the MLX error log and expose Metal/GPU failure counters for Prometheus.

Uses substring matching (no regex) against known fatal MLX/Metal signatures so it
stays dependency-light and works with the venv's prometheus_client.
"""
import argparse
import os
import time
from prometheus_client import Counter, start_http_server

METAL_SIGNATURES = (
    "check_error",
    "MetalAllocator",
    "CommandBuffer",
    "Insufficient Memory",
    "MTLCommandBuffer",
    "[METAL]",
)
GPU_SIGNATURES = ("AGXMetal", "GPU command", "eval_gpu")

metal_failures = Counter("mlx_metal_failures_total", "MLX Metal command-buffer failures")
gpu_errors = Counter("mlx_gpu_errors_total", "MLX GPU errors")


def follow(path):
    while not os.path.exists(path):
        time.sleep(2)
    with open(path, "r", errors="ignore") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(1)
                continue
            if any(sig in line for sig in METAL_SIGNATURES):
                metal_failures.inc()
            if any(sig in line for sig in GPU_SIGNATURES):
                gpu_errors.inc()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/var/log/hydra/mlx-server-error.log")
    ap.add_argument("--port", type=int, default=11502)
    args = ap.parse_args()
    start_http_server(args.port)
    follow(args.log)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create the LaunchDaemon template `com.hydra.mlx-logexporter.plist.j2`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist — Managed by Ansible (llm-mlx role). -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hydra.mlx-logexporter</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{ mlx_venv }}/bin/python</string>
    <string>{{ mlx_dir }}/mlx_log_exporter.py</string>
    <string>--log</string><string>{{ llm_log_dir }}/mlx-server-error.log</string>
    <string>--port</string><string>{{ mlx_logexporter_port }}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardErrorPath</key><string>{{ llm_log_dir }}/mlx-logexporter-error.log</string>
</dict>
</plist>
```

- [ ] **Step 4: Deploy in `tasks/main.yml`** (after the server LaunchDaemon block)

```yaml
- name: Install prometheus-client for log exporter
  pip:
    name: ["prometheus-client>=0.20.0"]
    virtualenv: "{{ mlx_venv }}"
    extra_args: "--index-url {{ jfrog_pypi_index }} --trusted-host {{ jfrog_pypi_trusted_host }}"
  become: true
  when: mlx_use_jfrog | bool

- name: Copy MLX log exporter
  copy:
    src: mlx_log_exporter.py
    dest: "{{ mlx_dir }}/mlx_log_exporter.py"
    mode: "0755"
  become: true
  notify: restart mlx-logexporter

- name: Install MLX log exporter LaunchDaemon
  template:
    src: com.hydra.mlx-logexporter.plist.j2
    dest: /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist
    owner: root
    group: wheel
    mode: "0644"
  become: true
  notify: restart mlx-logexporter
```

- [ ] **Step 5: Add handler**

```yaml
- name: restart mlx-logexporter
  shell: |
    launchctl bootout system/com.hydra.mlx-logexporter 2>/dev/null || true
    launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-logexporter.plist
  become: true
  ignore_errors: true
```

- [ ] **Step 6: Wire the log-exporter port into discovery**

Add a second discovery entry (provider `mlx_errors`, port `mlx_logexporter_port`) analogous to Task 5 Step 2, so the counters reach Prometheus.

- [ ] **Step 7: Validate**

Run: `python -c "import ast; ast.parse(open('ansible/roles/llm-mlx/files/mlx_log_exporter.py').read())"`
Expected: no output (valid syntax).

- [ ] **Step 8: Commit**

```bash
git add ansible/roles/llm-mlx/
git commit -m "feat(llm-mlx): add mlx_metal_failures_total log exporter"
```

---

## Task 8: Salt crash beacon + reactor (detect → email → restart → drain)

**Files:**
- Create: `ansible/roles/salt-minion/templates/beacons.conf.j2`
- Modify: `ansible/roles/salt-minion/tasks/main.yml` (deploy beacon config)
- Create: `ansible/roles/salt-master/templates/reactor-mlx_crash.sls.j2`
- Create: `ansible/roles/salt-master/templates/mlx_crash_handler.sls.j2`
- Modify: `ansible/roles/salt-master/templates/salt-master.conf.j2` (register reactor + smtp returner)
- Modify: `ansible/roles/salt-master/tasks/main.yml` (deploy the new state/reactor)

**Interfaces:**
- The minion `log` beacon watches `/var/log/hydra/mlx-server-error.log` and fires event tag `hydra/mlx/crash`.
- The master reactor maps `hydra/mlx/crash` → state `mlx_crash_handler` which (a) sends SMTP email, (b) restarts the MLX LaunchDaemon, (c) marks the node draining via the LiteLLM admin API.

- [ ] **Step 1: Create the minion beacon config `beacons.conf.j2`**

```jinja2
# /usr/local/etc/salt/minion.d/beacons.conf (macOS) — Managed by Ansible.
beacons:
  log:
    - file: {{ llm_log_dir | default('/var/log/hydra') }}/mlx-server-error.log
    - tags:
        mlx_metal:
          regex: '.*(check_error|MetalAllocator|CommandBuffer|Insufficient Memory|abort).*'
    - interval: 5
```

- [ ] **Step 2: Deploy the beacon config from `salt-minion/tasks/main.yml`**

After "Deploy Salt Minion config", add:

```yaml
- name: Ensure minion.d directory exists
  file:
    path: "{{ salt_minion_config | dirname }}/minion.d"
    state: directory
    mode: "0755"
  become: true

- name: Deploy MLX crash beacon
  template:
    src: beacons.conf.j2
    dest: "{{ salt_minion_config | dirname }}/minion.d/beacons.conf"
    mode: "0640"
  become: true
  when: "'mlx' in (llm_frameworks | default([]))"
  notify:
    - restart salt-minion (macOS)
    - restart salt-minion (Linux)
```

- [ ] **Step 3: Create the reactor `reactor-mlx_crash.sls.j2`**

```jinja2
# /srv/salt/reactors/mlx_crash.sls — Managed by Ansible.
# Fires when a minion's log beacon matches an MLX/Metal crash signature.
{% raw %}
handle_mlx_crash:
  local.state.apply:
    - tgt: {{ data['id'] }}
    - arg:
      - base.mlx_crash_handler
    - kwarg:
        pillar:
          crash_node: {{ data['id'] }}
          crash_line: {{ data['data'].get('match', 'unknown') | json }}
{% endraw %}
```

- [ ] **Step 4: Create the handler state `mlx_crash_handler.sls.j2`**

```jinja2
# /srv/salt/base/mlx_crash_handler.sls — Managed by Ansible.
# Email + restart + drain on MLX crash. Runs on the affected minion.
{% raw %}
restart_mlx_server:
  cmd.run:
    - name: |
        launchctl bootout system/com.hydra.mlx-server 2>/dev/null || true
        launchctl bootstrap system /Library/LaunchDaemons/com.hydra.mlx-server.plist

notify_ops_mlx_crash:
  cmd.run:
    - name: >
        printf 'Subject: [HYDRA] MLX crash on %s\n\nNode %s logged an MLX/Metal failure:\n%s\n'
        "{{ pillar['crash_node'] }}" "{{ pillar['crash_node'] }}" "{{ pillar['crash_line'] }}"
        | /usr/sbin/sendmail -t -f hydra-alerts@hydra.local ops@hydra.local
    - require:
      - cmd: restart_mlx_server
{% endraw %}
```

> Drain step: if the LiteLLM admin API is reachable, add a third `cmd.run` that POSTs a cooldown for this node's backend. Left as a follow-up because LiteLLM's per-backend cooldown is currently config-driven (`cooldown_time: 60`) and auto-recovers; the restart above is the primary remediation.

- [ ] **Step 5: Register the reactor in `salt-master.conf.j2`**

```jinja2
reactor:
  - 'salt/beacon/*/mlx_metal/':
    - /srv/salt/reactors/mlx_crash.sls

# SMTP returner config (master-side, optional alternate path to sendmail)
smtp.from: 'hydra-alerts@hydra.local'
smtp.to: 'ops@hydra.local'
smtp.host: '{{ salt_smtp_host | default("smtp.hydra.local") }}'
smtp.tls: True
```

- [ ] **Step 6: Deploy the new state + reactor from `salt-master/tasks/main.yml`**

Add two `template:` tasks (mirroring the existing `grain_report.sls` deploy) writing `mlx_crash_handler.sls` to `{{ salt_states_dir }}/base/` and `mlx_crash.sls` to `{{ salt_reactors_dir }}/`.

- [ ] **Step 7: Validate**

Run:
```bash
cd ansible
ansible-playbook -i inventories/llm/hosts.yml salt-onboarding.yml --syntax-check
python -c "import yaml,glob; [yaml.safe_load(open(f).read().replace('{% raw %}','').replace('{% endraw %}','')) for f in glob.glob('ansible/roles/salt-*/templates/*.sls.j2') if 'beacon' not in f]" 2>/dev/null || echo "jinja-templated SLS — lint after render"
```
Expected: syntax-check passes.

- [ ] **Step 8: Commit**

```bash
git add ansible/roles/salt-minion/ ansible/roles/salt-master/
git commit -m "feat(salt): MLX crash beacon + reactor (restart + SMTP report)"
```

---

## Task 9 (optional, P3): Add Tempo trace backend

> Today LiteLLM emits OTEL traces that land in Opik. Add Tempo only if you want a dedicated, Grafana-native trace store with span search. Otherwise skip — Opik already covers LLM tracing.

**Files:**
- Modify: `monitoring/docker-compose.yml`
- Create: `monitoring/tempo/tempo-config.yml`
- Modify: `monitoring/grafana/provisioning/datasources/datasources.yml`
- Modify: `monitoring/otel-collector/otel-gateway-config.yaml` (add `otlp/tempo` exporter on the traces pipeline)

- [ ] **Step 1: Add the Tempo service to `docker-compose.yml`**

```yaml
  tempo:
    image: grafana/tempo:2.5.0
    container_name: tempo
    restart: unless-stopped
    mem_limit: 512m
    command: ["-config.file=/etc/tempo/tempo-config.yml"]
    volumes:
      - ./tempo/tempo-config.yml:/etc/tempo/tempo-config.yml:ro
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"   # Tempo query
    networks: [hydra-monitor]
```

Add `tempo-data:` to the `volumes:` block.

- [ ] **Step 2: Create `monitoring/tempo/tempo-config.yml`**

```yaml
server:
  http_listen_port: 3200
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
storage:
  trace:
    backend: local
    local:
      path: /var/tempo/blocks
    wal:
      path: /var/tempo/wal
compactor:
  compaction:
    block_retention: 168h
```

- [ ] **Step 3: Add Tempo datasource in `datasources.yml`**

```yaml
  - name: Tempo
    type: tempo
    access: proxy
    url: http://tempo:3200
    uid: tempo
```

- [ ] **Step 4: Route traces from the OTEL gateway to Tempo**

In `otel-gateway-config.yaml`, add an `otlp/tempo` exporter (`endpoint: tempo:4317`, `tls.insecure: true`) and include it in the `traces` pipeline alongside the existing Opik exporter.

- [ ] **Step 5: Validate compose**

Run: `cd monitoring && docker compose config -q`
Expected: no output (valid).

- [ ] **Step 6: Commit**

```bash
git add monitoring/docker-compose.yml monitoring/tempo/ monitoring/grafana/provisioning/datasources/datasources.yml monitoring/otel-collector/otel-gateway-config.yaml
git commit -m "feat(monitoring): add Tempo trace backend and gateway export"
```

---

## Task 10 (optional, P3): MLX Grafana dashboard

**Files:**
- Create: `monitoring/grafana/dashboards/mlx-inference.json`

**Panels (PromQL):**

```promql
# TTFT p95 (vllm-mlx exposes vllm:time_to_first_token_seconds)
histogram_quantile(0.95, sum(rate(vllm:time_to_first_token_seconds_bucket{node_id=~"$node"}[5m])) by (le, node_id))

# Tokens/sec
sum(rate(vllm:generation_tokens_total{node_id=~"$node"}[1m])) by (node_id)

# Request queue depth
sum(vllm:num_requests_waiting{node_id=~"$node"}) by (node_id)

# Metal failures (from Task 7 exporter)
increase(mlx_metal_failures_total{node_id=~"$node"}[1h])

# Unified-memory pressure
apple_gpu_memory_used_bytes{node_id=~"$node"} / apple_gpu_memory_total_bytes{node_id=~"$node"}
```

> If `mlx_server_backend != 'vllm_mlx'`, swap the `vllm:*` series for the `llm_*` series exported by the `llm-telemetry` sidecar (`llm_inference_duration_seconds`, `llm_tokens_per_second`, `llm_queue_depth`).

- [ ] **Step 1:** Author the dashboard JSON with the panels above and the standard `$cluster`/`$node`/`$pool` template variables (copy the variable block from `node-deep-dive.json`).
- [ ] **Step 2: Validate JSON** — Run: `python -c "import json; json.load(open('monitoring/grafana/dashboards/mlx-inference.json'))"` — Expected: no error.
- [ ] **Step 3: Commit** — `git add monitoring/grafana/dashboards/mlx-inference.json && git commit -m "feat(grafana): MLX inference dashboard"`

---

## Self-Review

**Spec coverage:**

| Requirement (from request) | Task(s) |
|----------------------------|---------|
| MLX automation "latest" | Task 1 (mlx-lm 0.31.3) |
| Carry deps locally; pip/ruby from JFrog | Task 1 (JFrog index + wheel fallback; pip/ruby excluded) |
| Scalable deployment | Task 4A (continuous batching, paged cache) |
| Telemetry / observability / tracing integration | Tasks 4A/4B (/metrics), 5 (scrape wiring), 9 (Tempo), 10 (dashboard) |
| LLM / CPU / power monitoring | Existing apple-silicon-exporter + otel-mac-agent (verified present); LLM via Task 4/5 |
| Events | Task 8 (Salt event bus beacon/reactor) |
| Crash handling | Task 3 (guardrails), launchd KeepAlive (existing), Task 8 (detect+restart) |
| Report back via SMTP | Task 6 (Alertmanager email) + Task 8 (Salt reactor sendmail) — both, per request |
| Ansible vs Salt recommendation | Decision section: both; Salt owns crash loop |
| Crash-log root cause (Metal OOM) addressed | Task 3 guardrails + Task 6 `MLXMemoryPressure`/`MLXMetalFailure` + Task 7 metric |

No gaps.

**Placeholder scan:** No `TBD`/`TODO-implement`/"handle edge cases" left except explicit `# TODO v2: move to vault` markers (intentional, matches existing repo convention) and the documented LiteLLM-drain follow-up in Task 8 Step 4.

**Type/name consistency:** `mlx_server_backend` values (`vllm_mlx` / `mlx_lm_sidecar` / `mlx_lm`) are used identically in Tasks 1, 4A, 4B, 4C, 5. `mlx_port=11500`, `mlx_metrics_port=11501`, `mlx_logexporter_port=11502` are consistent across Tasks 1, 5, 7. Metric names `mlx_metal_failures_total` / `mlx_gpu_errors_total` match between Task 6 (alert), Task 7 (exporter), Task 10 (dashboard). Event tag `mlx_metal` / `hydra/mlx/crash` consistent across Task 8 beacon and reactor.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-22-mlx-cluster-automation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Suggested model split per the project rules: `haiku` for Tasks 2, 4C, 5 (≤3 files + exact snippets); `sonnet` for Tasks 1, 4A, 4B, 6, 7, 8 (multi-file / integration judgment).

**2. Inline Execution** — execute tasks in this session with checkpoints for review.

Recommended order: **1 → 2 → 3 → (choose 4A/4B/4C) → 5 → 6 → 7 → 8**, then optional **9 → 10**. Each task ends in an independently testable, committable deliverable.

