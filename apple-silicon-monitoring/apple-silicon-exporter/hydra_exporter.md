# Apple Silicon Exporter

Cross-platform Prometheus exporter for Apple Silicon (M1/M2/M3/M4/M5) hardware metrics including GPU utilization, power consumption, thermal state, and system metrics.

On **macOS** it uses native APIs (IOKit, Metal, `powermetrics`) for direct hardware access. On **Linux** — including **Asahi Linux** running on Apple Silicon, as well as generic x86-64 and ARM64 hosts — it reads metrics from `/proc` and `/sys` (hwmon, DRM, RAPL powercap) and **degrades gracefully**: metrics with no Linux data source are simply omitted rather than reported as misleading zeros.

> **Chip detection is generic.** The exporter recognises `M1`–`M5` (and any future M-series part) plus the `Pro`/`Max`/`Ultra` variants where detectable, and falls back to the raw model string for hardware it does not recognise. See `apple_chip_info` below.
>
> For NVIDIA GPUs on Linux, use [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter); for AMD GPUs, use [amdgpu_exporter](https://github.com/amdgpu-exporter/amdgpu_exporter). The rest of this monitoring platform (LLM telemetry SDK, OTEL pipeline, dashboards, alerts) is hardware-agnostic.

## Features

- **GPU Metrics**: Utilization, memory usage, temperature, power (macOS: IOKit + Metal; Linux: hwmon temperature where available)
- **Power Metrics**: System, CPU cluster, GPU, and Neural Engine power (macOS: `powermetrics`; Linux: hwmon `power*_input` or RAPL powercap for total system power)
- **Thermal Metrics**: Thermal pressure level, throttling state (macOS: IOKit; not exposed by the kernel on Linux today)
- **System Metrics**: CPU usage, memory, load average (macOS: sysctl/vm_stat; Linux: `/proc/stat`, `/proc/meminfo`, `/proc/loadavg`)
- **Chip Identification**: `apple_chip_info` info-metric carrying the detected chip family/variant

## Requirements

### macOS
- macOS 12+ (Monterey or later)
- Apple Silicon Mac (M1, M2, M3, M4, or M5 series)
- Root privileges (for `powermetrics` and IOKit access)

### Linux
- Linux ARM64 (Asahi Linux on Apple Silicon) or x86-64/ARM64 generic hosts
- No CGO required — the Linux build is pure Go
- Read access to `/proc` and `/sys` (RAPL energy counters may require root)

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/company/apple-silicon-exporter.git
cd apple-silicon-exporter

# Build
make build

# Install (requires sudo)
sudo make install

# Start service
sudo make start
```

### Manual Installation

```bash
# Build
go build -o apple-silicon-exporter ./cmd/exporter

# Install binary
sudo cp apple-silicon-exporter /usr/local/bin/
sudo chmod 755 /usr/local/bin/apple-silicon-exporter

# Create directories
sudo mkdir -p /var/log/apple-silicon-exporter
sudo mkdir -p /var/lib/apple-silicon-exporter

# Install LaunchDaemon
sudo cp deploy/launchdaemons/com.company.apple-silicon-exporter.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.company.apple-silicon-exporter.plist
sudo chmod 644 /Library/LaunchDaemons/com.company.apple-silicon-exporter.plist

# Load and start
sudo launchctl load /Library/LaunchDaemons/com.company.apple-silicon-exporter.plist
```

## Usage

```bash
# Run with default settings
sudo apple-silicon-exporter

# Custom listen address
sudo apple-silicon-exporter --listen=0.0.0.0:9101

# Enable/disable collectors
sudo apple-silicon-exporter --enable-iokit=true --enable-powermetrics=true --enable-metal=true

# Debug logging
sudo apple-silicon-exporter --log-level=debug
```

### Linux

```bash
# Build for the host (pure Go, no CGO/root needed to build)
make build            # or: make build-linux-amd64 / make build-linux-arm64

# Run (root only needed for RAPL system-power on some hosts)
./build/apple-silicon-exporter --listen=0.0.0.0:9101
```

On Linux the macOS-specific flags (`--enable-metal`, `--enable-powermetrics`, `--powermetrics-path`) are accepted for compatibility but unavailable collectors disable themselves and report `apple_scrape_success{collector="..."} 0`.

## Metrics

### Chip Information

| Metric | Type | Description |
|--------|------|-------------|
| `apple_chip_info` | Gauge | Constant `1`; chip details carried in labels |

Labels: `chip` (e.g. `M4 Max`), `family` (e.g. `M4`), `variant` (`Pro`/`Max`/`Ultra`/empty), `model` (raw `hw.model` / device-tree model). On unrecognised hardware `family`/`variant` are empty and `chip`/`model` carry the raw identifier.

```
apple_chip_info{chip="M4 Max",family="M4",variant="Max",model="Mac15,9"} 1
```

### GPU Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `apple_gpu_utilization_percent` | Gauge | GPU utilization percentage |
| `apple_gpu_memory_used_bytes` | Gauge | GPU memory in use |
| `apple_gpu_memory_total_bytes` | Gauge | Total GPU memory available |
| `apple_gpu_temperature_celsius` | Gauge | GPU temperature |
| `apple_gpu_power_watts` | Gauge | GPU power consumption |

### CPU Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `apple_cpu_power_watts` | Gauge | CPU cluster power (efficiency/performance) |
| `apple_cpu_usage_percent` | Gauge | CPU usage by mode (user/system/idle) |

### Neural Engine Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `apple_ane_power_watts` | Gauge | Neural Engine power consumption |
| `apple_ane_utilization_percent` | Gauge | Neural Engine utilization |

### Thermal Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `apple_thermal_pressure` | Gauge | Thermal pressure level (nominal/moderate/heavy/critical) |
| `apple_thermal_throttle_active` | Gauge | Whether throttling is active (cpu/gpu) |

### System Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `apple_system_power_watts` | Gauge | Total system power consumption |
| `apple_memory_used_bytes` | Gauge | System memory in use |
| `apple_memory_total_bytes` | Gauge | Total system memory |

## Platform Metric Availability

Where a metric has no data source on a platform it is omitted from `/metrics` and the corresponding `apple_scrape_success{collector="..."}` reflects whether that collector produced data.

| Metric | macOS (Apple Silicon) | Asahi Linux (Apple Silicon) | Generic Linux (x86-64) |
|--------|:---------------------:|:---------------------------:|:----------------------:|
| `apple_chip_info` | ✅ (sysctl) | ✅ (device-tree) | ✅ (raw model fallback) |
| `apple_cpu_usage_percent` | ✅ | ✅ (`/proc/stat`) | ✅ (`/proc/stat`) |
| `apple_memory_used_bytes` / `apple_memory_total_bytes` | ✅ | ✅ (`/proc/meminfo`) | ✅ (`/proc/meminfo`) |
| `apple_gpu_temperature_celsius` | ✅ (IOKit/SMC) | ⚠️ if a GPU hwmon sensor is present | ❌ |
| `apple_gpu_utilization_percent` | ✅ (IOKit/Metal) | ❌ (not exposed by kernel) | ❌ |
| `apple_gpu_memory_used_bytes` / `_total_bytes` | ✅ (IOKit) | ❌ | ❌ |
| `apple_gpu_power_watts` | ✅ (`powermetrics`) | ❌ | ❌ |
| `apple_cpu_power_watts` (per cluster) | ✅ (`powermetrics`) | ❌ | ❌ |
| `apple_ane_power_watts` / `apple_ane_utilization_percent` | ✅ (`powermetrics`) | ❌ | ❌ |
| `apple_system_power_watts` | ✅ (`powermetrics`) | ⚠️ hwmon `power*_input` if present | ⚠️ RAPL powercap if present |
| `apple_thermal_pressure` / `apple_thermal_throttle_active` | ✅ (IOKit) | ❌ (not exposed by kernel) | ❌ |

Legend: ✅ available · ⚠️ available when the hardware/kernel exposes the underlying sensor · ❌ not available (omitted).

> **Note on `apple_system_power_watts` via RAPL:** RAPL reports cumulative energy, so power is computed from the delta between two scrapes. The first scrape after start primes the counter and reports no value; subsequent scrapes report watts.

## Configuration

### Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--listen` | `127.0.0.1:9101` | Address to listen on |
| `--metrics-path` | `/metrics` | Metrics endpoint path |
| `--log-level` | `info` | Log level (debug/info/warn/error) |
| `--enable-iokit` | `true` | Enable IOKit collector |
| `--enable-powermetrics` | `true` | Enable powermetrics collector |
| `--enable-metal` | `true` | Enable Metal collector |
| `--powermetrics-path` | `/usr/bin/powermetrics` | Path to powermetrics |
| `--powermetrics-interval` | `1s` | Powermetrics sample interval |

## Prometheus Configuration

```yaml
scrape_configs:
  - job_name: 'apple-silicon'
    static_configs:
      - targets: ['localhost:9101']
    scrape_interval: 15s
```

## Grafana Dashboards

Import the dashboards from the project root `dashboards/` directory:
- `fleet-overview.json` — cluster-wide GPU heatmap, throughput, and thermal status
- `node-deep-dive.json` — per-node GPU, CPU, thermal, and LLM performance
- `quality-monitor.json` — hallucination risk scoring and quality signals

## Troubleshooting

### Permission Denied

The exporter requires root privileges to access hardware metrics:

```bash
# Run as root
sudo apple-silicon-exporter

# Or use LaunchDaemon (recommended for production)
sudo launchctl load /Library/LaunchDaemons/com.company.apple-silicon-exporter.plist
```

### Missing Metrics

Some metrics require specific conditions:
- GPU metrics require active GPU workload for meaningful values
- Power metrics require powermetrics access (root)
- Metal counters may not be available on all macOS versions

### Service Not Starting

Check logs:

```bash
# View logs
tail -f /var/log/apple-silicon-exporter/exporter.log
tail -f /var/log/apple-silicon-exporter/exporter.err

# Check LaunchDaemon status
sudo launchctl list | grep apple-silicon
```

## Development

```bash
# Run tests
make test

# Run with coverage
make test-coverage

# Lint
make lint

# Format
make fmt

# Run in dev mode
make run
```

## Architecture

Each sub-collector package exposes a common set of types and a `Collect()` method. Platform-specific implementations are selected at compile time via build tags (`//go:build darwin` / `//go:build linux`), so `collector.go` is platform-agnostic and only emits metrics that the active platform reports as available.

```
apple-silicon-exporter/
├── cmd/exporter/             # Main entry point (pflag CLI, HTTP server)
├── internal/
│   ├── collector/            # Platform-agnostic Prometheus collector
│   ├── chip/                 # Chip family/variant detection
│   │   ├── chip.go           #   generic brand-string parser (shared)
│   │   ├── chip_darwin.go    #   sysctl machdep.cpu.brand_string / hw.model
│   │   └── chip_linux.go     #   /proc/device-tree, /proc/cpuinfo
│   ├── iokit/                # GPU + thermal
│   │   ├── types.go          #   shared metric types
│   │   ├── iokit_darwin.go   #   IOKit/CoreFoundation bindings (CGO)
│   │   └── iokit_linux.go    #   /sys/class/hwmon, /sys/class/drm
│   ├── metal/                # GPU (macOS only)
│   │   ├── types.go
│   │   ├── metal_darwin.go   #   Metal framework bindings (CGO)
│   │   └── metal_linux.go    #   no-op (returns unsupported)
│   ├── powermetrics/         # Power
│   │   ├── types.go
│   │   ├── powermetrics_darwin.go  # parses `powermetrics` JSON
│   │   └── powermetrics_linux.go   # hwmon power*_input / RAPL powercap
│   └── system/               # CPU + memory
│       ├── types.go
│       ├── system_darwin.go  #   sysctl / vm_stat
│       └── system_linux.go   #   /proc/stat, /proc/meminfo, /proc/loadavg
├── .github/workflows/ci.yml  # CI: build/vet/test/gofmt/golangci-lint (linux + macos)
├── Makefile                  # Cross-compile, test, lint, fmt targets
└── go.mod / go.sum           # Go 1.23+ module
```

### Build Targets

| Target | GOOS/GOARCH | CGO | Notes |
|--------|-------------|-----|-------|
| `make build` | host | auto | CGO on macOS, off on Linux |
| `make build-darwin-arm64` | darwin/arm64 | 1 | Requires macOS SDK (build on a Mac) |
| `make build-linux-arm64` | linux/arm64 | 0 | Asahi Linux / ARM64 |
| `make build-linux-amd64` | linux/amd64 | 0 | Generic x86-64 |
| `make build-all` | all of the above | — | darwin target only succeeds on a Mac |
| `make release` | all | — | Packages tarballs into `dist/` |

Quality targets: `make check` runs `fmt-check` + `vet` + `test`; `make lint` runs `golangci-lint`.

### Data Collection Methods

| Data | macOS source | Linux source |
|------|--------------|--------------|
| Chip family/variant | `sysctl machdep.cpu.brand_string` | `/proc/device-tree/{model,compatible}`, `/proc/cpuinfo` |
| GPU utilization | IOKit / Metal | — (not exposed) |
| GPU memory | IOKit | — |
| GPU temperature | IOKit / SMC | `/sys/class/hwmon` (GPU sensor) |
| GPU/CPU/ANE power | `powermetrics` | — (per-domain power not exposed) |
| System power | `powermetrics` | hwmon `power*_input` or RAPL powercap |
| Thermal pressure | IOKit | — (not exposed) |
| CPU/memory | sysctl, vm_stat | `/proc/stat`, `/proc/meminfo` |

## License

Apache 2.0
