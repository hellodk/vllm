# ML Inference Cluster — Architecture Decision Document

## For: Local AI-Assisted Development Platform
## Scale: 2,000 Developers | 20 Mac Minis (M1/M3/M4, 8–16 GB RAM)
## Date: March 2026

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Capacity Analysis — The Hard Math](#2-capacity-analysis)
3. [Architecture Overview](#3-architecture-overview)
4. [Option A — Single-Node Serving (Ollama + LiteLLM)](#4-option-a)
5. [Option B — Distributed Large Model (exo)](#5-option-b)
6. [Option C — Tabby Self-Hosted Platform](#6-option-c)
7. [Option D — Hybrid: Local + Cloud](#7-option-d)
8. [IDE Tooling Landscape](#8-ide-tooling-landscape)
9. [Observability & Guardrails Stack](#9-observability-stack)
10. [Recommendation](#10-recommendation)
11. [Reference Links](#11-reference-links)

---

## 1. Executive Summary

We want to provide 2,000 developers with an AI-assisted coding experience (similar to Cursor/GitHub Copilot) using 20 co-located Mac Minis. This document evaluates four architecture options, maps the tooling landscape, and provides a clear recommendation.

**Critical finding:** 20 Mac Minis cannot serve 2,000 developers for real-time autocomplete at Copilot-level responsiveness. The math is in Section 2. Every architecture option must address this gap — either through infrastructure scaling, intelligent caching, usage tiering, or a hybrid cloud approach.

---

## 2. Capacity Analysis — The Hard Math

### 2.1 Hardware Inventory

```
┌──────────────────────────────────────────────────────────────┐
│                    HARDWARE INVENTORY                         │
├──────────┬───────┬───────┬────────┬──────────┬──────────────┤
│ Count    │ Chip  │ RAM   │ CPU    │ GPU      │ Max Model    │
├──────────┼───────┼───────┼────────┼──────────┼──────────────┤
│ 1        │ M1    │ 8 GB  │ 8 core │ 8 core   │ ~3B (Q4)    │
│ 1        │ M3    │ 16 GB │ 10 core│ 10 core  │ ~7B (Q4)    │
│ 18       │ M4    │ 16 GB │ 10 core│ 10 core  │ ~7B (Q4)    │
├──────────┼───────┼───────┼────────┼──────────┼──────────────┤
│ 20 total │       │ 312GB │ ~198   │ ~198     │              │
│          │       │ total │ cores  │ cores    │              │
└──────────┴───────┴───────┴────────┴──────────┴──────────────┘
```

### 2.2 Developer Activity Model

```
Total developers:                        2,000
Peak concurrent (30% of total):            600
Active typing (50% of concurrent):         300
Autocomplete requests per active dev:      1 req / 3 seconds
Chat requests per active dev:              1 req / 3 minutes

Peak autocomplete demand:                  ~100 req/s
Peak chat demand:                          ~3-5 req/s
```

### 2.3 Per-Node Throughput (Single Ollama Instance)

```
┌────────────────────────────────────────────────────────────────────┐
│                  THROUGHPUT PER MAC MINI                            │
├──────────────┬──────────────┬──────────────┬──────────────────────┤
│ Model        │ Tokens/sec   │ Autocomplete │ Chat (256 tok)       │
│              │ (generation) │ (64 tok)     │                      │
├──────────────┼──────────────┼──────────────┼──────────────────────┤
│ 1.5B (Q4)   │ ~80-100 t/s  │ ~1.2 req/s   │ ~0.35 req/s          │
│ 3B (Q4)     │ ~50-70 t/s   │ ~0.9 req/s   │ ~0.25 req/s          │
│ 7B (Q4)     │ ~30-50 t/s   │ ~0.6 req/s   │ ~0.15 req/s          │
│ 70B (Q4)*   │ ~3-8 t/s     │ N/A          │ ~0.02 req/s          │
└──────────────┴──────────────┴──────────────┴──────────────────────┘
* 70B requires distributed inference across multiple nodes
```

### 2.4 Cluster Capacity vs Demand

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     CAPACITY vs DEMAND GAP                               │
├────────────────────┬──────────────────┬──────────────┬──────────────────┤
│ Scenario           │ Cluster Capacity │ Peak Demand  │ Gap              │
│                    │ (20 nodes)       │ (2000 devs)  │                  │
├────────────────────┼──────────────────┼──────────────┼──────────────────┤
│ Autocomplete (7B)  │ ~12 req/s        │ ~100 req/s   │ 8x SHORT ❌     │
│ Autocomplete (3B)  │ ~18 req/s        │ ~100 req/s   │ 5x SHORT ❌     │
│ Autocomplete (1.5B)│ ~24 req/s        │ ~100 req/s   │ 4x SHORT ❌     │
│ Chat (7B)          │ ~3 req/s         │ ~3-5 req/s   │ TIGHT ⚠️        │
│ Chat (70B dist.)   │ ~0.1 req/s       │ ~3-5 req/s   │ 30x SHORT ❌    │
├────────────────────┴──────────────────┴──────────────┴──────────────────┤
│                                                                          │
│ CONCLUSION: 20 Mac Minis can serve ~200 devs at full Copilot-level      │
│ experience, NOT 2,000. To serve 2,000 devs we need a hybrid strategy.   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.5 What's Needed for Full 2,000-Dev Coverage

```
For autocomplete only (1.5B model):     ~80-100 Mac Minis
For autocomplete + chat (7B model):     ~150-200 Mac Minis
For Cursor-equivalent experience:       Cloud API (or 200+ Mac Minis)
```

---

## 3. Architecture Overview — All Four Options at a Glance

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ Option A │  │ Option B │  │ Option C │  │ Option D             │   │
│  │ Single   │  │ Distrib. │  │ Tabby    │  │ Hybrid               │   │
│  │ Node     │  │ (exo)    │  │ Platform │  │ Local + Cloud        │   │
│  │          │  │          │  │          │  │                      │   │
│  │ Ollama   │  │ 70B      │  │ Purpose  │  │ Local: autocomplete  │   │
│  │ +LiteLLM │  │ across   │  │ built    │  │ Cloud: chat/agent    │   │
│  │          │  │ cluster  │  │ for this │  │                      │   │
│  │ ~200 devs│  │ ~50 devs │  │ ~200 devs│  │ ~2000 devs ✅       │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────────┘   │
│                                                                         │
│  Complexity:    Low           Medium        Low          Medium         │
│  Model quality: 7B            70B           3-7B         7B + GPT4     │
│  Latency:       Fast          Slow          Fast         Mixed         │
│  Cost:          Hardware only Hardware only Hardware     Hardware+Cloud │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Option A — Single-Node Serving (Ollama + LiteLLM)

**Each Mac Mini runs one model independently. LiteLLM routes across them.**

### 4.1 Architecture Diagram

```
                        ┌──────────────────────────────┐
                        │        2,000 DEVELOPERS       │
                        │   VS Code + Continue.dev /    │
                        │   Cursor / Cline / Tabby      │
                        └──────────────┬───────────────┘
                                       │
                                       │ HTTPS :443
                                       ▼
                    ┌──────────────────────────────────────┐
                    │         GATEWAY (Mac Mini 1 — M1)    │
                    │                                      │
                    │  ┌────────────┐  ┌────────────────┐  │
                    │  │   Nginx    │  │   LiteLLM      │  │
                    │  │ (reverse   │→ │   Proxy        │  │
                    │  │  proxy,    │  │                │  │
                    │  │  TLS)      │  │  • Routing     │  │
                    │  └────────────┘  │  • Rate limit  │  │
                    │                  │  • Guardrails  │  │
                    │  ┌────────────┐  │  • Caching     │  │
                    │  │ Prometheus │  │  • Token count │  │
                    │  │ Grafana    │  │  • Fallbacks   │  │
                    │  │ Loki       │  └───────┬────────┘  │
                    │  └────────────┘          │           │
                    └──────────────────────────┼───────────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          │                    │                    │
                          ▼                    ▼                    ▼
               ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
               │ Mac Mini 2 (M3) │  │ Mac Mini 3 (M4) │  │ Mac Mini N (M4) │
               │                 │  │                 │  │                 │
               │ ┌─────────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │
               │ │   Ollama    │ │  │ │   Ollama    │ │  │ │   Ollama    │ │
               │ │ qwen2.5-    │ │  │ │ qwen2.5-    │ │  │ │ qwen2.5-    │ │
               │ │ coder:7b    │ │  │ │ coder:7b    │ │  │ │ coder:7b    │ │
               │ └─────────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │
               │ ┌─────────────┐ │  │ ┌─────────────┐ │  │ ┌─────────────┐ │
               │ │  Promtail   │ │  │ │  Promtail   │ │  │ │  Promtail   │ │
               │ │ (→ Loki)    │ │  │ │ (→ Loki)    │ │  │ │ (→ Loki)    │ │
               │ └─────────────┘ │  │ └─────────────┘ │  │ └─────────────┘ │
               └─────────────────┘  └─────────────────┘  └─────────────────┘
                    19 inference nodes (Ollama per node)
```

### 4.2 Component Map

```
┌────────────────────┬───────────────────────────────────────────────────┐
│ Layer              │ Component → Purpose                               │
├────────────────────┼───────────────────────────────────────────────────┤
│ IDE (Dev machine)  │ Continue.dev → Chat, inline edit, @codebase      │
│                    │ Tabby Extension → Tab autocomplete               │
│                    │ Cline → Multi-file agent (Composer-like)         │
│                    │ Cursor → Native IDE (optional)                   │
├────────────────────┼───────────────────────────────────────────────────┤
│ API Gateway        │ LiteLLM → Model routing, rate limiting, caching  │
│ (Mac Mini 1)       │ Nginx → TLS termination, reverse proxy           │
│                    │ Guardrails.py → Hallucination/secret/PII filter  │
├────────────────────┼───────────────────────────────────────────────────┤
│ Observability      │ Prometheus → Metrics (latency, tokens, errors)   │
│ (Mac Mini 1)       │ Grafana → Dashboards and alerts                  │
│                    │ Loki + Promtail → Centralized log aggregation    │
├────────────────────┼───────────────────────────────────────────────────┤
│ Inference          │ Ollama → Model serving (one instance per node)   │
│ (Mac Mini 2-20)    │ Models: qwen2.5-coder:7b, phi3:mini, etc.       │
├────────────────────┼───────────────────────────────────────────────────┤
│ Deployment         │ Ansible → Idempotent provisioning & operations   │
└────────────────────┴───────────────────────────────────────────────────┘
```

### 4.3 Pros / Cons

```
✅ Simplest to set up and operate
✅ Each node is independent — no single point of failure for inference
✅ Easy to add more nodes (just add to Ansible inventory)
✅ Fast autocomplete (~1-2s) for 7B model
✅ LiteLLM caching reduces duplicate requests

❌ Limited to 7B models (16GB RAM constraint per node)
❌ 7B model quality insufficient for complex reasoning
❌ Cannot serve 2,000 devs at peak (serves ~200 at Copilot-level latency)
❌ No model larger than 7B available for chat
```

### 4.4 Realistic Capacity: ~150-250 concurrent developers

---

## 5. Option B — Distributed Large Model (exo)

**Pool multiple Mac Minis to run a single large model (e.g., 70B) by sharding across nodes.**

### 5.1 Architecture Diagram

```
                        ┌──────────────────────────────┐
                        │        2,000 DEVELOPERS       │
                        └──────────────┬───────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │     GATEWAY (Mac Mini 1 — M1)        │
                    │  LiteLLM → routes between pools      │
                    │  Monitoring stack                     │
                    └────────┬──────────────┬───────────────┘
                             │              │
                    ┌────────▼──────┐  ┌────▼──────────────────────┐
                    │  POOL A       │  │  POOL B                   │
                    │  "Fast"       │  │  "Smart"                  │
                    │  (autocomplete)│  │  (chat/agent)             │
                    │               │  │                           │
                    │  12 Mac Minis │  │  7 Mac Minis              │
                    │  Each: Ollama │  │  Running exo              │
                    │  + 3B model   │  │  peer-to-peer cluster     │
                    │               │  │                           │
                    │  ~12 req/s    │  │  ┌─────────────────────┐  │
                    │  autocomplete │  │  │  Llama 3.1 70B      │  │
                    │               │  │  │  Sharded across     │  │
                    │               │  │  │  7 nodes (~10GB     │  │
                    │               │  │  │  per node)          │  │
                    │               │  │  │                     │  │
                    │               │  │  │  ~5-8 tokens/sec    │  │
                    │               │  │  │  ~0.03 req/s        │  │
                    └───────────────┘  │  └─────────────────────┘  │
                                       └───────────────────────────┘
```

### 5.2 How exo Works

```
┌─────────────────────────────────────────────────────────────────────┐
│                        exo — PEER-TO-PEER LLM                       │
│                                                                     │
│  Each Mac Mini holds a SHARD of the model layers:                   │
│                                                                     │
│  Mac Mini 4:  Layers  0-11  (embed + early layers)                  │
│  Mac Mini 5:  Layers 12-23  ──────┐                                 │
│  Mac Mini 6:  Layers 24-35        │ Data flows node-to-node         │
│  Mac Mini 7:  Layers 36-47        │ over your LAN                   │
│  Mac Mini 8:  Layers 48-59  ──────┘                                 │
│  Mac Mini 9:  Layers 60-71                                          │
│  Mac Mini 10: Layers 72-79  (final layers + output)                 │
│                                                                     │
│  Request flow:                                                      │
│  Input → Mini4 → Mini5 → Mini6 → ... → Mini10 → Output            │
│                                                                     │
│  ⚠️ Latency = sum of all node compute + network transfer            │
│  ⚠️ Throughput bottlenecked by slowest node                         │
│  ⚠️ If ANY node fails, entire model is unavailable                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.3 Pros / Cons

```
✅ Access to 70B+ model quality (much better reasoning, fewer hallucinations)
✅ Utilizes aggregate RAM across nodes (7 × 16GB = 112GB usable)
✅ exo auto-discovers peers on LAN, easy setup
✅ Can run models that no single Mac Mini could run

❌ EXTREMELY SLOW: ~5-8 tokens/sec for 70B (a 256-token response takes 30-50 seconds)
❌ LAN bandwidth is the bottleneck (activations transfer between nodes)
❌ Single point of failure — one node down = entire model down
❌ Can only handle ~1 concurrent request (sequential pipeline)
❌ At 0.03 req/s, serves maybe 50 developers with tolerable latency for CHAT only
❌ Completely impractical for autocomplete (needs <500ms latency)
❌ Experimental software — not production-grade
```

### 5.4 Realistic Capacity: ~30-50 developers for chat only

### 5.5 Verdict on exo

**exo is impressive technology but wrong for this scale.** A 70B model distributed across 7 Mac Minis over Gigabit Ethernet produces ~5-8 tok/s. That's 30-50 seconds for a typical chat response. For 2,000 developers this is not viable as a primary serving path — but could work as a "premium" tier for complex questions if devs are willing to wait.

---

## 6. Option C — Tabby Self-Hosted Platform

**Tabby is a purpose-built self-hosted AI coding assistant (not a general LLM proxy). It comes with its own model runtime, code indexing, and IDE extensions.**

### 6.1 Architecture Diagram

```
                        ┌──────────────────────────────┐
                        │        2,000 DEVELOPERS       │
                        │   VS Code (Tabby extension)   │
                        │   JetBrains (Tabby plugin)    │
                        └──────────────┬───────────────┘
                                       │
                                       │ HTTP :8080
                                       ▼
                    ┌──────────────────────────────────────┐
                    │        LOAD BALANCER (Nginx)          │
                    │  Round-robin across Tabby instances    │
                    └────────┬─────────┬─────────┬─────────┘
                             │         │         │
                    ┌────────▼───┐ ┌───▼────┐ ┌──▼─────────┐
                    │ Tabby      │ │ Tabby  │ │ Tabby      │
                    │ Instance 1 │ │ Inst.2 │ │ Instance N │
                    │            │ │        │ │            │
                    │ ┌────────┐ │ │  ...   │ │ ┌────────┐ │
                    │ │StarCoder│ │ │        │ │ │StarCoder│ │
                    │ │ 2-3B   │ │ │        │ │ │ 2-3B   │ │
                    │ │(Metal) │ │ │        │ │ │(Metal) │ │
                    │ └────────┘ │ │        │ │ └────────┘ │
                    │ ┌────────┐ │ │        │ │ ┌────────┐ │
                    │ │  Code  │ │ │        │ │ │  Code  │ │
                    │ │ Index  │ │ │        │ │ │ Index  │ │
                    │ └────────┘ │ │        │ │ └────────┘ │
                    └────────────┘ └────────┘ └────────────┘
                       Each Mac Mini = 1 Tabby instance
```

### 6.2 What Tabby Provides Out of the Box

```
┌─────────────────────────────────────────────────────────────────────┐
│                     TABBY FEATURE MAP                                │
├──────────────────────┬──────────────────────────────────────────────┤
│ Code Completion      │ FIM (Fill-in-Middle) using StarCoder2        │
│                      │ Specifically trained for code completion      │
│                      │ Better than generic LLMs for autocomplete    │
├──────────────────────┼──────────────────────────────────────────────┤
│ Chat                 │ Built-in chat with code context              │
│                      │ Uses Qwen2.5-Coder or CodeLlama             │
├──────────────────────┼──────────────────────────────────────────────┤
│ Code Indexing        │ Native repo indexing (no separate embedding) │
│                      │ Understands your codebase structure          │
├──────────────────────┼──────────────────────────────────────────────┤
│ IDE Support          │ VS Code extension                            │
│                      │ JetBrains plugin (IntelliJ, PyCharm, etc.)  │
│                      │ Vim/Neovim plugin                            │
├──────────────────────┼──────────────────────────────────────────────┤
│ Admin UI             │ Web-based admin dashboard                    │
│                      │ Usage analytics, user management             │
├──────────────────────┼──────────────────────────────────────────────┤
│ Apple Silicon        │ Metal acceleration (native performance)      │
│                      │ Optimized for M-series chips                 │
├──────────────────────┼──────────────────────────────────────────────┤
│ Enterprise           │ SSO/LDAP, team management, audit logs        │
│ (Tabby Enterprise)   │ License required for >5 users               │
└──────────────────────┴──────────────────────────────────────────────┘
```

### 6.3 Pros / Cons

```
✅ Purpose-built for this exact use case
✅ StarCoder2 is better at code completion than general LLMs
✅ Built-in repo indexing — no separate embedding pipeline
✅ JetBrains support (critical if not all devs use VS Code)
✅ Admin dashboard with usage analytics
✅ Metal acceleration on Apple Silicon
✅ Active open-source community

❌ Same 7B model quality ceiling per node
❌ Same capacity problem (~200 devs, not 2,000)
❌ Enterprise license needed for team features at this scale
❌ Less flexible than Ollama + LiteLLM for custom routing
❌ No agent/Composer-like capabilities
```

### 6.4 Realistic Capacity: ~150-250 concurrent developers

---

## 7. Option D — Hybrid: Local Cluster + Cloud API (RECOMMENDED for 2,000 devs)

**Use the Mac Mini cluster for high-frequency, low-latency tasks (autocomplete). Route complex tasks (chat, agent, code review) to a cloud API.**

### 7.1 Architecture Diagram

```
                        ┌──────────────────────────────────────────┐
                        │             2,000 DEVELOPERS              │
                        │                                          │
                        │   VS Code / Cursor / JetBrains           │
                        │   ┌─────────────┐  ┌──────────────────┐  │
                        │   │ Continue.dev │  │ Tabby Extension  │  │
                        │   │ or Cline     │  │ (autocomplete)   │  │
                        │   └──────┬──────┘  └────────┬─────────┘  │
                        └──────────┼──────────────────┼────────────┘
                                   │                  │
                         Chat/Agent│                  │ Autocomplete
                                   │                  │
                    ┌──────────────▼──────────────────▼─────────────┐
                    │              API GATEWAY LAYER                  │
                    │         (Mac Mini 1 — M1, 8 GB)                │
                    │                                                │
                    │  ┌──────────────────────────────────────────┐  │
                    │  │            LiteLLM Proxy                 │  │
                    │  │                                          │  │
                    │  │  ┌─────────────────────────────────────┐ │  │
                    │  │  │       INTELLIGENT ROUTER             │ │  │
                    │  │  │                                     │ │  │
                    │  │  │  IF autocomplete request:           │ │  │
                    │  │  │    → Route to LOCAL Mac Mini pool   │ │  │
                    │  │  │                                     │ │  │
                    │  │  │  IF chat/agent request:             │ │  │
                    │  │  │    → Route to CLOUD API             │ │  │
                    │  │  │    (Claude / GPT-4 / DeepSeek)      │ │  │
                    │  │  │                                     │ │  │
                    │  │  │  IF cloud rate-limited or down:     │ │  │
                    │  │  │    → Fallback to LOCAL 7B model     │ │  │
                    │  │  └─────────────────────────────────────┘ │  │
                    │  │                                          │  │
                    │  │  • Rate limiting (per-user, per-team)   │  │
                    │  │  • Token budget enforcement              │  │
                    │  │  • Guardrails (secret/PII filtering)    │  │
                    │  │  • Response caching (5 min TTL)         │  │
                    │  │  • Spend tracking & alerts              │  │
                    │  └──────────────────────────────────────────┘  │
                    │                                                │
                    │  ┌────────────┐  ┌─────────────────────────┐  │
                    │  │ Prometheus │  │ Nginx (TLS, /v1 proxy)  │  │
                    │  │ Grafana    │  └─────────────────────────┘  │
                    │  │ Loki       │                                │
                    │  └────────────┘                                │
                    └───────┬────────────────────────┬──────────────┘
                            │                        │
              ┌─────────────▼──────────┐   ┌────────▼──────────────┐
              │   LOCAL MAC MINI POOL   │   │     CLOUD API POOL    │
              │   (19 Mac Minis)        │   │                       │
              │                         │   │  ┌─────────────────┐  │
              │  ┌───┐┌───┐┌───┐┌───┐  │   │  │  Claude API     │  │
              │  │ M3││ M4││ M4││ M4│  │   │  │  (Anthropic)    │  │
              │  └───┘└───┘└───┘└───┘  │   │  └─────────────────┘  │
              │  ┌───┐┌───┐┌───┐┌───┐  │   │  ┌─────────────────┐  │
              │  │ M4││ M4││ M4││ M4│  │   │  │  GPT-4 API      │  │
              │  └───┘└───┘└───┘└───┘  │   │  │  (OpenAI)       │  │
              │  ┌───┐┌───┐┌───┐┌───┐  │   │  └─────────────────┘  │
              │  │ M4││ M4││ M4││ M4│  │   │  ┌─────────────────┐  │
              │  └───┘└───┘└───┘└───┘  │   │  │  DeepSeek API   │  │
              │  ┌───┐┌───┐┌───┐┌───┐  │   │  │  (Budget tier)  │  │
              │  └───┘└───┘└───┘└───┘  │   │  └─────────────────┘  │
              │  ┌───┐┌───┐┌───┐       │   │                       │
              │  │ M4││ M4││ M4│       │   │  Fallback chain:      │
              │  └───┘└───┘└───┘       │   │  Claude → GPT-4 →     │
              │                         │   │  DeepSeek → Local 7B  │
              │  Each running:          │   │                       │
              │  • Ollama               │   │  Budget controls:     │
              │  • StarCoder2-3B        │   │  • Per-user limits    │
              │    (autocomplete)       │   │  • Per-team budgets   │
              │  • qwen2.5-coder:7b    │   │  • Monthly caps       │
              │    (chat fallback)      │   │                       │
              └─────────────────────────┘   └───────────────────────┘
```

### 7.2 Request Routing Logic

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     REQUEST ROUTING DECISION TREE                        │
│                                                                         │
│                        Incoming Request                                  │
│                             │                                           │
│                    ┌────────▼────────┐                                  │
│                    │ Request type?    │                                  │
│                    └────────┬────────┘                                  │
│                             │                                           │
│              ┌──────────────┼──────────────┐                            │
│              │              │              │                            │
│      ┌───────▼──────┐  ┌───▼───────┐  ┌──▼──────────┐                 │
│      │ Autocomplete  │  │   Chat    │  │   Agent     │                 │
│      │ (tab complete)│  │ (Cmd+L)   │  │ (Cline/     │                 │
│      │              │  │           │  │ Composer)    │                 │
│      └──────┬───────┘  └─────┬─────┘  └──────┬──────┘                 │
│             │                │               │                         │
│      ┌──────▼───────┐  ┌────▼──────┐  ┌─────▼───────┐                 │
│      │ LOCAL CLUSTER │  │ CLOUD API │  │  CLOUD API  │                 │
│      │              │  │           │  │  (needs      │                 │
│      │ StarCoder2-3B│  │ Claude    │  │   strong     │                 │
│      │ or phi3:mini │  │ Sonnet    │  │   reasoning) │                 │
│      │              │  │           │  │              │                 │
│      │ Latency:     │  │ Latency:  │  │  Latency:   │                 │
│      │ 200-500ms    │  │ 1-5s      │  │  5-30s      │                 │
│      │              │  │           │  │              │                 │
│      │ Cost: $0     │  │ Cost:     │  │  Cost:      │                 │
│      │ (hardware    │  │ ~$0.003   │  │  ~$0.01     │                 │
│      │  already     │  │ per chat  │  │  per agent  │                 │
│      │  owned)      │  │           │  │  task       │                 │
│      └──────────────┘  └───────────┘  └─────────────┘                 │
│                                                                         │
│  FALLBACK CHAIN:                                                        │
│  Cloud down? → Local 7B (degraded but functional)                       │
│  Local down? → Cloud only (higher cost but full coverage)               │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Cost Estimate for 2,000 Developers (Hybrid)

```
┌──────────────────────────────────────────────────────────────────────┐
│                    MONTHLY COST ESTIMATE                              │
├──────────────────────┬────────────────────┬──────────────────────────┤
│ Component            │ Volume             │ Cost / Month             │
├──────────────────────┼────────────────────┼──────────────────────────┤
│ Autocomplete (local) │ ~2M req/month      │ $0 (hardware owned)     │
│                      │ (100 req/s × 8hr   │                         │
│                      │  × 22 workdays)    │                         │
├──────────────────────┼────────────────────┼──────────────────────────┤
│ Chat — Claude Sonnet │ ~200K req/month    │ ~$2,000 - $5,000        │
│ (avg 500 in + 300    │ (5 req/hr × 2000   │ (depends on prompt      │
│  out tokens)         │  devs × 20 days)   │  size and caching)      │
├──────────────────────┼────────────────────┼──────────────────────────┤
│ Agent — Claude Opus  │ ~20K req/month     │ ~$1,500 - $3,000        │
│ (complex tasks,      │ (used sparingly)   │                         │
│  avg 2000 in + 1000  │                    │                         │
│  out tokens)         │                    │                         │
├──────────────────────┼────────────────────┼──────────────────────────┤
│ Electricity (20 Macs)│ ~20 × 40W × 24/7  │ ~$150                   │
├──────────────────────┼────────────────────┼──────────────────────────┤
│ TOTAL                │                    │ ~$3,650 - $8,150/mo     │
│                      │                    │ ~$1.80 - $4.00 per      │
│                      │                    │ developer per month     │
├──────────────────────┴────────────────────┴──────────────────────────┤
│                                                                      │
│ Compare: GitHub Copilot Business = $19/dev/month × 2000 = $38,000   │
│ Compare: Cursor Business = $40/dev/month × 2000 = $80,000           │
│                                                                      │
│ HYBRID APPROACH: 5-20x CHEAPER than pure SaaS                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 7.4 Pros / Cons

```
✅ SCALES TO 2,000 DEVS — only viable option at this scale
✅ Best of both worlds: fast local autocomplete + smart cloud chat
✅ 5-20x cheaper than Copilot/Cursor licensing
✅ Graceful degradation: if cloud is down, local 7B still works
✅ Cloud gives access to GPT-4/Claude quality for complex reasoning
✅ LiteLLM handles all routing — devs see a single API endpoint
✅ Budget controls prevent runaway cloud spend
✅ Data stays local for autocomplete (most sensitive context)

❌ Cloud dependency for chat/agent quality
❌ Monthly cloud API costs ($3K-8K range)
❌ Need to manage API keys and cloud provider relationships
❌ Sensitive code sent to cloud for chat (can be mitigated with guardrails)
❌ More complex architecture to operate
```

### 7.5 Realistic Capacity: **2,000 developers (full coverage)**

---

## 8. IDE Tooling Landscape

### 8.1 Complete Tool Comparison

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│                              IDE TOOLING COMPARISON                                    │
├──────────────┬─────────────┬──────────────┬──────────────┬────────────┬───────────────┤
│ Feature      │ Continue.dev│ Cursor       │ Cline        │ Tabby      │ GitHub        │
│              │             │              │              │            │ Copilot       │
├──────────────┼─────────────┼──────────────┼──────────────┼────────────┼───────────────┤
│ Tab complete │ ✅ Good      │ ✅ Excellent  │ ❌ No        │ ✅ Excellent│ ✅ Excellent   │
│ Chat         │ ✅ Good      │ ✅ Excellent  │ ✅ Good      │ ✅ Good     │ ✅ Good        │
│ Inline edit  │ ✅ Cmd+I     │ ✅ Cmd+K      │ ❌ No        │ ❌ No      │ ❌ No          │
│ Agent/Compose│ ⚠️ Basic     │ ✅ Excellent  │ ✅ Excellent  │ ❌ No      │ ✅ Good (WS)   │
│ Multi-file   │ ⚠️ Basic     │ ✅ Yes        │ ✅ Yes       │ ❌ No      │ ⚠️ Basic       │
│ @codebase    │ ✅ Yes       │ ✅ Yes        │ ✅ Yes       │ ✅ Yes     │ ✅ Yes          │
│ @file/@folder│ ✅ Yes       │ ✅ Yes        │ ✅ Yes       │ ✅ Yes     │ ✅ Yes          │
│ Terminal AI  │ ✅ Yes       │ ✅ Yes        │ ✅ Yes       │ ❌ No      │ ✅ Yes          │
│ Custom API   │ ✅ Yes       │ ✅ Yes        │ ✅ Yes       │ ✅ Yes     │ ❌ No           │
│ JetBrains    │ ✅ Yes       │ ❌ No (own IDE)│ ❌ No       │ ✅ Yes     │ ✅ Yes          │
│ VS Code      │ ✅ Extension │ ✅ Fork of    │ ✅ Extension │ ✅ Extension│ ✅ Extension    │
│ Self-hosted  │ ✅ Yes       │ ⚠️ Partial    │ ✅ Yes       │ ✅ Yes     │ ❌ No           │
│ Open source  │ ✅ Apache 2  │ ❌ Proprietary│ ✅ Apache 2  │ ✅ SSPL    │ ❌ Proprietary  │
│ License cost │ Free        │ $20-40/seat  │ Free         │ Enterprise │ $19-39/seat   │
│              │             │              │              │ pricing    │               │
├──────────────┴─────────────┴──────────────┴──────────────┴────────────┴───────────────┤
│                                                                                       │
│ RECOMMENDED COMBO:  Continue.dev (chat + inline) + Tabby (autocomplete)               │
│ POWER USERS:        Add Cline for agent/multi-file tasks                              │
│ IF USING CURSOR:    Point it at LiteLLM endpoint directly                             │
│                                                                                       │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Where Each Tool Sits in the Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                        DEVELOPER'S MACHINE                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    VS Code / Cursor                           │   │
│  │                                                              │   │
│  │  ┌──────────────┐  ┌────────────┐  ┌────────────────────┐   │   │
│  │  │  Tabby Ext.  │  │ Continue   │  │ Cline Extension    │   │   │
│  │  │              │  │ .dev Ext.  │  │                    │   │   │
│  │  │ • Ghost text │  │            │  │ • Creates files    │   │   │
│  │  │   complete   │  │ • Chat     │  │ • Edits multi-file │   │   │
│  │  │ • FIM model  │  │ • Cmd+I    │  │ • Runs commands    │   │   │
│  │  │              │  │ • @context │  │ • Autonomous agent │   │   │
│  │  │              │  │ • /commands│  │                    │   │   │
│  │  └──────┬───────┘  └─────┬──────┘  └─────────┬──────────┘   │   │
│  │         │                │                    │              │   │
│  └─────────┼────────────────┼────────────────────┼──────────────┘   │
│            │                │                    │                  │
│            │ :8080          │ :4000/v1           │ :4000/v1        │
│            │ (Tabby API)    │ (OpenAI compat)    │ (OpenAI compat) │
│            │                │                    │                  │
└────────────┼────────────────┼────────────────────┼──────────────────┘
             │                │                    │
             ▼                ▼                    ▼
    ┌────────────────┐  ┌──────────────────────────────────┐
    │  Tabby Server  │  │       LiteLLM Proxy              │
    │  (Mac Minis)   │  │       (Gateway Mac Mini)         │
    │                │  │                                  │
    │  Dedicated     │  │   Routes to:                     │
    │  completion    │  │   • Local Ollama (autocomplete)  │
    │  model         │  │   • Cloud API (chat/agent)       │
    │  (StarCoder2)  │  │   • Local 7B (fallback)         │
    └────────────────┘  └──────────────────────────────────┘
```

### 8.3 Recommended IDE Setup Per Developer Persona

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  PERSONA 1: "Standard Developer" (80% of team)                     │
│  ───────────────────────────────────────────────                    │
│  IDE:     VS Code                                                   │
│  Install: Continue.dev extension                                    │
│  Use:     Tab autocomplete + Chat (@codebase, /review, /test)      │
│  Config:  continue-dev/config.yaml → ~/.continue/config.yaml       │
│                                                                     │
│  PERSONA 2: "Power User" (15% of team)                             │
│  ───────────────────────────────────────                            │
│  IDE:     VS Code                                                   │
│  Install: Continue.dev + Cline                                      │
│  Use:     Everything above + agent mode for refactors, new features │
│  Config:  Both config files                                         │
│                                                                     │
│  PERSONA 3: "JetBrains User" (5% of team)                          │
│  ──────────────────────────────────────────                         │
│  IDE:     IntelliJ / PyCharm / WebStorm                             │
│  Install: Tabby plugin                                              │
│  Use:     Tab autocomplete + Chat                                   │
│  Config:  Set Tabby endpoint in IDE settings                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. Observability & Guardrails Stack

### 9.1 Monitoring Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     OBSERVABILITY STACK                                   │
│                     (All on Gateway Mac Mini)                             │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                      GRAFANA (:3000)                             │    │
│  │                                                                 │    │
│  │  Dashboard: "ML Cluster Overview"                               │    │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────────┐   │    │
│  │  │ Cluster   │ │ Request   │ │ Token     │ │ Guardrails    │   │    │
│  │  │ Health    │ │ Perf.     │ │ Usage     │ │               │   │    │
│  │  │           │ │           │ │           │ │ • Blocks      │   │    │
│  │  │ • Node up │ │ • Latency │ │ • In/Out  │ │ • Secrets     │   │    │
│  │  │ • Models  │ │   P50/95  │ │   per min │ │   flagged     │   │    │
│  │  │ • CPU/RAM │ │ • TTFT    │ │ • Context │ │ • PII caught  │   │    │
│  │  │           │ │ • Errors  │ │   window  │ │ • Rate limits │   │    │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────────┘   │    │
│  │  ┌─────────────────────────────────────────────────────────┐   │    │
│  │  │                    LIVE LOGS                              │   │    │
│  │  │  Ollama + LiteLLM + Guardrails — all nodes               │   │    │
│  │  └─────────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                         │                    │                          │
│               ┌─────────▼────────┐  ┌────────▼─────────┐               │
│               │   PROMETHEUS     │  │      LOKI        │               │
│               │   (:9090)        │  │    (:3100)       │               │
│               │                  │  │                  │               │
│               │  Scrapes:        │  │  Receives:       │               │
│               │  • LiteLLM /met. │  │  • Ollama logs   │               │
│               │  • Ollama /met.  │  │  • LiteLLM logs  │               │
│               │  • node_exporter │  │  • Guardrail logs│               │
│               │  • Loki health   │  │  (from all nodes)│               │
│               │                  │  │                  │               │
│               │  Alerts:         │  │                  │               │
│               │  • Node down     │  │                  │               │
│               │  • High latency  │  │                  │               │
│               │  • Error rate    │  │                  │               │
│               │  • Memory >90%   │  │                  │               │
│               │  • Token budget  │  │                  │               │
│               └──────────────────┘  └──────────────────┘               │
│                                                                         │
│  Each inference node runs PROMTAIL → ships logs to Loki                 │
│  Each inference node runs NODE_EXPORTER → scraped by Prometheus         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Guardrails Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GUARDRAILS PIPELINE                                │
│                    (runs inside LiteLLM on every request)            │
│                                                                     │
│  ══════════════════ PRE-CALL CHECKS ══════════════════              │
│                                                                     │
│  Request in ──┐                                                     │
│               ▼                                                     │
│  ┌─────────────────────┐                                            │
│  │ 1. Rate Limiting    │ → Block if > 30 req/min/user              │
│  │    (per user)       │                                            │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 2. Token Budget     │ → Block if > 100K tokens/hr/user          │
│  │    (per user)       │                                            │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 3. Secret Scanner   │ → Warn if input contains API keys,        │
│  │    (input)          │   passwords, private keys, AWS creds      │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 4. Context Window   │ → Auto-truncate if input > model limit    │
│  │    Manager          │   Strategy: keep_recent / middle_out       │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 5. Grounding Prompt │ → Inject anti-hallucination system prompt  │
│  │    Injection        │   "Do not invent APIs or imports"          │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 6. Temperature Cap  │ → Force temp ≤ 0.3 for code,              │
│  │                     │   ≤ 0.1 for autocomplete                  │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│        Send to Model                                                │
│                                                                     │
│  ══════════════════ POST-CALL CHECKS ═════════════════              │
│                                                                     │
│        Model responds ──┐                                           │
│                         ▼                                           │
│  ┌─────────────────────┐                                            │
│  │ 7. Secret Scanner   │ → BLOCK if response leaks secrets         │
│  │    (output)         │   Replace with "[REDACTED]"               │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 8. PII Scanner      │ → Warn if response contains emails,       │
│  │    (output)         │   phone numbers, SSN-like patterns        │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│  ┌─────────────────────┐                                            │
│  │ 9. Hallucination    │ → Flag fabricated URLs, uncertain claims,  │
│  │    Heuristics       │   suspicious package names                │
│  └──────────┬──────────┘                                            │
│             ▼                                                       │
│       Response to Developer                                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Recommendation

### 10.1 For 2,000 Developers: Option D (Hybrid) is the Only Viable Path

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│                   RECOMMENDED ARCHITECTURE                               │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │                     PHASE 1 (Week 1-2)                        │      │
│  │                     Prove it works on 2 Mac Minis             │      │
│  │                                                               │      │
│  │  • Set up Mac Mini 1 (M1) as gateway + monitoring             │      │
│  │  • Set up Mac Mini 2 (M3) as inference node                   │      │
│  │  • Deploy Ollama + LiteLLM + monitoring stack                 │      │
│  │  • Test with 5-10 developers                                  │      │
│  │  • Validate: autocomplete latency, chat quality, dashboards   │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │                     PHASE 2 (Week 3-4)                        │      │
│  │                     Scale local, add cloud                     │      │
│  │                                                               │      │
│  │  • Deploy all 20 Mac Minis via Ansible                        │      │
│  │  • Add cloud API keys to LiteLLM (Claude/GPT-4)              │      │
│  │  • Configure intelligent routing (local autocomplete,         │      │
│  │    cloud chat)                                                │      │
│  │  • Set up per-team token budgets                              │      │
│  │  • Deploy Tabby on dedicated nodes for best autocomplete      │      │
│  │  • Test with 50-100 developers                                │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │                     PHASE 3 (Month 2)                         │      │
│  │                     Full rollout to 2,000 devs                │      │
│  │                                                               │      │
│  │  • Roll out IDE configs to all developers                     │      │
│  │  • Monitor usage patterns in Grafana                          │      │
│  │  • Tune: model selection, caching TTL, rate limits            │      │
│  │  • Identify hot teams (high usage) → allocate more budget     │      │
│  │  • Consider: more Mac Minis if autocomplete latency is high   │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                              │                                          │
│                              ▼                                          │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │                     PHASE 4 (Month 3+)                        │      │
│  │                     Optimize and expand                        │      │
│  │                                                               │      │
│  │  • Add more Mac Minis based on Grafana capacity data          │      │
│  │  • Evaluate: exo for premium 70B tier (if demand exists)      │      │
│  │  • Evaluate: fine-tuning models on your codebase              │      │
│  │  • Evaluate: Cursor Business licenses for power users         │      │
│  │  • Build internal leaderboard / usage analytics               │      │
│  └───────────────────────────────────────────────────────────────┘      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Recommended Node Allocation (20 Mac Minis)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    NODE ALLOCATION PLAN                               │
├──────────┬──────────┬──────────────────────────────────────────────┤
│ Nodes    │ Role     │ What it runs                                 │
├──────────┼──────────┼──────────────────────────────────────────────┤
│ 1        │ Gateway  │ LiteLLM + Nginx + Prometheus + Grafana +    │
│ (M1 8GB) │          │ Loki + phi3:mini (health checks)            │
├──────────┼──────────┼──────────────────────────────────────────────┤
│ 14       │ Autocomp │ Tabby (StarCoder2-3B) — dedicated to tab    │
│ (M4 16GB)│ Pool     │ autocomplete. ~14 concurrent completions.   │
│          │          │ Each also has Ollama qwen2.5-coder:7b for   │
│          │          │ chat fallback when cloud is unavailable.     │
├──────────┼──────────┼──────────────────────────────────────────────┤
│ 4        │ Chat     │ Ollama qwen2.5-coder:7b — local chat for   │
│ (M4 16GB)│ Fallback │ when cloud API is slow/down. Also serves    │
│          │          │ Continue.dev @codebase embedding queries.    │
├──────────┼──────────┼──────────────────────────────────────────────┤
│ 1        │ Spare    │ Hot standby. Can replace any failed node.   │
│ (M3 16GB)│          │ Also runs: nomic-embed-text for RAG.        │
└──────────┴──────────┴──────────────────────────────────────────────┘
```

### 10.3 Decision Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Question: Should we use exo (distributed 70B)?                    │
│  Answer:   NOT AS PRIMARY. The math doesn't work:                   │
│            • 70B across 7 nodes = ~5 tok/s = 30-50s per response   │
│            • 1 concurrent request across those 7 nodes             │
│            • 2,000 devs need 3-5 chat req/s                        │
│            • You would need 100+ nodes just for exo chat           │
│                                                                     │
│            USE exo only as an optional "premium tier" if you       │
│            have spare nodes and devs who need high-quality         │
│            answers and are willing to wait 30-60 seconds.          │
│                                                                     │
│  Question: Which IDE tool should we standardize on?                │
│  Answer:   Continue.dev as the primary, Tabby as supplementary.    │
│            Devs can add Cline for agent workflows.                 │
│            All point to the same LiteLLM endpoint.                 │
│                                                                     │
│  Question: How much will the cloud API cost?                       │
│  Answer:   ~$4,000-8,000/month for 2,000 devs.                    │
│            That's $2-4 per developer — vs $19-40 for SaaS tools.   │
│            LiteLLM caching will reduce this significantly.         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. Reference Links

### 11.1 Core Infrastructure

| Tool | What it does | Link |
|------|-------------|------|
| **Ollama** | Local LLM serving on macOS/Linux | https://ollama.com |
| **LiteLLM** | OpenAI-compatible proxy, routing, rate limiting | https://github.com/BerriAI/litellm |
| **Tabby** | Self-hosted AI coding assistant | https://tabby.tabbyml.com |
| **exo** | Distributed LLM inference across devices | https://github.com/exo-explore/exo |

### 11.2 IDE Extensions

| Tool | What it does | Link |
|------|-------------|------|
| **Continue.dev** | Open-source AI coding in VS Code / JetBrains | https://continue.dev |
| **Cline** | Autonomous AI agent in VS Code | https://github.com/cline/cline |
| **Cursor** | AI-native code editor (fork of VS Code) | https://cursor.com |
| **Tabby Extension** | VS Code / JetBrains autocomplete | https://tabby.tabbyml.com/docs/extensions |

### 11.3 Models (Recommended for Apple Silicon)

| Model | Size | Best for | Ollama command |
|-------|------|----------|----------------|
| **Qwen 2.5 Coder 7B** | ~4.5 GB | Code chat, inline edit | `ollama pull qwen2.5-coder:7b` |
| **Qwen 2.5 Coder 3B** | ~2.0 GB | Fast chat fallback | `ollama pull qwen2.5-coder:3b` |
| **Phi-3 Mini** | ~2.3 GB | Fast general chat | `ollama pull phi3:mini` |
| **StarCoder2 3B** | ~2.0 GB | Code completion (FIM) | Via Tabby |
| **DeepSeek Coder V2 Lite** | ~9 GB | Best local code quality | `ollama pull deepseek-coder-v2:16b` |
| **Nomic Embed Text** | ~0.3 GB | Embeddings for RAG | `ollama pull nomic-embed-text` |
| **Llama 3.1 70B** | ~40 GB | (exo only) High quality chat | Needs distributed |

### 11.4 Observability

| Tool | What it does | Link |
|------|-------------|------|
| **Prometheus** | Metrics collection and alerting | https://prometheus.io |
| **Grafana** | Visualization and dashboards | https://grafana.com |
| **Loki** | Log aggregation (like CloudWatch/Splunk) | https://grafana.com/oss/loki |
| **Promtail** | Log shipper for Loki | https://grafana.com/docs/loki/latest/send-data/promtail |

### 11.5 Deployment

| Tool | What it does | Link |
|------|-------------|------|
| **Ansible** | Agentless infrastructure automation | https://docs.ansible.com |
| **Colima** | Lightweight Docker for macOS (replaces Docker Desktop) | https://github.com/abiosoft/colima |

### 11.6 Cloud API Providers (for Hybrid approach)

| Provider | Model | Pricing (per 1M tokens) | Link |
|----------|-------|------------------------|------|
| **Anthropic** | Claude Sonnet 4 | ~$3 in / $15 out | https://docs.anthropic.com/en/docs/about-claude/models |
| **OpenAI** | GPT-4o | ~$2.50 in / $10 out | https://platform.openai.com/docs/models |
| **DeepSeek** | DeepSeek V3 | ~$0.27 in / $1.10 out | https://platform.deepseek.com |
| **Google** | Gemini 2.0 Flash | ~$0.10 in / $0.40 out | https://ai.google.dev/pricing |

### 11.7 Further Reading

- Tabby self-hosted deployment guide: https://tabby.tabbyml.com/docs/administration
- LiteLLM proxy configuration: https://docs.litellm.ai/docs/proxy/configs
- Continue.dev configuration: https://docs.continue.dev/reference
- Ollama model library: https://ollama.com/library
- Apple MLX framework (alternative to Ollama): https://github.com/ml-explore/mlx

---

*Document generated for ML Cluster Architecture Decision. All configurations and Ansible playbooks are in the `mac-mini-cluster/` directory alongside this document.*
