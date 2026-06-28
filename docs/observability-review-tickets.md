# Hydra Observability — Principal Architect Review & Tickets

**Date:** 2026-06-26
**Scope:** Multi-engine inference observability (vLLM, vLLM-MLX, Apple MLX, SGLang, llm-d) across the hydra fleet.
**Review panel:**

| Architect | Area | Tickets | 🍩 Donuts |
|---|---|---|---|
| **Priya Nair** — Principal LLM Inference Architect | LLM engine telemetry, KV/cache, parallelism, tracing, evaluation/Opik | 10 | 🍩×10 |
| **Marcus Chen** — Principal SRE / Observability Architect | Alerting correctness, RDMA/network fabric, SLOs, pipeline reliability | 8 | 🍩×8 |
| **Diego Ramirez** — Principal Platform / Ansible Architect | Provisioning, air-gap, supply-chain, discovery, CI | 9 | 🍩×9 |

> **Donut rule:** one 🍩 awarded per ticket raised. Running tally at the bottom.

> **Cross-cutting P0 (joint ownership — Priya + Marcus):** both the LLM and SRE reviews independently found that the entire `llm_*` SDK-based alert set is **dead** against the engines actually deployed (which emit `vllm:*` / `sglang:*`). This is the single most important fix and is captured as **LLM-1 / SRE-1** below — treat them as one workstream anchored by the `hydra:llm:*` normalization layer (**LLM-3**).

---

## A. LLM Inference Architect — Priya Nair 🍩×9

### LLM-1 · Reconcile dead `llm_*` alert rules with metrics engines actually emit · **P0** · M 🍩
- **Problem:** `apple-silicon-monitoring/alerts/llm-inference.yaml` + `hallucination-detection.yaml` key 100% on `llm_*` SDK metrics, produced only by the manually-wrapped `llm_telemetry` SDK or the mock server. Real engines emit `vllm:*`/`sglang:*`. Inference alerting is silently non-functional.
- **Fix:** Make engine-native metrics the source of truth; rebind operational alerts onto the `hydra:llm:*` normalization layer (LLM-3). Add meta-alert `LLMMetricsMissing = absent(hydra:llm:up{provider=~"vllm.*|sglang|mlx"})`.
- **Files:** `apple-silicon-monitoring/alerts/{llm-inference,hallucination-detection}.yaml`, `monitoring/prometheus/rules/`, `deploy/kubernetes/prometheus-rules.yaml`.

### LLM-2 · Fix vLLM-MLX dashboard metric names (`vllm:` vs `vllm_mlx_`) · **P1** · S 🍩
- **Problem:** `monitoring/grafana/dashboards/mlx-inference.json` queries `vllm:*`, but docs/alerts reference `vllm_mlx_*`. Primary MLX dashboard likely renders empty on `vllm-mlx==0.4.0rc1`.
- **Fix:** Capture authoritative `:11500/metrics` from a live node; pick canonical names (or relabel at gateway); add CI lint comparing dashboard `expr` to a known-metrics allowlist.
- **Files:** `mlx-inference.json`, `monitoring/otel-collector/otel-gateway-config.yaml`, docs.

### LLM-3 · Cross-engine normalization layer (`hydra:llm:*` recording rules) · **P1** · M 🍩
- **Problem:** No recording rules exist anywhere (`rg "record:"` empty). Each engine uses different names → per-engine dashboards/alerts, no fleet rollups.
- **Fix:** Recording-rule group mapping vLLM/SGLang/MLX/SDK → unified `hydra:llm:*` (aligned to OTel GenAI semconv): TTFT p50/p95/p99, tokens/s, requests_running/waiting, kv_cache_usage, error_rate. Commit the mapping table.
- **Files:** new `monitoring/prometheus/rules/hydra-llm-normalization.yml`, fleet dashboards.

### LLM-4 · vLLM scheduler & cache observability (KV usage, prefix-hit, preemptions, running/waiting) · **P1** · M 🍩
- **Problem:** `vllm:gpu_cache_usage_perc` scraped but undashboarded; prefix/radix hit-rate, `vllm:num_preemptions_total`, running-vs-waiting all uncollected — the leading indicators of paged-attention thrashing on 16 GB M4 nodes.
- **Fix:** Recording rules for prefix-hit ratio, preemption rate, running/waiting ratio; "KV & Scheduler" dashboard row; alerts `KVCacheSaturation`, `PagedAttentionThrashing`, `PrefixCacheColdspot`.
- **Files:** `mlx-inference.json`, normalization rules, `monitoring/prometheus/rules/mlx-alerts.yml`.

### LLM-5 · Instrument pure Apple MLX (`mlx_lm`) backend with perf metrics · **P1** · L 🍩
- **Problem:** When `mlx_server_backend` ∈ {mlx_lm, mlx_lm_sidecar}, the only telemetry is crash counters — zero throughput/TTFT/tokens/active-requests/KV.
- **Fix:** Metrics sidecar (extend `mlx_logexporter` pattern) or SDK middleware emitting tokens/s, TTFT, TPOT, active requests, KV occupancy vs `mlx_max_kv_size`, wired-mem vs `mlx_memory_limit_mb`; register endpoint in discovery; normalize via LLM-3.
- **Files:** `ansible/roles/llm-mlx/{files,templates,vars}`, `ansible/roles/llm-discovery/*`.

### LLM-6 · Wire engine OTLP traces (prefill/decode/queue spans) into Tempo/Opik · **P2** · M 🍩
- **Problem:** Tempo + Opik exporters configured and idle; no engine emits spans (plists lack `--otlp-traces-endpoint`). No per-request latency decomposition.
- **Fix:** Add `--otlp-traces-endpoint` to vLLM/MLX plists; SDK spans where engine-native tracing unavailable; sampling policy; Tempo exemplar links from latency panels.
- **Files:** `com.hydra.vllm-server.plist.j2`, `com.hydra.mlx-server.plist.j2`, agent + gateway configs.

### LLM-7 · Integrate SGLang into discovery, dashboards, alerts · **P2** · M 🍩
- **Problem:** No `llm-sglang` role; absent from `valid_llm_providers`/`llm_probe_ports` → never auto-discovered; `sglang:*` metrics unused even if scraped.
- **Fix:** Add SGLang to discovery + catalog; source recording rules → `hydra:llm:*` (`cache_hit_rate`, `num_running_reqs`, `gen_throughput`); SGLang dashboard + alerts.
- **Files:** `ansible/group_vars/all.yml`, `llm-discovery`, new dashboard, rules.

### LLM-8 · Advanced engine internals: TP/PP/EP topology, MoE expert load, spec-decode, LoRA · **P3** · L 🍩
- **Problem:** `tensor_parallel_groups` defined (2×2 TP via RoCE) but no per-rank correlation, no MoE expert load, no `vllm:spec_decode_*`, no `vllm:lora_requests_info`.
- **Fix:** `tp_group`/`rank` relabel; spec-decode acceptance rate + regression alert; LoRA per-adapter volume; correlate with RDMA fabric (SRE-3/4).
- **Files:** agent template relabel, `group_vars/all.yml`, rules, dashboards.

### LLM-9 · Integrate llm-d (K8s pull) into push-based hydra · **P3** · L 🍩
- **Problem:** llm-d entirely absent. EPP routing, KV-cache-aware routing, P/D disaggregation, NIXL KV-transfer over RDMA all invisible; pull-vs-push architecture mismatch.
- **Fix:** In-cluster OTEL collector (prometheus receiver) scraping EPP/gateway/prefill/decode pods → OTLP to hydra gateway with consistent labels; llm-d dashboard + alerts; ADR documenting the bridge.
- **Files:** new `deploy/kubernetes/llm-d-collector.yaml`, gateway config, dashboard/alerts, ADR.

### LLM-10 · Activate Opik for LLM tracing + online/offline evaluation · **P2** · L 🍩
- **Problem:** The full Opik stack is **deployed but idle**. `monitoring/docker-compose.yml` runs `opik-backend/frontend/mysql/clickhouse/redis` and `otel-gateway-config.yaml` has an `otlphttp/opik` exporter, but nothing drives it: traces are not shaped with GenAI/Opik conventions (so the trace UI is thin — no input/output, tokens, cost, thread/session), there are **no Opik projects/datasets/experiments**, **no online-evaluation rules** despite an air-gapped LiteLLM "judge LLM" already wired in compose, no prompt versioning, and no Grafana↔Opik linking. The perf-proxy now emits generic OTLP spans (LLM-6) but without Opik-native attributes Opik shows latency-only spans and zero scores. Net: the heuristic hallucination signals (proxy `--quality`) are the *only* quality source; the real judge-backed eval platform is dark.
- **Fix:**
  - **Span shaping:** enrich perf-proxy + engine spans with OTel-GenAI / OpenInference + Opik attributes (`gen_ai.prompt`/`completion`, usage tokens, model, `opik.project`/tags, `thread_id` from the request) so Opik renders full traces; optional opik-SDK middleware where capturing input/output payloads is acceptable, gated by a **privacy flag** (default off, mirrors the proxy's no-raw-text default).
  - **Online evaluation:** Opik automation rules scoring sampled traces via the air-gapped LiteLLM judge (hallucination, moderation, answer-relevance, RAG context-groundedness); export scores back as `opik:*` Prometheus metrics and fold into `hydra:llm:*` so `LLMHallucinationRisk*` alerts can be backed by **real judge scores**, not just entropy/repetition heuristics.
  - **Offline eval / regression:** Opik datasets of golden prompts + experiments run in CI and on model bumps; gate model promotion on score thresholds (ties into the golden-prompt canary idea).
  - **Prompt management:** register prompts/versions in the Opik prompt library; stamp deployed model+prompt version onto traces.
  - **Ansible + Salt:** air-gapped staging of `comet/opik-*` images; render Opik client config (`OPIK_URL_OVERRIDE`, `OPIK_PROJECT_NAME`, workspace, API key via vault) on nodes/gateway; wire perf-proxy `--opik`/privacy flags; mTLS + secrets per ANS-5; mirror the whole thing in the `salt/` tree (dual-tool parity).
  - **UX:** Grafana data-link from latency/quality panels to the matching Opik trace; project tile on the MLX/exo dashboard.
- **Acceptance:** a request to the `192.168.1.23` MLX node yields a full trace in the Opik UI (input/output/tokens/model/latency); ≥1 online-eval rule scores sampled traces via the judge and surfaces as `opik:*` feeding `hydra:llm:hallucination_risk`; a golden-prompt dataset+experiment runs with a pass/fail CI gate; everything air-gapped, secrets vaulted, TLS not `insecure`; Salt mirror at parity.
- **Files:** `monitoring/docker-compose.yml`, `monitoring/otel-collector/otel-gateway-config.yaml`, `ansible/roles/llm-common/files/llm_perf_proxy.py`, `llm-mlx`/`llm-exo` plists + vars, new `roles/opik-client` (or `monitoring-common`), `group_vars/all/{main,vault}.yml`, `monitoring/prometheus/rules/llm-quality.yml` (`opik:*` rules), Grafana dashboards, `monitoring/opik/init.sql` + `.env`, new CI eval workflow, `salt/` mirror.
- **Depends on:** LLM-6 (OTLP traces wired ✅), LLM-3 normalization ✅, the perf-proxy `--quality` layer ✅; **pairs with** the air-gapped judge gateway already in `docker-compose.yml`.

---

## B. SRE / Observability Architect — Marcus Chen 🍩×8

### SRE-1 · LLM inference alerts DEAD against natively-scraped engines · **P0** · M 🍩
- **Joint with LLM-1.** Default `mlx_server_backend=vllm_mlx` serves native `vllm:*`; alerts query SDK `llm_*` → model-down/stall/OOM/queue/latency never fire. Also label-space mismatch (`model` vs `model_name`). Fix via normalization + `absent()` guard + `promtool check rules` in CI.
- **Files:** `llm-inference.yaml`, new `llm-normalization.rules.yml`, agent template, docs.

### SRE-2 · No alerting on observability backends / ingest pipeline (+ orphaned PagerDuty) · **P0** · M 🍩
- **Problem:** If VictoriaMetrics/Loki/Tempo/OTEL-gateway fail, platform goes blind with no alert (Prometheus is self-monitoring only; no Tempo scrape; no `up`-based rules). Multiple alerts set `pagerduty: trigger` but the **active** Alertmanager has **no PagerDuty receiver** → critical pages degrade to Slack silently.
- **Fix:** `platform-backends.rules.yml` (`up==0` for VM/Loki/Tempo/gateway/AM); OTEL self-metrics alerts (`otelcol_exporter_send_failed_*`, queue saturation, refused points); remote_write failure/lag alerts; add `pagerduty-critical` receiver + route (`continue: true`); "Backbone health" dashboard.
- **Files:** `monitoring/prometheus/prometheus.yml`, new rules, `monitoring/alertmanager/alertmanager.yml`, dashboard.

### SRE-3 · RDMA/RoCE fabric completely unmonitored (silent throughput collapse) · **P1** · L 🍩
- **Problem:** RDMA is load-bearing for LargePool TP (AllReduce ~2 µs RDMA vs ~320 ms TCP). A PFC pause storm / ECN misconfig degrades ~100–600× with zero signal. No PFC/ECN/CNP/RoCE telemetry exists.
- **Fix:** Linux: node-exporter `--collector.infiniband` or sysfs counters (`rx/tx_pause`, `np_cnp_sent`, `out_of_buffer`, `rnr_nak_retry_err`). macOS Thunderbolt ConnectX: sidecar collector or `roce_hardware_present=0` gauge. Switch: `snmp_exporter` for per-priority pause/ECN/queue-3 drops. Alerts on pause storms/CNP/buffers; "RDMA Fabric" dashboard.
- **Files:** agent templates, new `roles/snmp-exporter`, apple-silicon-exporter, rules, dashboard.

### SRE-4 · Collective-comms (NCCL/RCCL) + DCGM NVLink profiling not collected · **P1** · L 🍩
- **Problem:** Explicit AllReduce targets (~8 ms RDMA vs ~1,280 ms TCP for 32B) but no collective metric; DCGM runs default counter set (no `DCGM_FI_PROF_NVLINK_*`).
- **Fix:** Custom DCGM counters CSV (NVLink/PCIe/SM/DRAM active) mounted + `-f`; NCCL profiler or periodic `nccl-tests` AllReduce probe → `nccl_allreduce_latency_seconds`/`busbw_gbps` per `tp_group`; SLO + regression alert; NVLink/AllReduce dashboard.
- **Files:** `dcgm-exporter.service.j2`, `hw-exporter/tasks/nvidia.yml`, new probe role, rules, dashboard.

### SRE-5 · No network bandwidth telemetry; Mac agent has no hostmetrics · **P1** · M 🍩
- **Problem:** No network dashboard anywhere; the rendered Mac agent (`monitoring-cfg` template) emits no host/network metrics; Linux agent lacks parity; MinIO model-pull BW untracked.
- **Fix:** Add `hostmetrics` (network/load/mem/fs/paging) to Mac agent; MinIO scrape (`minio_s3_traffic_*`); "Network & Throughput" dashboard (per-node RX/TX, uplink, model-pull duration); link-saturation alert.
- **Files:** agent template, MinIO scrape, rules, dashboard.

### SRE-6 · `apple_thermal_pressure` modeled two contradictory ways + duplicate/dead AM configs · **P1** · S–M 🍩
- **Problem:** `apple-silicon-alerts.yml` treats it as numeric level (`>=2`); `apple-silicon-hardware.yaml` treats it as value-1 with `{level=...}` label — one is broken. Only `/etc/prometheus/rules/*.yml` is loaded, so the entire `apple-silicon-monitoring/alerts/*` tree (incl. a second Alertmanager config) is **not deployed** but still drifting.
- **Fix:** Pin one thermal encoding (recommend numeric 0–3) and align exporter + rules; delete/archive unloaded alert tree; CI `promtool`/`amtool` gates.
- **Files:** `apple-silicon-alerts.yml`, `apple-silicon-monitoring/alerts/*`, exporter thermal emit, CI.

### SRE-7 · Alerting maturity: no burn-rate SLOs, weak routing/inhibition, placeholder runbooks · **P2** · M–L 🍩
- **Problem:** All alerts static single-threshold (over/under-pages); AM routes by severity only; inhibition `equal: instance` won't match hw alerts labeled `node_id`; runbook URLs are placeholders.
- **Fix:** Multi-window multi-burn-rate SLO alerts (e.g. 1h@14.4 + 6h@6) over `hydra:llm:*`; normalize `node_id`/`instance` labels so inhibition matches; team/category routing in active config; CI asserts every critical alert has a `runbook_url`.
- **Files:** new `slo-burnrate.rules.yml`, `alertmanager.yml`, all rule files, CI.

### SRE-8 · Exporter & pipeline reliability (powermetrics overhead, limiter drops, TLS, cardinality, retention) · **P2** · L 🍩
- **Problem:** powermetrics spawned per scrape (~1s root subprocess every 15s); agent `memory_limiter: 100MiB` silently drops under burst; `tls.insecure: true` everywhere; unbounded `quantization` label risk + `resource_to_telemetry_conversion` promotes all attrs; no VM retention/cardinality budget.
- **Fix:** Single long-lived powermetrics sampler with cached gauges; raise/right-size limiter; plan internal TLS (Salt PKI); bound cardinality (regex allowlist, prune per-core); precompute hot quantiles as recording rules; set VM retention + cardinality dashboard.
- **Files:** exporter `main.go`/`powermetrics.go`, agent template, gateway config, `docker-compose.yml`, rules.

---

## C. Platform / Ansible Architect — Diego Ramirez 🍩×9

### ANS-1 · Two divergent, conflicting provisioning paths for the same macOS nodes · **P0** · L 🍩
- **Problem:** `site.yml` (base+hw-exporter+monitoring-cfg on `all`) and `mac-monitoring.yml` (otel-mac-agent on `os_macos`) both render `/etc/hydra/otel-agent-config.yaml` from **different templates** and both install the same LaunchDaemons from different templates → last-run-wins, nondeterministic drift.
- **Fix:** Single source of truth for macOS (recommend `otel-mac-agent`); scope `site.yml` to Linux or import the mac playbook; make `hw-exporter` Linux-only; add managed-by assert guards.
- **Files:** `site.yml`, `mac-monitoring.yml`, roles `base`/`hw-exporter`/`monitoring-cfg`/`otel-mac-agent`.

### ANS-2 · `site.yml`/base+hw-exporter path broken + violates air-gap · **P0** · M 🍩
- **Problem:** Undefined `otel_agent_download_base`/`node_exporter_download_base`; missing `hydra_versions.{node_exporter,dcgm_exporter}` keys; internet/Homebrew/`docker pull` on hosts documented as air-gapped → play fails / breaks air-gap.
- **Fix:** Add missing version keys via `inventory_plugin.py`; convert fetches to air-gapped staging (`files/` → copy → extract); gate any online behavior behind `hydra_airgapped: false`.
- **Files:** `roles/base/tasks/main.yml`, `hw-exporter/tasks/{cpu,nvidia,apple}.yml`, `cluster.yml`, `group_vars/all.yml`.

### ANS-3 · Unify the two OTEL agent templates; close macOS LLM gap + Linux hostmetrics gap · **P1** · L 🍩
- **Problem:** Mac agent never reads `discovered.yml` → MLX/vLLM-MLX `/metrics` uncollected on Macs; Linux agent has no `hostmetrics` (no host network parity). Two hand-maintained templates drift.
- **Fix:** One canonical template (`roles/monitoring-common`) with Jinja toggles (`enable_hostmetrics`, `enable_llm_scrape`, …); both roles consume it; Mac path gains LLM scrape, Linux path gains host network.
- **Files:** both `otel-agent-config.yaml.j2`, `discovered.yml.j2`, `otel-mac-agent/tasks/configure.yml`.

### ANS-4 · Supply-chain integrity: enforce checksums + consumed version pins · **P1** · M 🍩
- **Problem:** No `checksum:` on any binary install in an air-gapped fleet; `apple_silicon_exporter: 1.0.0` pin defined but never consumed; `otel_version` printed not enforced.
- **Fix:** `hydra_artifact_checksums` (sha256 by artifact+arch) verified before install; assert `--version` == pin; add `node_exporter`/`dcgm_exporter`/`apple_silicon_exporter` to `versions:` and consume everywhere.
- **Files:** `base`, `hw-exporter`, `otel-mac-agent/tasks/install.yml`, `group_vars/all.yml`, `cluster.yml`.

### ANS-5 · TLS/secrets hardening: eliminate `insecure: true`, mTLS + ansible-vault · **P1** · M 🍩
- **Problem:** `insecure: true` on every OTLP/remote-write exporter; plaintext secrets committed (`minio_secret_key: changeme-v2`, `fleet_admin_password: changeme123`, `litellm_master_key: sk-hydra-master-changeme`); engine API keys hardcoded `none`.
- **Fix:** TLS block (ca/cert/key) default `insecure: false` with staged certs; move secrets to `ansible-vault`; CI secret-scan gate; per-engine auth in discovery schema.
- **Files:** both agent templates, `cluster.yml`, `inventories/llm/hosts.yml`, new `group_vars/all/vault.yml`.

### ANS-6 · Make LLM discovery engine-aware & resilient (registry-driven) · **P2** · L 🍩
- **Problem:** Fixed 4-port probe map, `/health`-only, no SGLang/llm-d, no per-engine `metrics_path`/auth; MLX special-cased; `mlx_metrics_port`/`mlx_effective_metrics_port` defined but unused; no re-discovery on restart.
- **Fix:** `llm_engine_catalog` (port/health_path/metrics_path/auth/scheme) driving probe + scrape; add SGLang/llm-d; carry metrics_path/auth into `discovered.yml`; remove dead MLX port vars; optional launchd/salt re-discovery.
- **Files:** `llm-discovery/*`, `group_vars/all.yml`, `inventory_plugin.py`, both OTEL templates, `llm-mlx/vars/main.yml`.

### ANS-7 · Dedicated `apple-silicon-exporter` role: per-arch staging, pin, checksum (M1–M5 + Linux) · **P2** · L 🍩
- **Problem:** Two install paths (build-from-source needing internet vs binary copy), two plist templates, hardcoded `:9101`, unused version pin, placeholder module path. No role to install the new cross-platform (M1–M5 + Linux) artifacts.
- **Fix:** New `roles/apple-silicon-exporter` owning per-arch staged binaries (`darwin_arm64`, `linux_arm64`, `linux_amd64`), pin from `hydra_versions`, sha256 verify, `--version` assert, single unit template, healthcheck; remove build/copy logic from other roles. *(Pairs with the new exporter delivered on branch `feat/silicon-exporter-m1-m5-linux`.)*
- **Files:** new `roles/apple-silicon-exporter/`, `hw-exporter/tasks/apple.yml`, `otel-mac-agent/tasks/apple_exporter.yml`, exporter repo.

### ANS-8 · Add CI: ansible-lint + molecule + idempotency/check-mode; fix stale `rolling-restart.yml` · **P2** · L 🍩
- **Problem:** No `.github/workflows`, `.ansible-lint`, or `molecule/`. `rolling-restart.yml` references `ai.hydra.*` LaunchAgents + vars that no longer exist (current units are `com.hydra.*` system daemons) → would fail. Mac restart handler swallows errors and never validates config.
- **Fix:** `.ansible-lint` (production) + CI (yamllint, ansible-lint, syntax-check, pytest, molecule idempotency/check-mode for critical roles); rewrite/delete `rolling-restart.yml`; add `otelcol validate` to mac handler.
- **Files:** new `.github/workflows/ansible.yml`, `.ansible-lint`, `roles/*/molecule/`, `rolling-restart.yml`, `otel-mac-agent/handlers/main.yml`.

### ANS-9 · Resolve multi-inventory drift (static `hosts.yml` vs dynamic `cluster.yml`) · **P3** · M 🍩
- **Problem:** `inventories/llm/hosts.yml` (`llm_frameworks`) and `cluster.yml` (`llm_endpoints`) independently define overlapping facts and already disagree (mlx/vllm vs ollama/llamacpp); LiteLLM port inconsistent across three places.
- **Fix:** One source of truth (recommend `cluster.yml`/`inventory_plugin.py`); derive the other or add a CI parity check; reconcile LiteLLM port.
- **Files:** `inventories/llm/hosts.yml`, `cluster.yml`, `inventory_plugin.py`, `group_vars/all.yml`.

---

## Priority rollup

| Priority | Tickets |
|---|---|
| **P0** | LLM-1 / SRE-1 (joint), SRE-2, ANS-1, ANS-2 |
| **P1** | LLM-2, LLM-3, LLM-4, LLM-5, SRE-3, SRE-4, SRE-5, SRE-6, ANS-3, ANS-4, ANS-5 |
| **P2** | LLM-6, LLM-7, LLM-10, SRE-7, SRE-8, ANS-6, ANS-7, ANS-8 |
| **P3** | LLM-8, LLM-9, ANS-9 |

**Recommended sequence:** (1) `hydra:llm:*` normalization (LLM-3) → unblocks LLM-1/SRE-1 alert rebind; (2) backbone self-monitoring + PagerDuty (SRE-2); (3) fix broken/duplicated Ansible provisioning (ANS-1, ANS-2); then P1 fabric/network/coverage work.

## 🍩 Donut tally

| Person | Donuts |
|---|---|
| Priya Nair (LLM) | 🍩🍩🍩🍩🍩🍩🍩🍩🍩🍩 (10) |
| Marcus Chen (SRE) | 🍩🍩🍩🍩🍩🍩🍩🍩 (8) |
| Diego Ramirez (Ansible) | 🍩🍩🍩🍩🍩🍩🍩🍩🍩 (9) |
| **Total** | **27 🍩** |
