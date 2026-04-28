# Apple Silicon Exporter

Prometheus exporter for Apple Silicon (M1/M2/M3/M4) Mac hardware metrics including GPU utilization, power consumption, thermal state, and system metrics. Uses native macOS APIs (IOKit, Metal, powermetrics) for direct hardware access.

> **Platform:** macOS only. For NVIDIA GPUs on Linux, use [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter). For AMD GPUs, use [amdgpu_exporter](https://github.com/amdgpu-exporter/amdgpu_exporter). The rest of this monitoring platform (LLM telemetry SDK, OTEL pipeline, dashboards, alerts) is hardware-agnostic.

## Features

- **GPU Metrics**: Utilization, memory usage, temperature, power (via IOKit and Metal)
- **Power Metrics**: System, CPU cluster, GPU, and Neural Engine power consumption (via powermetrics)
- **Thermal Metrics**: Thermal pressure level, throttling state (via IOKit)
- **System Metrics**: CPU usage, memory, load average (via sysctl/vm_stat)

## Requirements

- macOS 12+ (Monterey or later)
- Apple Silicon Mac (M1, M2, M3, M4 series)
- Root privileges (for powermetrics and IOKit access)

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

## Metrics

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

```
apple-silicon-exporter/
├── cmd/exporter/          # Main entry point
├── internal/
│   ├── collector/         # Main Prometheus collector (registers all sub-collectors)
│   ├── iokit/            # IOKit framework bindings (CGO) — GPU, thermal
│   ├── metal/            # Metal framework bindings (CGO) — performance counters
│   ├── powermetrics/     # powermetrics output parser — power consumption
│   └── system/           # System metrics (sysctl/vm_stat) — CPU, memory
├── Makefile              # Build, install, test, lint targets
└── go.mod / go.sum       # Go 1.22+ module
```

### Data Collection Methods

| Data | Source | API |
|------|--------|-----|
| GPU utilization | IOKit | Metal Performance State |
| GPU memory | IOKit | GPUMemoryAllocated |
| GPU temperature | IOKit | Thermal management |
| GPU/CPU/ANE power | powermetrics | Binary execution and output parsing |
| Thermal pressure | IOKit | Thermal state levels |
| CPU/memory | sysctl, vm_stat | Standard UNIX APIs |

## License

Apache 2.0
