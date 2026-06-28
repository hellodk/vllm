# Onboarding a Single MLX Node (end-to-end)

This runbook takes a bare Apple-silicon box running an ad-hoc `mlx_lm.server` and
turns it into a fully instrumented Hydra fleet node whose LLM metrics flow into
the central monitoring stack. The worked example uses **`192.168.1.23`** (an
experiment box — *not* `cylon`).

The starting point — what most people run by hand:

```bash
mlx_lm.server --model mlx-community/Qwen3.5-4B-MLX-8bit --host 0.0.0.0 --port 8080
```

That gives you an OpenAI-compatible API and **zero** observability:
`mlx_lm.server` exposes no Prometheus metrics, only crash lines in its log.

---

## 1. What instrumentation you get

| Layer | Source | Metrics |
|-------|--------|---------|
| Hardware | `apple-silicon-exporter` | GPU%, ANE%, thermal pressure, power |
| Engine crashes | `mlx_log_exporter.py` | `mlx_metal_failures_total`, `mlx_gpu_errors_total` |
| **Engine perf** | **`llm_perf_proxy.py` sidecar** | **`mlx:*` → TTFT, TPOT, tokens/s, running/waiting, KV (est.)** |
| Fabric (distributed) | `mlx_allreduce_probe.py` | `mlx_allreduce_latency_seconds`, `…busbw…` |
| Transport | OTEL agent | pushes everything to the gateway → VictoriaMetrics |

The perf-proxy is the key piece for the pure-MLX path: it sits in front of the
engine, forwards every request unchanged (including SSE streaming), and derives
the metrics the engine never emits. The engine moves to a loopback port and the
proxy takes over the public port — clients see no difference.

```
client ──▶ :11500 (perf-proxy) ──▶ 127.0.0.1:11510 (mlx_lm.server)
                 │
                 └─▶ :11501 /metrics  ──scrape──▶ OTEL agent ──▶ gateway
```

---

## 2. Pick a backend

| | `hydra_mlx_backend: mlx_lm` | `hydra_mlx_backend: vllm_mlx` (default) |
|---|---|---|
| Your exact `mlx_lm.server` workflow | ✅ identical engine | ❌ different server |
| Native Prometheus metrics | ❌ (perf-proxy fills the gap) | ✅ full `vllm:*` incl. true KV, prefix cache, preemptions |
| Continuous batching / paged KV | basic | ✅ |
| Recommendation | experimentation / parity with your CLI | **production** |

Both feed the same `hydra:llm:*` dashboards. Pick `mlx_lm` to mirror exactly what
you run today; pick `vllm_mlx` when you want the richest signal. Flip a single
variable to switch.

---

## 3. Add the node to inventory

`ansible/inventories/llm/hosts.yml` — add the box to a pool and point it at MLX:

```yaml
    fast_pool:
      vars:
        pool: fast
        llm_frameworks: [mlx]
        hydra_mlx_backend: mlx_lm          # pure mlx_lm + perf-proxy sidecar
      hosts:
        hydra-exp-23:
          ansible_host: 192.168.1.23
          mlx_default_model: mlx-community/Qwen3.5-4B-MLX-8bit
          mlx_port: 11500                  # public OpenAI API (proxy front door)
```

Notes:
* `mlx_port` is what clients hit (default 11500; set 8080 if you want to keep your
  current port). The engine is moved to `mlx_internal_port` (11510) automatically.
* `hydra_mlx_backend: mlx_lm` flips `mlx_sidecar_enabled` on, which makes both the
  role and `llm-discovery` agree to scrape the sidecar's `:11501/metrics`.

---

## 4. Deploy

```bash
cd ansible

# 4a. base tooling + the shared perf-proxy + hardware/log exporters + OTEL agent
ansible-playbook -i inventories/llm/hosts.yml site.yml --limit hydra-exp-23

# 4b. the MLX framework + perf-proxy sidecar
ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml \
  --limit hydra-exp-23 --tags mlx

# 4c. (re)render the OTEL agent so it discovers the new scrape target
ansible-playbook -i inventories/llm/hosts.yml site.yml \
  --limit hydra-exp-23 --tags monitoring
```

What lands on the host:
* `/Library/LaunchDaemons/com.hydra.mlx-server.plist` — engine on `127.0.0.1:11510`
* `/Library/LaunchDaemons/com.hydra.mlx-perf.plist` — proxy on `:11500`, metrics `:11501`
* `/Library/LaunchDaemons/com.hydra.mlx-logexporter.plist` — crash counters `:11502`
* `/etc/hydra/discovered.yml` — lists the `mlx_perf` provider
* `/etc/hydra/otel-agent-config.yaml` — scrapes it and forwards to the gateway

---

## 5. Verify

```bash
# engine is alive on the loopback port
curl -s 127.0.0.1:11510/v1/models | jq .

# proxy front door works (and is what clients use)
curl -s 127.0.0.1:11500/v1/models | jq .

# perf metrics exist (empty until first request)
curl -s 127.0.0.1:11501/metrics | grep '^mlx:'

# generate load, then look for TTFT / tokens
curl -s 127.0.0.1:11500/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"Qwen3.5-4B-MLX-8bit","messages":[{"role":"user","content":"hi"}],"stream":true}' >/dev/null
curl -s 127.0.0.1:11501/metrics | grep -E 'time_to_first_token|generation_tokens_total'
```

In Grafana: dashboard **“MLX / exo Perf + Fabric”** (`uid: mlx-perf-fabric`), filter
`provider = mlx_perf`. The cross-engine **“MLX Inference”** rollups also light up via
the `hydra:llm:*` recording rules.

> KV-cache usage for the pure-MLX path is a **best-effort estimate**
> (`active_tokens / (max_kv × concurrency)`) — `mlx_lm` does not expose true KV
> occupancy. Use it as a saturation signal. Switch to `vllm_mlx` for ground truth.

---

## 6. Distributed / production workloads

For multi-node tensor-parallel inference you have two MLX-native paths:

### exo (P2P distributed)
Set `llm_frameworks: [exo]` on the pool and define peers + a tensor-parallel group:

```yaml
    large_pool:
      vars:
        pool: large
        llm_frameworks: [mlx, exo]
      hosts:
        hydra-large-01: { ansible_host: 192.168.10.30, exo_peers: ["192.168.10.31"] }
        hydra-large-02: { ansible_host: 192.168.10.31, exo_peers: ["192.168.10.30"] }
```

exo also has no native metrics, so the same perf-proxy fronts its API and emits
`exo:*` (scraped on `:52417`). It folds into the identical `hydra:llm:*` rules.

### Apple AllReduce fabric probe
Define the group and enable the probe (the Apple counterpart to the NVIDIA
`nccl-probe`):

```yaml
# group_vars/all/main.yml or inventory
tensor_parallel_groups:
  tp_a: [hydra-large-01, hydra-large-02]
mlx_allreduce_enabled: true
```

```bash
ansible-playbook -i inventories/llm/hosts.yml llm-frameworks.yml \
  --limit large_pool --tags allreduce
```

This installs a periodic `mlx.launch`-driven `all_sum` benchmark on the group
leader and an always-on textfile exporter on `:11503`. Latency/busbw show up under
**`hydra:fabric:*`** with `MLXAllReduceLatencyHigh` / `MLXAllReduceProbeStale`
alerts (`monitoring/prometheus/rules/mlx-fabric.rules.yml`).

**Prerequisites for the distributed paths:** passwordless SSH between peers and a
fast interconnect (Thunderbolt/RoCE). Without RDMA the ring falls back to TCP and
AllReduce latency explodes — that is exactly what the probe and `RDMAHardwareAbsent`
alert are there to catch.

### Production readiness — honest status
* Single-node MLX (pure or vllm-mlx): **ready.** Full perf + hardware + crash telemetry.
* exo distributed: **engine deploys; observability now in place**, but exo itself is
  young — validate failover and model-shard placement before relying on it.
* AllReduce probe: **functional**, requires the SSH/fabric prerequisites above and
  benefits from per-fabric latency thresholds tuned to your hardware.

---

## 7. Salt equivalent

Every step above is also implemented as Salt states under `salt/` (states +
pillar mirror of these roles) so you can run the same node from either tool and
decide which to standardise on. See `salt/README.md` for the equivalent
`salt-ssh` / highstate commands.
