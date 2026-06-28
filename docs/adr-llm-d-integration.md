# ADR: llm-d Kubernetes Observability Bridge (LLM-9)

**Status:** Accepted  
**Date:** 2026-06-28  
**Ticket:** LLM-9  
**Author:** Priya Nair (Principal LLM Inference Architect)

---

## Context

llm-d is entirely absent from the hydra observability stack.  Its architecture
disaggregates inference into distinct Kubernetes pod roles — Endpoint Provisioner
(EPP), prefill workers, decode workers, and a KV-cache-aware routing gateway —
communicating over RDMA/NIXL.  None of these components are currently scraped,
enriched, or visible in dashboards or alerts.

The existing hydra monitoring stack is push-based: 28 Mac Mini nodes run an
OTEL Collector agent that forwards metrics via OTLP gRPC to a central gateway
(`otel-gateway.observability.svc.cluster.local:4317`).  llm-d runs in
Kubernetes and exposes Prometheus `/metrics` endpoints on each pod — a
pull-based model.  There is a structural mismatch between the two.

---

## Decision

Deploy a dedicated **in-cluster OpenTelemetry Collector** (`llm-d-collector`)
as a Kubernetes **Deployment** in the `observability` namespace.  It uses the
OTel `prometheus` receiver with `kubernetes_sd_configs` pod role to pull
metrics from every llm-d / vLLM pod annotated with
`prometheus.io/scrape: "true"`, then forwards to the existing hydra gateway
over OTLP gRPC.

### Why in-cluster collector, not remote scrape

| Option | Pros | Cons |
|--------|------|------|
| **In-cluster OTel collector** (chosen) | Pod IPs directly reachable; no firewall punching; k8sattributes processor enriches from the Kubernetes API without credentials crossing a network boundary; zero changes to existing gateway or Mac-fleet agents | Adds one more workload to manage |
| Remote scrape from gateway | No new workload | Pod IPs not routable from outside cluster; would require NodePort/LoadBalancer per pod or a Prometheus federation layer; adds cluster egress for every scrape interval |
| Prometheus Operator + remote_write | Native k8s pattern; battle-tested | Requires Prometheus Operator CRDs; another stateful workload; scrape-then-remote_write latency is higher than OTLP streaming |

The in-cluster collector is the lightest path that reuses the existing OTLP
pipeline and avoids changes to the gateway config.

### Why Deployment, not DaemonSet

A DaemonSet runs one replica per node and is the right choice when metrics are
node-local (e.g. hardware exporters, cgroup stats).  llm-d pods can be
scheduled on any subset of GPU nodes and are reachable by pod IP from anywhere
in the cluster.  A single-replica Deployment with cluster-wide
`kubernetes_sd_configs` can scrape all pods without per-node overhead.  If pod
count grows large enough to warrant sharding, the replica count can be increased
with hash-mod target sharding — no architectural change required.

---

## Label mapping

### Pod annotation contract

llm-d / vLLM pods must carry:

```yaml
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "8080"          # vLLM default metrics port
    prometheus.io/path: "/metrics"      # optional, defaults to /metrics
  labels:
    app.kubernetes.io/component: epp    # epp | prefill | decode | gateway | vllm
    model: "meta-llama--Llama-3-8B-Instruct"
    pool: "llama3-8b-pool"
    llm_d_role: prefill                 # optional P/D disaggregation tag
```

### Label stamps applied by the collector

The collector stamps the following on every scraped time-series so that the
existing `hydra:llm:*` normalization recording rules apply without modification:

| Attribute | Value / Source | Why |
|-----------|---------------|-----|
| `cluster` | `hydra` (static) | Fleet-wide grouping, matches Mac-fleet convention |
| `environment` | `production` (static) | Environment routing in Alertmanager |
| `gpu_provider` | `nvidia` (static) | llm-d targets x86 NVIDIA GPU nodes |
| `engine` | `app.kubernetes.io/component` pod label | Selects the correct normalization rule branch in `hydra:llm:*` |
| `model` | `model` pod label | Model-level aggregation in dashboards and alerts |
| `pool` | `pool` pod label | EPP inference-pool grouping for KV-cache-aware routing SLOs |
| `llm_d_role` | `llm_d_role` pod label | P/D disaggregation visibility |
| `namespace` | `__meta_kubernetes_namespace` | Multi-tenant filtering |
| `pod` | `__meta_kubernetes_pod_name` | Pod-level drill-down |
| `node` | `__meta_kubernetes_pod_node_name` | Correlate with RDMA/fabric metrics |

The `engine` label aligns with values already handled by the LLM-3
normalization layer (`vllm`, `sglang`, `mlx`).  llm-d EPP / prefill / decode
pods exposing `vllm:*` metrics will therefore route through the same
`hydra:llm:*` recording rules as Mac-fleet vLLM-MLX nodes.

---

## How it plugs into the gateway

```
llm-d pods  ──[prometheus scrape]──▶  llm-d-collector (Deployment)
                                              │
                              OTLP gRPC  4317 │
                                              ▼
                              otel-gateway.observability.svc.cluster.local
                                              │
                              prometheusremotewrite
                                              │
                                              ▼
                              VictoriaMetrics / Mimir
                                              │
                                              ▼
                      hydra:llm:* recording rules  →  dashboards / alerts
```

The gateway already listens on `0.0.0.0:4317` (gRPC) as defined in
`monitoring/otel-collector/otel-gateway-config.yaml`.  The Kubernetes Service
`otel-gateway.observability.svc.cluster.local:4317` (ClusterIP, defined in
`otel-gateway.yaml`) exposes this port cluster-internally — no changes to the
gateway are needed.

The endpoint is parameterised via the `llm-d-collector-env` ConfigMap
(`GATEWAY_OTLP_ENDPOINT`), making it trivial to point to an external gateway or
a staging environment without editing the collector config.

---

## Consequences

**Positive**
- Zero changes to the gateway, Mac-fleet agents, or existing dashboards.
- `hydra:llm:*` normalization recording rules automatically cover vLLM metrics
  from llm-d pods once they carry the required labels.
- EPP routing decisions, KV-cache-aware placement, and P/D disaggregation
  metrics become visible in the same VictoriaMetrics instance as the rest of
  the fleet.
- NIXL KV-transfer RDMA metrics (once exposed via `/metrics`) will flow through
  the same pipeline and correlate with SRE-3 RDMA fabric alerts.

**Negative / risks**
- Pods must be labelled with the annotation contract above; un-annotated pods
  are silently skipped.  An absence alert (`absent(hydra:llm:up{...})`) should
  be added (LLM-1 workstream) to surface this.
- The collector holds Kubernetes API watch connections; a stale lease can cause
  a brief gap.  Mitigate with `retry_on_failure` on the OTLP exporter (already
  configured) and a `LLMCollectorDown` alert on `up{job="llm-d-collector"}`.
- `gpu_provider=nvidia` is hardcoded.  If llm-d pods are ever scheduled on
  Apple Silicon nodes, override via a pod-label-sourced relabel rule.

---

## Files created

| File | Purpose |
|------|---------|
| `apple-silicon-monitoring/deploy/kubernetes/llm-d-collector.yaml` | ServiceAccount, ClusterRole, ClusterRoleBinding, env ConfigMap, collector ConfigMap, Deployment |
| `docs/adr-llm-d-integration.md` | This document |
