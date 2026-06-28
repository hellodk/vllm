# ansible/roles/hw-exporter/files — pre-staged binaries

This directory holds tarballs and images that the `hw-exporter` role copies to
Linux targets **without** any outbound internet connection on the target host.

## node_exporter (cpu.yml)

| Architecture | File |
|---|---|
| Linux x86_64 | `node_exporter-<version>.linux-amd64.tar.gz` |
| Linux aarch64 | `node_exporter-<version>.linux-arm64.tar.gz` |

`<version>` = `cluster.yml:cluster.versions.node_exporter`

Download (on a machine with internet access):

```bash
VER=$(grep 'node_exporter' ansible/cluster.yml | awk '{print $2}' | tr -d '"')
curl -fsSL -o ansible/roles/hw-exporter/files/node_exporter-${VER}.linux-amd64.tar.gz \
  "https://github.com/prometheus/node_exporter/releases/download/v${VER}/node_exporter-${VER}.linux-amd64.tar.gz"
```

## dcgm-exporter (nvidia.yml)

| File | Description |
|---|---|
| `dcgm-exporter-<version>.tar` | Docker image saved with `docker save` |

`<version>` = `cluster.yml:cluster.versions.dcgm_exporter`

Export procedure (on a machine with internet access and Docker):

```bash
VER=$(grep 'dcgm_exporter' ansible/cluster.yml | awk '{print $2}' | tr -d '"')
docker pull nvcr.io/nvidia/k8s/dcgm-exporter:${VER}-ubuntu22.04
docker save nvcr.io/nvidia/k8s/dcgm-exporter:${VER}-ubuntu22.04 \
  -o ansible/roles/hw-exporter/files/dcgm-exporter-${VER}.tar
```

Transfer this directory to the Ansible control node before running the playbook.
Files listed in `.gitignore` — do **not** commit binary tarballs or image archives.
