# otel-mac-agent — Offline Dependencies

This directory holds binaries the role copies to target Mac Minis.
**No internet access is required on the targets** — all files ship from here.

Files in this directory are excluded from git (see `.gitignore`).
An operator must download/build them once before running the playbook.

---

## Required files

### 1. otelcol-contrib (OpenTelemetry Collector Contrib)

Download the darwin arm64 binary on a machine with internet access:

```bash
VERSION=0.96.0
curl -LO https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/otelcol-contrib_${VERSION}_darwin_arm64.tar.gz

# Verify checksum (recommended)
curl -LO https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/otelcol-contrib_${VERSION}_checksums.txt
shasum -a 256 -c otelcol-contrib_${VERSION}_checksums.txt --ignore-missing

# Place here
mv otelcol-contrib_${VERSION}_darwin_arm64.tar.gz \
   /path/to/hydra/ansible/roles/otel-mac-agent/files/otelcol-contrib_darwin_arm64.tar.gz
```

Filename expected by the role: **`otelcol-contrib_darwin_arm64.tar.gz`**

### 2. apple-silicon-exporter

Build from source (requires Go 1.22+ on any macOS machine):

```bash
cd /path/to/hydra/apple-silicon-monitoring/apple-silicon-exporter

GOOS=darwin GOARCH=arm64 go build \
  -o /path/to/hydra/ansible/roles/otel-mac-agent/files/apple-silicon-exporter \
  ./cmd/exporter/
```

Filename expected by the role: **`apple-silicon-exporter`**

---

## Deploy

```bash
cd /path/to/hydra/ansible
ansible-playbook mac-monitoring.yml                      # all mac nodes
ansible-playbook mac-monitoring.yml --limit p1-m3-16g   # one node
ansible-playbook mac-monitoring.yml --tags configure     # config only (no binary copy)
ansible-playbook mac-monitoring.yml --check --diff       # dry-run
```

---

## Verify after deploy

```bash
# OTEL agent health
curl http://<NODE_IP>:13133

# Apple Silicon hardware metrics
curl http://<NODE_IP>:9101/metrics | grep apple_gpu

# OTEL agent Prometheus endpoint (local scrape)
curl http://<NODE_IP>:8889/metrics | head -20
```
