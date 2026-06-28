# _archive — Unloaded Alert Configs

These files are **NOT active**. Prometheus loads rules exclusively from
`/etc/prometheus/rules/*.yml` (configured in
`monitoring/prometheus/prometheus.yml`). Nothing in this
`apple-silicon-monitoring/alerts/` tree is ever evaluated.

The files were moved here to prevent them from masquerading as deployed
configuration and to eliminate confusion with the canonical rule set.

## Archived files

| File | Reason |
|------|--------|
| `apple-silicon-hardware.yaml` | Duplicate / conflicting hardware alert rules; superseded by `monitoring/prometheus/rules/apple-silicon-alerts.yml` (the live file). Also carried a contradictory `apple_thermal_pressure` contract (`{level="critical"}` label-encoded) that conflicts with the canonical numeric gauge defined in SRE-6. |
| `alertmanager.yaml` | Standalone Alertmanager config fragment that was never wired to any deployed Alertmanager instance. Active routing config lives in `monitoring/alertmanager/`. |

## Files NOT yet archived (pending LLM-1)

`llm-inference.yaml` and `hallucination-detection.yaml` are being migrated /
retired under ticket **LLM-1** on a parallel branch. They should be relocated
or deleted after that merge rather than moved here preemptively (to avoid a
merge collision).

## Active rule files

All production-evaluated Prometheus alert rules live under:

```
monitoring/prometheus/rules/
├── apple-silicon-alerts.yml   ← Apple Silicon hardware (canonical)
└── ...
```

Do **not** create new alert rules in `apple-silicon-monitoring/alerts/` —
they will not be loaded by Prometheus.
