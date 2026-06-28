# apple-silicon-exporter — air-gapped binary staging (ANS-7)

This role installs a **pre-built, sha256-pinned** `apple-silicon-exporter` binary.
No internet access and no build-from-source on targets.

## Stage the per-arch binaries here

Build on a connected machine from the exporter repo
(`feat/silicon-exporter-m1-m5-linux`) and drop the artifacts in this directory,
named `apple-silicon-exporter_<version>_<os>_<arch>`:

```
apple-silicon-exporter_1.0.0_darwin_arm64    # M1–M5 Mac Mini
apple-silicon-exporter_1.0.0_linux_arm64     # ARM Linux
apple-silicon-exporter_1.0.0_linux_amd64     # x86_64 Linux
```

`<version>` must match `hydra_versions.apple_silicon_exporter` (cluster.yml /
group_vars). Example build:

```bash
cd apple-silicon-monitoring/apple-silicon-exporter
GOOS=darwin GOARCH=arm64 go build \
  -o ../../ansible/roles/apple-silicon-exporter/files/apple-silicon-exporter_1.0.0_darwin_arm64 \
  ./cmd/exporter/
```

## Record the checksum

Compute sha256 and add it under `hydra_artifact_checksums.apple_silicon_exporter`
in `ansible/group_vars/all/artifacts.yml` (ANS-4):

```bash
shasum -a 256 apple-silicon-exporter_1.0.0_darwin_arm64
```

The role refuses to install a binary whose sha256 is missing or mismatched.

> Binaries are intentionally git-ignored (see `.gitignore`); only this README and
> the checksum manifest are committed.
