# mlx-rdma-exporter — air-gapped binary staging (SRE-3)

Stage the prebuilt RDMA/RoCE exporter here, named
`mlx-rdma-exporter_<version>_<os>_<arch>`:

```
mlx-rdma-exporter_0.1.0_darwin_arm64    # Apple Silicon largepool (Thunderbolt ConnectX)
mlx-rdma-exporter_0.1.0_linux_arm64
mlx-rdma-exporter_0.1.0_linux_amd64
```

`<version>` matches `hydra_versions.mlx_rdma_exporter`.

> The exporter source (Go) is owned by the monitoring team — this ansible role
> only stages and runs the prebuilt artifact. Binaries are git-ignored.
