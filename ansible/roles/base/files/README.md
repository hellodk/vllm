# ansible/roles/base/files — pre-staged binaries

This directory holds tarballs that the `base` role copies to Linux targets
**without** any outbound internet connection on the target host.

## Required files

Place the appropriate tarball(s) here before running `ansible/site.yml`:

| Architecture | File | Where to download |
|---|---|---|
| Linux x86_64 | `otelcol-contrib_<version>_linux_amd64.tar.gz` | GitHub Releases |
| Linux aarch64 | `otelcol-contrib_<version>_linux_arm64.tar.gz` | GitHub Releases |

Download URL (replace `<version>` with `cluster.yml:cluster.versions.otel_agent`):

```
https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v<version>
```

## Air-gap staging procedure

On a machine with internet access:

```bash
VER=$(grep 'otel_agent' ansible/cluster.yml | awk '{print $2}' | tr -d '"')
curl -fsSL -o ansible/roles/base/files/otelcol-contrib_${VER}_linux_amd64.tar.gz \
  "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VER}/otelcol-contrib_${VER}_linux_amd64.tar.gz"
# For aarch64 targets, download the arm64 variant as well.
```

Transfer this directory to the Ansible control node before running the playbook.
Files listed in `.gitignore` — do **not** commit binary tarballs.
