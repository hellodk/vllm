# Hydra Network Design — 10 GbE + RDMA RoCE-v2

**Revision**: 1.0 — 2026-05-16  
**Switch**: 10 GbE managed (28-port + 2× 25 GbE uplinks for future expansion)  
**Protocol**: RoCE v2 (RDMA over Converged Ethernet) — lossless Ethernet required  
**MTU**: 9000 (jumbo frames — reduces CPU overhead on large model transfers)  
**Subnet**: 192.168.10.0/24 · **Domain**: hydra.local

---

## IP Address Allocation

| IP | Hostname | Role |
|----|---------|------|
| 192.168.10.1 | switch | 10 GbE switch management |
| 192.168.10.10 | hydra-gw-01 | LiteLLM GW 1 :4000 · HAProxy **active** · dnsmasq (hydra.local) |
| 192.168.10.11 | hydra-gw-02 | LiteLLM GW 2 :4000 · HAProxy **passive** |
| 192.168.10.12 | hydra-store-01 | MinIO :9000 · Model Registry :8100 |
| 192.168.10.13 | hydra-svc-01 | Fleet API :8000 · PostgreSQL :5432 · Redis :6379 · VictoriaMetrics :8428 · Grafana :3000 · Alertmanager :9093 · NTP · Salt Master |
| 192.168.10.20–29 | hydra-fast-01 to -10 | **FastPool** (8B Q4_K_M) |
| 192.168.10.30–33 | hydra-reason-01 to -04 | **ReasonPool** (14B Q4_K_M) |
| 192.168.10.40–43 | hydra-large-01 to -04 | **LargePool** (TP pairs, RDMA) |
| 192.168.10.50–52 | hydra-vision-01 to -03 | **VisionPool** (8B VL Q4_K_M) |
| 192.168.10.60–61 | hydra-embed-01 to -02 | **EmbedPool** (BGE-M3) |
| 192.168.10.70 | hydra-speech-01 | **SpeechPool** (Whisper) |

---

## RDMA Configuration (RoCE v2)

RDMA enables sub-2 μs AllReduce latency for LargePool tensor-parallel groups. Without it, cross-node AllReduce at 1 Gbps takes ~3,200 ms for an 8B model — impractical. At 10 GbE it's ~320 ms — tolerable. With RDMA it's ~2 μs — transparent overhead.

### Switch requirements

```
Priority Flow Control (PFC): enabled on all ports
  Priority 3: RDMA/RoCE traffic (lossless, no drop)
  Priority 0: default (best-effort)
ECN (Explicit Congestion Notification): enabled
DSCP mapping: CS3 (DSCP 24) → Priority 3
Lossless queue: queue 3 reserved for RoCE v2
Jumbo frames: 9216-byte frames on all ports
```

### macOS RDMA setup (choose one)

**Option A — Thunderbolt RDMA adapter (recommended, hardware RDMA):**
```bash
# Connect Mellanox ConnectX-5 via Thunderbolt-to-PCIe enclosure
# (e.g. Sonnet Echo Express III-D with MCX512A-ACAT NIC)
# Install MLNX_OFED driver (Mellanox provides macOS ARM64 builds)

# Verify hardware RDMA
ibv_devices     # → mlx5_0
ibv_devinfo     # → port_state: PORT_ACTIVE, link_layer: Ethernet

# Configure RoCE v2
roce_tos_tos set mlx5_0 1 106   # DSCP 24 mapped to TOS 106
```

**Option B — Software RDMA via libfabric (no hardware required, ~10× slower than hardware):**
```bash
brew install libfabric      # pre-download and stage — no internet on target
export FI_PROVIDER=tcp
export FI_OPT_INTERFACE=en0
# Use with exo or vLLM multi-node
```

**Option C — Optimised TCP (fallback, no RDMA):**
```bash
# macOS TCP tuning for 10 GbE (Ansible role: otel-mac-agent configures this)
sudo sysctl -w net.inet.tcp.sendspace=4194304
sudo sysctl -w net.inet.tcp.recvspace=4194304
sudo sysctl -w net.inet.tcp.mssdflt=8960     # MTU 9000 - headers
sudo sysctl -w net.inet.tcp.win_scale_factor=8
sudo sysctl -w kern.ipc.somaxconn=4096
# LargePool AllReduce at 10 GbE TCP: ~320 ms (32B model) — acceptable for batch
```

### LargePool tensor-parallel groups

```
Group A  (tag=large, tp_group=A):
  hydra-large-01  192.168.10.40
  hydra-large-02  192.168.10.41
  RDMA link:      direct (same switch, PFC queue 3)
  Model:          Qwen 2.5 32B Q4_K_M (~20 GB, 10 GB per node)
  AllReduce:      ~8 ms (hardware RDMA) · ~1,280 ms (10 GbE TCP)

Group B  (tag=large, tp_group=B):
  hydra-large-03  192.168.10.42
  hydra-large-04  192.168.10.43
  Same configuration as Group A — independent concurrent inference
```

---

## Jumbo Frame Configuration (MTU 9000)

Required on all 28 nodes AND on the switch. Reduces per-packet CPU overhead for large model transfers and RDMA.

**Switch**: Enable 9216-byte MTU on all ports (console command varies by vendor).

**Nodes (via Ansible `otel-mac-agent` role):**
```bash
sudo networksetup -setMTU en0 9000

# Verify
networksetup -getMTU en0
# → MTU: 9000

# Test end-to-end (8972 + 28 header = 9000)
ping -D -s 8972 192.168.10.13   # must succeed without fragmentation
```

---

## NTP — Internal Stratum (air-gapped)

All 28 nodes must be time-synchronized for log correlation, Salt scheduling, and certificate validity. `hydra-svc-01` serves NTP internally.

**Server setup (hydra-svc-01, macOS):**
```bash
# One-time initial sync (if internet briefly available during setup):
sudo systemsetup -setnetworktimeserver 0.pool.ntp.org
# Then configure as internal server via /etc/ntp.conf (ntpd) or Chrony

# Verify stratum
ntpq -p   # should show: *LOCAL(0)   .LOCL.   1 l  ...
```

**Client setup (all other nodes — Ansible managed):**
```bash
sudo systemsetup -setnetworktimeserver 192.168.10.13
sudo systemsetup -setusingnetworktime on

# Verify sync
sudo systemsetup -getnetworktimeserver   # → 192.168.10.13
sntp -s 192.168.10.13                    # → clock adjusted
```

---

## Internal DNS — dnsmasq on hydra-gw-01

Resolves `*.hydra.local` → eliminates hardcoded IPs from all service configs.

**Install and configure (hydra-gw-01):**
```bash
# Pre-stage dnsmasq binary (no internet on target)
brew install dnsmasq

cat > /opt/homebrew/etc/dnsmasq.conf << 'DNSCONF'
domain=hydra.local
local=/hydra.local/
expand-hosts

# Infrastructure
address=/hydra-gw-01.hydra.local/192.168.10.10
address=/hydra-gw-02.hydra.local/192.168.10.11
address=/hydra-store-01.hydra.local/192.168.10.12
address=/hydra-svc-01.hydra.local/192.168.10.13

# FastPool
address=/hydra-fast-01.hydra.local/192.168.10.20
address=/hydra-fast-02.hydra.local/192.168.10.21
address=/hydra-fast-03.hydra.local/192.168.10.22
address=/hydra-fast-04.hydra.local/192.168.10.23
address=/hydra-fast-05.hydra.local/192.168.10.24
address=/hydra-fast-06.hydra.local/192.168.10.25
address=/hydra-fast-07.hydra.local/192.168.10.26
address=/hydra-fast-08.hydra.local/192.168.10.27
address=/hydra-fast-09.hydra.local/192.168.10.28
address=/hydra-fast-10.hydra.local/192.168.10.29

# ReasonPool
address=/hydra-reason-01.hydra.local/192.168.10.30
address=/hydra-reason-02.hydra.local/192.168.10.31
address=/hydra-reason-03.hydra.local/192.168.10.32
address=/hydra-reason-04.hydra.local/192.168.10.33

# LargePool (RDMA pairs)
address=/hydra-large-01.hydra.local/192.168.10.40
address=/hydra-large-02.hydra.local/192.168.10.41
address=/hydra-large-03.hydra.local/192.168.10.42
address=/hydra-large-04.hydra.local/192.168.10.43

# VisionPool
address=/hydra-vision-01.hydra.local/192.168.10.50
address=/hydra-vision-02.hydra.local/192.168.10.51
address=/hydra-vision-03.hydra.local/192.168.10.52

# EmbedPool
address=/hydra-embed-01.hydra.local/192.168.10.60
address=/hydra-embed-02.hydra.local/192.168.10.61

# SpeechPool
address=/hydra-speech-01.hydra.local/192.168.10.70
DNSCONF

brew services start dnsmasq
```

**All nodes — point DNS to hydra-gw-01 (Ansible managed):**
```bash
sudo networksetup -setdnsservers en0 192.168.10.10
```

---

## Firewall / Switch ACLs

| Port | Protocol | Source | Destination | Purpose |
|------|----------|--------|-------------|---------|
| 4000 | TCP | Clients | hydra-gw-{01,02} | LiteLLM API |
| 8000 | TCP | All nodes | hydra-svc-01 | Fleet Platform ingest |
| 9000 | TCP | All nodes | hydra-store-01 | MinIO model pull |
| 8100 | TCP | hydra-gw-{01,02} | hydra-store-01 | Model Registry API |
| 4317 | TCP | All nodes | hydra-svc-01 | OTLP/gRPC telemetry |
| 9101 | TCP | hydra-svc-01 | All nodes | Prometheus scrape (apple-exporter) |
| 4505/4506 | TCP | All nodes | hydra-svc-01 | Salt Master ZeroMQ |
| 4317 UDP | — | hydra-large-{01-04} | hydra-large-{01-04} | RoCE v2 collective ops (RDMA) |
| 53 | UDP/TCP | All nodes | hydra-gw-01 | DNS (dnsmasq) |
| 123 | UDP | All nodes | hydra-svc-01 | NTP |
| 22 | TCP | Operator | All nodes | SSH management |

---

## Throughput Reference

| Operation | 1 Gbps (old) | 10 GbE (new) | RDMA RoCE-v2 |
|-----------|-------------|--------------|--------------|
| 8B model pull from MinIO | ~40 s | **~4 s** | — |
| 14B model pull | ~72 s | **~7 s** | — |
| 32B model pull | ~160 s | **~16 s** | — |
| 70B model pull | ~320 s | **~32 s** | — |
| AllReduce 8B weight tensor | ~3,200 ms | ~320 ms | **~2 ms** |
| AllReduce 32B weight tensor | ~12,800 ms | ~1,280 ms | **~8 ms** |
| Salt state push to 28 minions | ~28 s | **~3 s** | — |
| OTEL metrics flush (all nodes) | ~28 s | **~3 s** | — |
