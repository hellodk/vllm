# snmp-exporter — air-gapped binary staging (SRE-3)

Stage the prebuilt prometheus `snmp_exporter` here, named
`snmp_exporter_<version>_<os>_<arch>`:

```
snmp_exporter_0.26.0_linux_amd64
snmp_exporter_0.26.0_linux_arm64
```

`<version>` matches `hydra_versions.snmp_exporter`. Download from
<https://github.com/prometheus/snmp_exporter/releases> on a connected host and
copy here. Binaries are git-ignored.

> The prometheus scrape job that polls this exporter is owned by the monitoring
> team (monitoring/prometheus) and is out of scope for the ansible branch.
