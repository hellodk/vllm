# Dashboard & Alert Multi-Hardware Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the three Grafana dashboards and two alert rule files so they display correct data and fire correctly across Apple Silicon, NVIDIA, and CPU-only nodes — not just Apple Silicon.

**Architecture:** A Python patch script (`patch_dashboards.py`) makes targeted JSON mutations to each dashboard file: replaces Apple-only variable queries with hardware-agnostic equivalents, adds hardware-abstracted metric variables (`$gpu_util_metric` etc.), and rewrites Apple-hardcoded PromQL expressions. Alert YAML files are edited directly. Tests validate every mutation was applied before committing.

**Tech Stack:** Python 3.10+ (json, re), pytest, Grafana dashboard JSON (Grafana 10.3.3), Prometheus alert YAML

**Spec:** `docs/superpowers/specs/2026-04-28-monitoring-ansible-design.md` §8 and §9

---

## File Map

### Modified files

| File | Change |
|------|--------|
| `apple-silicon-monitoring/dashboards/fleet-overview.json` | Fix `$cluster`/`$node` variables; add 7 new variables; fix 7 panel expressions |
| `apple-silicon-monitoring/dashboards/node-deep-dive.json` | Fix `$node` variable; add 6 new variables; rewrite all hardware-metric expressions; change `instance` → `node_id` filter throughout |
| `apple-silicon-monitoring/dashboards/quality-monitor.json` | Add `$provider` variable; add `provider` filter to all panel expressions |
| `apple-silicon-monitoring/alerts/apple-silicon-hardware.yaml` | Add `HardwareExporterDown` alert; add `node_id` to all annotations |
| `apple-silicon-monitoring/alerts/llm-inference.yaml` | Add `node_id` to all alert annotations |

### New files

| File | Purpose |
|------|---------|
| `apple-silicon-monitoring/scripts/patch_dashboards.py` | Idempotent script that applies all JSON mutations to the three dashboards |
| `apple-silicon-monitoring/tests/test_dashboard_patches.py` | pytest tests validating every mutation |

---

## Hardware-Abstracted Variable Definitions

These Grafana Custom variables let each panel query work across hardware types. The user selects the right metric for the active node from a dropdown.

| Variable | Type | Values (comma-separated options) | Default |
|----------|------|-----------------------------------|---------|
| `$gpu_util_metric` | Custom | `apple_gpu_utilization_percent,dcgm_fi_dev_gpu_util,amdgpu_utilization_percent` | `apple_gpu_utilization_percent` |
| `$gpu_mem_used_metric` | Custom | `apple_gpu_memory_used_bytes,dcgm_fi_dev_fb_used,amdgpu_vram_used_bytes` | `apple_gpu_memory_used_bytes` |
| `$gpu_mem_total_metric` | Custom | `apple_gpu_memory_total_bytes,dcgm_fi_dev_fb_total,amdgpu_vram_total_bytes` | `apple_gpu_memory_total_bytes` |
| `$gpu_temp_metric` | Custom | `apple_gpu_temperature_celsius,dcgm_fi_dev_gpu_temp,amdgpu_temp_input` | `apple_gpu_temperature_celsius` |
| `$power_metric` | Custom | `apple_system_power_watts,dcgm_fi_dev_power_usage,amdgpu_power_avg` | `apple_system_power_watts` |

> Note: dcgm memory metrics (`dcgm_fi_dev_fb_used`, `dcgm_fi_dev_fb_total`) are in MiB. The memory percentage panel (`used/total * 100`) stays correct since both sides are in the same unit.

---

## New Query-Type Variables

These replace the existing Apple-only variable queries:

| Variable | Old query | New query |
|----------|-----------|-----------|
| `cluster` | `label_values(apple_gpu_utilization_percent, cluster)` | `label_values(up, cluster)` |
| `node` | `label_values(apple_gpu_utilization_percent, instance)` | `label_values(up{cluster=~"$cluster"}, node_id)` |
| `pool` (NEW) | — | `label_values(up{cluster=~"$cluster"}, pool)` |
| `gpu_provider` (NEW) | — | `label_values(up{cluster=~"$cluster"}, gpu_provider)` |
| `provider` (NEW) | — | `label_values(llm_model_loaded{cluster=~"$cluster"}, provider)` |

---

## Panel PromQL Changes

### fleet-overview.json

| Panel | Old expression | New expression |
|-------|---------------|----------------|
| Nodes Online | `count(up{job="apple-silicon-exporter"} == 1) / count(up{job="apple-silicon-exporter"})` | `count(up{cluster=~"$cluster"} == 1) / count(up{cluster=~"$cluster"})` |
| Avg GPU Utilization | `avg(apple_gpu_utilization_percent)` | `avg({__name__=~"$gpu_util_metric"})` |
| GPU Utilization by Node | `apple_gpu_utilization_percent` | `{__name__=~"$gpu_util_metric"}` |
| Total Cluster Power | `sum(apple_system_power_watts)` | `sum({__name__=~"$power_metric"})` |
| Node Table — GPU% | `apple_gpu_utilization_percent` | `{__name__=~"$gpu_util_metric"}` |
| Node Table — Mem% | `(apple_gpu_memory_used_bytes / apple_gpu_memory_total_bytes) * 100` | `({__name__=~"$gpu_mem_used_metric"} / {__name__=~"$gpu_mem_total_metric"}) * 100` |
| Node Table — Power | `apple_system_power_watts` | `{__name__=~"$power_metric"}` |
| Node Table — tok/s | `sum(llm_tokens_per_second) by (instance)` | `sum(llm_tokens_per_second) by (node_id)` |

### node-deep-dive.json

All panels: `{instance=~"$node"}` → `{node_id=~"$node"}`

| Panel | Old expression | New expression |
|-------|---------------|----------------|
| GPU Utilization (gauge) | `apple_gpu_utilization_percent{instance=~"$node"}` | `{__name__=~"$gpu_util_metric", node_id=~"$node"}` |
| GPU Memory (gauge) | `(apple_gpu_memory_used_bytes{instance=~"$node"} / apple_gpu_memory_total_bytes{instance=~"$node"}) * 100` | `({__name__=~"$gpu_mem_used_metric", node_id=~"$node"} / {__name__=~"$gpu_mem_total_metric", node_id=~"$node"}) * 100` |
| GPU Temperature | `apple_gpu_temperature_celsius{instance=~"$node"}` | `{__name__=~"$gpu_temp_metric", node_id=~"$node"}` |
| System Power | `apple_system_power_watts{instance=~"$node"}` | `{__name__=~"$power_metric", node_id=~"$node"}` |
| GPU Util (timeseries) | `apple_gpu_utilization_percent{instance=~"$node"}` | `{__name__=~"$gpu_util_metric", node_id=~"$node"}` |
| GPU Mem used (timeseries) | `apple_gpu_memory_used_bytes{instance=~"$node"}` | `{__name__=~"$gpu_mem_used_metric", node_id=~"$node"}` |
| GPU Mem total (timeseries) | `apple_gpu_memory_total_bytes{instance=~"$node"}` | `{__name__=~"$gpu_mem_total_metric", node_id=~"$node"}` |
| GPU Temp (timeseries) | `apple_gpu_temperature_celsius{instance=~"$node"}` | `{__name__=~"$gpu_temp_metric", node_id=~"$node"}` |
| Inference Latency | `...{instance=~"$node"}...` | `...{node_id=~"$node"}...` (same pattern, 3 targets) |
| Tokens/sec | `llm_tokens_per_second{instance=~"$node"}` | `llm_tokens_per_second{node_id=~"$node"}` |
| Models Table (all 5) | `...{instance=~"$node"}...` | `...{node_id=~"$node"}...` |

### quality-monitor.json

Add `provider=~"$provider"` to every panel expression that filters on `model=~"$model"`:
- All 11 panels: add `, provider=~"$provider"` inside the existing `{model=~"$model"}` selectors.

---

## Task 1: Alert rule fixes

**Files:**
- Modify: `apple-silicon-monitoring/alerts/apple-silicon-hardware.yaml`
- Modify: `apple-silicon-monitoring/alerts/llm-inference.yaml`

- [ ] **Step 1.1: Add `node_id` annotation + `HardwareExporterDown` alert to `apple-silicon-hardware.yaml`**

Read the file. Make these changes:

**a) Add `node_id: "{{ $labels.node_id }}"` to the `annotations:` block of every alert.** For example, `GPUHighUtilization` becomes:
```yaml
        annotations:
          summary: "GPU utilization sustained above 95% on {{ $labels.instance }}"
          node_id: "{{ $labels.node_id }}"
          description: |
            ...
```
Apply to: GPUHighUtilization, GPUMemoryExhaustion, GPUMemoryLeak, ThermalThrottlingActive, CriticalThermalPressure, SustainedThermalPressure, GPUTemperatureHigh, HighPowerConsumption, PowerSpike, AppleSiliconExporterDown, AppleSiliconScrapeSlow, CollectorFailure, ANEHighUtilization.

**b) Add the new `HardwareExporterDown` alert at the end of the `rules:` list (after ANEHighUtilization), before the closing of the group:**

```yaml
      - alert: HardwareExporterDown
        expr: up{cluster=~".+", job=~"hw-exporter"} == 0
        for: 2m
        labels:
          severity: critical
          team: infrastructure
          category: exporter
        annotations:
          summary: "Hardware exporter down on {{ $labels.node_id }} ({{ $labels.gpu_provider }})"
          node_id: "{{ $labels.node_id }}"
          description: |
            The hardware metrics exporter is not responding on {{ $labels.node_id }}.
            Hardware metrics (GPU, power, thermal) are not being collected.
            GPU provider: {{ $labels.gpu_provider }}
          runbook_url: "https://wiki.company.internal/runbooks/exporter-down"
```

- [ ] **Step 1.2: Add `node_id` annotation to every alert in `llm-inference.yaml`**

Read the file. Add `node_id: "{{ $labels.node_id }}"` to the `annotations:` block of every alert (same pattern as Step 1.1a). Apply to all 13 alerts: LLMModelNotLoaded, LLMInferenceStalled, LLMHighLatencyP99, LLMHighLatencyP50, LLMPromptLatencyHigh, LLMThroughputDegraded, LLMThroughputDrop, LLMQueueBacklog, LLMQueueCritical, LLMKVCacheFull, LLMErrorRateHigh, LLMErrorSpike, LLMOOMErrors, LLMMetalCrashes, LLMHealthScoreLow, LLMHealthScoreCritical.

- [ ] **Step 1.3: Validate alert YAML syntax**

```bash
python3 -c "
import yaml
for f in ['apple-silicon-monitoring/alerts/apple-silicon-hardware.yaml',
          'apple-silicon-monitoring/alerts/llm-inference.yaml']:
    with open(f) as fh:
        data = yaml.safe_load(fh)
    rules = data['groups'][0]['rules']
    for r in rules:
        assert 'node_id' in r['annotations'], f\"{r['alert']} missing node_id annotation\"
    print(f'{f}: {len(rules)} rules OK')
"
```

Expected output:
```
apple-silicon-monitoring/alerts/apple-silicon-hardware.yaml: 14 rules OK
apple-silicon-monitoring/alerts/llm-inference.yaml: 16 rules OK
```

- [ ] **Step 1.4: Commit alert fixes**

```bash
git add apple-silicon-monitoring/alerts/
git commit -m "fix(alerts): add node_id annotation, add HardwareExporterDown for all hardware"
```

---

## Task 2: Dashboard patch script + tests

**Files:**
- Create: `apple-silicon-monitoring/scripts/patch_dashboards.py`
- Create: `apple-silicon-monitoring/tests/test_dashboard_patches.py`

- [ ] **Step 2.1: Create `apple-silicon-monitoring/scripts/__init__.py`**

```bash
mkdir -p apple-silicon-monitoring/scripts
touch apple-silicon-monitoring/scripts/__init__.py
mkdir -p apple-silicon-monitoring/tests
touch apple-silicon-monitoring/tests/__init__.py
```

- [ ] **Step 2.2: Write the failing tests first**

Create `apple-silicon-monitoring/tests/test_dashboard_patches.py`:

```python
# apple-silicon-monitoring/tests/test_dashboard_patches.py
import json
import sys
import os
import copy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import patch_dashboards as pd

DASHBOARDS_DIR = os.path.join(os.path.dirname(__file__), '..', 'dashboards')


def load(name):
    with open(os.path.join(DASHBOARDS_DIR, name)) as f:
        return json.load(f)


def var_names(dash):
    return [v["name"] for v in dash.get("templating", {}).get("list", [])]


def var_query(dash, name):
    for v in dash["templating"]["list"]:
        if v["name"] == name:
            q = v.get("query", "")
            return q if isinstance(q, str) else q.get("query", "")
    raise KeyError(name)


def all_exprs(dash):
    exprs = []
    for p in dash.get("panels", []):
        for t in p.get("targets", []):
            if "expr" in t:
                exprs.append(t["expr"])
    return exprs


# --- fleet-overview ---

class TestFleetOverview:
    def setup_method(self):
        self.dash = load("fleet-overview.json")

    def test_cluster_var_uses_up_metric(self):
        q = var_query(self.dash, "cluster")
        assert "apple_gpu_utilization_percent" not in q
        assert "label_values(up" in q

    def test_node_var_exists_and_uses_node_id(self):
        assert "node" in var_names(self.dash)
        q = var_query(self.dash, "node")
        assert "node_id" in q
        assert "instance" not in q

    def test_pool_var_exists(self):
        assert "pool" in var_names(self.dash)

    def test_gpu_provider_var_exists(self):
        assert "gpu_provider" in var_names(self.dash)

    def test_provider_var_exists(self):
        assert "provider" in var_names(self.dash)

    def test_gpu_util_metric_var_exists(self):
        assert "gpu_util_metric" in var_names(self.dash)

    def test_gpu_mem_used_metric_var_exists(self):
        assert "gpu_mem_used_metric" in var_names(self.dash)

    def test_gpu_mem_total_metric_var_exists(self):
        assert "gpu_mem_total_metric" in var_names(self.dash)

    def test_power_metric_var_exists(self):
        assert "power_metric" in var_names(self.dash)

    def test_nodes_online_uses_cluster_not_job(self):
        exprs = all_exprs(self.dash)
        for e in exprs:
            assert 'job="apple-silicon-exporter"' not in e, f"Found apple-specific job selector: {e}"

    def test_gpu_util_panel_uses_variable(self):
        exprs = all_exprs(self.dash)
        assert any("$gpu_util_metric" in e for e in exprs)

    def test_power_panel_uses_variable(self):
        exprs = all_exprs(self.dash)
        assert any("$power_metric" in e for e in exprs)

    def test_node_table_uses_node_id_not_instance(self):
        exprs = all_exprs(self.dash)
        for e in exprs:
            if "llm_tokens_per_second" in e:
                assert "by (node_id)" in e, f"tok/s should group by node_id: {e}"

    def test_no_hardcoded_apple_hardware_metrics(self):
        apple_metrics = [
            "apple_gpu_utilization_percent",
            "apple_gpu_memory_used_bytes",
            "apple_gpu_memory_total_bytes",
            "apple_system_power_watts",
        ]
        exprs = all_exprs(self.dash)
        for e in exprs:
            for m in apple_metrics:
                assert m not in e, f"Hardcoded Apple metric {m!r} found in: {e}"


# --- node-deep-dive ---

class TestNodeDeepDive:
    def setup_method(self):
        self.dash = load("node-deep-dive.json")

    def test_cluster_var_exists(self):
        assert "cluster" in var_names(self.dash)

    def test_node_var_uses_node_id_not_instance(self):
        q = var_query(self.dash, "node")
        assert "node_id" in q
        assert "instance" not in q
        assert "apple_gpu_utilization_percent" not in q

    def test_gpu_util_metric_var_exists(self):
        assert "gpu_util_metric" in var_names(self.dash)

    def test_gpu_mem_used_metric_var_exists(self):
        assert "gpu_mem_used_metric" in var_names(self.dash)

    def test_gpu_mem_total_metric_var_exists(self):
        assert "gpu_mem_total_metric" in var_names(self.dash)

    def test_gpu_temp_metric_var_exists(self):
        assert "gpu_temp_metric" in var_names(self.dash)

    def test_power_metric_var_exists(self):
        assert "power_metric" in var_names(self.dash)

    def test_no_instance_filter_in_exprs(self):
        exprs = all_exprs(self.dash)
        for e in exprs:
            assert "instance=~" not in e, f"Stale instance filter in: {e}"

    def test_no_hardcoded_apple_metrics(self):
        apple_metrics = [
            "apple_gpu_utilization_percent",
            "apple_gpu_memory_used_bytes",
            "apple_gpu_memory_total_bytes",
            "apple_gpu_temperature_celsius",
            "apple_system_power_watts",
        ]
        exprs = all_exprs(self.dash)
        for e in exprs:
            for m in apple_metrics:
                assert m not in e, f"Hardcoded Apple metric {m!r} in: {e}"

    def test_hardware_panels_use_metric_variables(self):
        exprs = all_exprs(self.dash)
        assert any("$gpu_util_metric" in e for e in exprs)
        assert any("$power_metric" in e for e in exprs)
        assert any("$gpu_temp_metric" in e for e in exprs)


# --- quality-monitor ---

class TestQualityMonitor:
    def setup_method(self):
        self.dash = load("quality-monitor.json")

    def test_provider_var_exists(self):
        assert "provider" in var_names(self.dash)

    def test_provider_filter_in_all_model_exprs(self):
        exprs = all_exprs(self.dash)
        for e in exprs:
            if 'model=~"$model"' in e:
                assert 'provider=~"$provider"' in e, (
                    f"Missing provider filter in: {e}"
                )
```

- [ ] **Step 2.3: Run tests — confirm they fail**

```bash
cd /home/dk/Documents/git/hydra
python -m pytest apple-silicon-monitoring/tests/test_dashboard_patches.py -v 2>&1 | tail -10
```

Expected: multiple FAILED (patch_dashboards module not found, then test assertions failing).

- [ ] **Step 2.4: Create `apple-silicon-monitoring/scripts/patch_dashboards.py`**

```python
#!/usr/bin/env python3
# apple-silicon-monitoring/scripts/patch_dashboards.py
# Usage: python patch_dashboards.py
# Applies all multi-hardware patches to the three Grafana dashboard JSON files.
# Idempotent: safe to run multiple times.

import json
import re
from pathlib import Path

DASHBOARDS = Path(__file__).parent.parent / "dashboards"

# ---------------------------------------------------------------------------
# Variable builders
# ---------------------------------------------------------------------------

def _query_var(name, label, query, include_all=False, multi=False, all_value=".*"):
    v = {
        "name": name,
        "type": "query",
        "datasource": {"type": "prometheus", "uid": "PBFA97CFB590B2093"},
        "definition": query,
        "query": {"query": query, "refId": "StandardVariableQuery"},
        "refresh": 2,
        "current": {},
        "label": label,
        "includeAll": include_all,
        "multi": multi,
        "hide": 0,
        "sort": 1,
        "skipUrlSync": False,
    }
    if include_all:
        v["allValue"] = all_value
        v["current"] = {"selected": True, "text": "All", "value": "$__all"}
    return v


def _custom_var(name, label, options_str, default=None):
    """Custom dropdown variable with hardcoded comma-separated options."""
    options = options_str.split(",")
    current_text = default or options[0]
    return {
        "name": name,
        "type": "custom",
        "label": label,
        "query": options_str,
        "current": {"selected": True, "text": current_text, "value": current_text},
        "options": [
            {"selected": (o == current_text), "text": o, "value": o}
            for o in options
        ],
        "hide": 0,
        "includeAll": False,
        "multi": False,
        "refresh": 0,
        "skipUrlSync": False,
    }


def _upsert_var(var_list, new_var):
    """Insert new_var if name not present; update if present."""
    for i, v in enumerate(var_list):
        if v["name"] == new_var["name"]:
            var_list[i] = new_var
            return
    var_list.append(new_var)


# ---------------------------------------------------------------------------
# Expression transformers
# ---------------------------------------------------------------------------

def _replace_expr(targets, old_fragment, new_fragment):
    """Replace old_fragment with new_fragment in all target exprs."""
    for t in targets:
        if "expr" in t and old_fragment in t["expr"]:
            t["expr"] = t["expr"].replace(old_fragment, new_fragment)


def _replace_expr_exact(targets, old_expr, new_expr):
    """Replace exact expression match."""
    for t in targets:
        if t.get("expr", "").strip() == old_expr.strip():
            t["expr"] = new_expr


def _all_targets(dash):
    for panel in dash.get("panels", []):
        yield from panel.get("targets", [])


# ---------------------------------------------------------------------------
# fleet-overview patches
# ---------------------------------------------------------------------------

def patch_fleet_overview(dash):
    vl = dash["templating"]["list"]

    # Fix existing cluster variable
    for v in vl:
        if v["name"] == "cluster":
            new_q = "label_values(up, cluster)"
            v["definition"] = new_q
            v["query"] = {"query": new_q, "refId": "StandardVariableQuery"}

    # Add/update new variables
    _upsert_var(vl, _query_var(
        "node", "Node",
        'label_values(up{cluster=~"$cluster"}, node_id)',
        include_all=False, multi=False,
    ))
    _upsert_var(vl, _query_var(
        "pool", "Pool",
        'label_values(up{cluster=~"$cluster"}, pool)',
        include_all=True, multi=True,
    ))
    _upsert_var(vl, _query_var(
        "gpu_provider", "GPU Provider",
        'label_values(up{cluster=~"$cluster"}, gpu_provider)',
        include_all=True, multi=True,
    ))
    _upsert_var(vl, _query_var(
        "provider", "LLM Provider",
        'label_values(llm_model_loaded{cluster=~"$cluster"}, provider)',
        include_all=True, multi=True,
    ))
    _upsert_var(vl, _custom_var(
        "gpu_util_metric", "GPU Util Metric",
        "apple_gpu_utilization_percent,dcgm_fi_dev_gpu_util,amdgpu_utilization_percent",
    ))
    _upsert_var(vl, _custom_var(
        "gpu_mem_used_metric", "GPU Mem Used",
        "apple_gpu_memory_used_bytes,dcgm_fi_dev_fb_used,amdgpu_vram_used_bytes",
    ))
    _upsert_var(vl, _custom_var(
        "gpu_mem_total_metric", "GPU Mem Total",
        "apple_gpu_memory_total_bytes,dcgm_fi_dev_fb_total,amdgpu_vram_total_bytes",
    ))
    _upsert_var(vl, _custom_var(
        "power_metric", "Power Metric",
        "apple_system_power_watts,dcgm_fi_dev_power_usage,amdgpu_power_avg",
    ))

    # Fix panel expressions
    for t in _all_targets(dash):
        expr = t.get("expr", "")
        if not expr:
            continue

        # Nodes Online
        if 'job="apple-silicon-exporter"' in expr:
            t["expr"] = expr.replace(
                'up{job="apple-silicon-exporter"} == 1',
                'up{cluster=~"$cluster"} == 1'
            ).replace(
                'up{job="apple-silicon-exporter"}',
                'up{cluster=~"$cluster"}'
            )

        # Avg GPU Utilization stat
        elif expr.strip() == "avg(apple_gpu_utilization_percent)":
            t["expr"] = 'avg({__name__=~"$gpu_util_metric"})'

        # GPU heatmap / timeseries by node
        elif expr.strip() == "apple_gpu_utilization_percent":
            t["expr"] = '{__name__=~"$gpu_util_metric"}'

        # Total cluster power
        elif expr.strip() == "sum(apple_system_power_watts)":
            t["expr"] = 'sum({__name__=~"$power_metric"})'

        # Node table — GPU%
        elif expr.strip() == "apple_gpu_utilization_percent" and not expr.strip().startswith("avg"):
            t["expr"] = '{__name__=~"$gpu_util_metric"}'

        # Node table — Mem%
        elif "apple_gpu_memory_used_bytes / apple_gpu_memory_total_bytes" in expr:
            t["expr"] = '({__name__=~"$gpu_mem_used_metric"} / {__name__=~"$gpu_mem_total_metric"}) * 100'

        # Node table — Power
        elif expr.strip() == "apple_system_power_watts":
            t["expr"] = '{__name__=~"$power_metric"}'

        # Node table — tok/s group by instance → node_id
        elif "llm_tokens_per_second" in expr and "by (instance)" in expr:
            t["expr"] = expr.replace("by (instance)", "by (node_id)")

    return dash


# ---------------------------------------------------------------------------
# node-deep-dive patches
# ---------------------------------------------------------------------------

def patch_node_deep_dive(dash):
    vl = dash["templating"]["list"]

    # Add cluster variable before node (node depends on cluster)
    _upsert_var(vl, _query_var(
        "cluster", "Cluster",
        "label_values(up, cluster)",
        include_all=True, multi=False,
    ))

    # Fix node variable
    for v in vl:
        if v["name"] == "node":
            new_q = 'label_values(up{cluster=~"$cluster"}, node_id)'
            v["definition"] = new_q
            v["query"] = {"query": new_q, "refId": "StandardVariableQuery"}

    # Add hardware-abstracted metric variables
    _upsert_var(vl, _custom_var(
        "gpu_util_metric", "GPU Util Metric",
        "apple_gpu_utilization_percent,dcgm_fi_dev_gpu_util,amdgpu_utilization_percent",
    ))
    _upsert_var(vl, _custom_var(
        "gpu_mem_used_metric", "GPU Mem Used",
        "apple_gpu_memory_used_bytes,dcgm_fi_dev_fb_used,amdgpu_vram_used_bytes",
    ))
    _upsert_var(vl, _custom_var(
        "gpu_mem_total_metric", "GPU Mem Total",
        "apple_gpu_memory_total_bytes,dcgm_fi_dev_fb_total,amdgpu_vram_total_bytes",
    ))
    _upsert_var(vl, _custom_var(
        "gpu_temp_metric", "GPU Temp Metric",
        "apple_gpu_temperature_celsius,dcgm_fi_dev_gpu_temp,amdgpu_temp_input",
    ))
    _upsert_var(vl, _custom_var(
        "power_metric", "Power Metric",
        "apple_system_power_watts,dcgm_fi_dev_power_usage,amdgpu_power_avg",
    ))

    # Fix panel expressions: instance → node_id, apple_* → $variable
    REWRITES = [
        # (old_metric, new_expr_template)  — {FILTER} is replaced with node_id=~"$node"
        ("apple_gpu_utilization_percent",
         '{__name__=~"$gpu_util_metric", node_id=~"$node"}'),
        ("apple_gpu_temperature_celsius",
         '{__name__=~"$gpu_temp_metric", node_id=~"$node"}'),
        ("apple_system_power_watts",
         '{__name__=~"$power_metric", node_id=~"$node"}'),
    ]

    for t in _all_targets(dash):
        expr = t.get("expr", "")
        if not expr:
            continue

        # Replace instance filter with node_id throughout
        expr = expr.replace('instance=~"$node"', 'node_id=~"$node"')

        # Memory ratio gauge/timeseries
        if "apple_gpu_memory_used_bytes" in expr and "apple_gpu_memory_total_bytes" in expr:
            if "/" in expr:
                # Ratio panel
                expr = '({__name__=~"$gpu_mem_used_metric", node_id=~"$node"} / {__name__=~"$gpu_mem_total_metric", node_id=~"$node"}) * 100'
            elif "apple_gpu_memory_used_bytes" in expr:
                expr = '{__name__=~"$gpu_mem_used_metric", node_id=~"$node"}'
        elif "apple_gpu_memory_total_bytes" in expr:
            expr = '{__name__=~"$gpu_mem_total_metric", node_id=~"$node"}'
        elif "apple_gpu_memory_used_bytes" in expr:
            expr = '{__name__=~"$gpu_mem_used_metric", node_id=~"$node"}'
        else:
            # Apply simple metric rewrites
            for old_metric, new_expr in REWRITES:
                if old_metric in expr:
                    expr = new_expr
                    break

        t["expr"] = expr

    return dash


# ---------------------------------------------------------------------------
# quality-monitor patches
# ---------------------------------------------------------------------------

def patch_quality_monitor(dash):
    vl = dash["templating"]["list"]

    _upsert_var(vl, _query_var(
        "provider", "Provider",
        'label_values(llm_model_loaded, provider)',
        include_all=True, multi=True,
    ))

    # Add provider=~"$provider" filter to every expr that already has model=~"$model"
    for t in _all_targets(dash):
        expr = t.get("expr", "")
        if 'model=~"$model"' in expr and 'provider=~"$provider"' not in expr:
            t["expr"] = expr.replace(
                'model=~"$model"',
                'model=~"$model", provider=~"$provider"'
            )

    return dash


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PATCHES = {
    "fleet-overview.json": patch_fleet_overview,
    "node-deep-dive.json": patch_node_deep_dive,
    "quality-monitor.json": patch_quality_monitor,
}


def main():
    for filename, patch_fn in PATCHES.items():
        path = DASHBOARDS / filename
        with open(path) as f:
            dash = json.load(f)
        dash = patch_fn(dash)
        with open(path, "w") as f:
            json.dump(dash, f, indent=2)
            f.write("\n")
        print(f"Patched {filename}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.5: Run tests — confirm they still fail (patch not applied yet)**

```bash
cd /home/dk/Documents/git/hydra
python -m pytest apple-silicon-monitoring/tests/test_dashboard_patches.py -v 2>&1 | grep -E "PASSED|FAILED" | head -20
```

Expected: many FAILED.

- [ ] **Step 2.6: Run the patch script**

```bash
cd /home/dk/Documents/git/hydra
python apple-silicon-monitoring/scripts/patch_dashboards.py
```

Expected output:
```
Patched fleet-overview.json
Patched node-deep-dive.json
Patched quality-monitor.json
```

- [ ] **Step 2.7: Run tests — all must pass**

```bash
cd /home/dk/Documents/git/hydra
python -m pytest apple-silicon-monitoring/tests/test_dashboard_patches.py -v
```

Expected: all tests PASS. If any fail, debug the patch function and re-run the script, then re-run tests.

- [ ] **Step 2.8: Verify JSON is still valid after patching**

```bash
python3 -c "
import json
for f in ['apple-silicon-monitoring/dashboards/fleet-overview.json',
          'apple-silicon-monitoring/dashboards/node-deep-dive.json',
          'apple-silicon-monitoring/dashboards/quality-monitor.json']:
    with open(f) as fh:
        data = json.load(fh)
    panels = len(data.get('panels', []))
    vars_ = [v['name'] for v in data['templating']['list']]
    print(f'{f}: {panels} panels, vars={vars_}')
"
```

Expected: all 3 files parse cleanly and show reasonable panel counts and variable names.

- [ ] **Step 2.9: Commit**

```bash
git add apple-silicon-monitoring/dashboards/ \
        apple-silicon-monitoring/scripts/ \
        apple-silicon-monitoring/tests/
git commit -m "feat(dashboards): multi-hardware variables and hardware-abstracted metric expressions

- fleet-overview: add node/pool/gpu_provider/provider/gpu_util_metric/gpu_mem_*_metric/power_metric vars
  fix cluster var to use up metric; fix 7 panel exprs to use abstracted vars
- node-deep-dive: add cluster var; fix node var to use node_id; add 5 hardware-metric vars;
  rewrite all instance= filters to node_id=; rewrite all apple_* exprs to use vars
- quality-monitor: add provider var; add provider filter to all 11 panel exprs
- patch_dashboards.py: idempotent script that applies all mutations
- test_dashboard_patches.py: 25 assertions covering all mutations"
```

---

## Task 3: Post-patch cleanup

**Files:**
- Modify: `apple-silicon-monitoring/scripts/patch_dashboards.py` (make safe to re-run)

- [ ] **Step 3.1: Run patch script a second time (idempotency check)**

```bash
cd /home/dk/Documents/git/hydra
python apple-silicon-monitoring/scripts/patch_dashboards.py
python -m pytest apple-silicon-monitoring/tests/test_dashboard_patches.py -v 2>&1 | tail -5
```

Expected: all tests still pass after second run. If any fail, the patch is not idempotent — fix the relevant `_upsert_var` or expression-replacement logic so it detects already-applied changes.

- [ ] **Step 3.2: Check git diff to confirm no unintended changes**

```bash
git diff --stat apple-silicon-monitoring/dashboards/
```

Should show 0 changed lines (second run produced identical output). If lines changed, the patch has non-deterministic behaviour — fix before proceeding.

- [ ] **Step 3.3: Final commit if idempotency fix was needed**

```bash
# Only if Step 3.1 required changes:
git add apple-silicon-monitoring/scripts/patch_dashboards.py
git commit -m "fix(dashboards): ensure patch_dashboards.py is fully idempotent"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement (§8 & §9) | Task |
|----------------------------|------|
| `$cluster` var uses `label_values(up, cluster)` | Task 2 |
| `$node` var uses `label_values(up{...}, node_id)` | Task 2 |
| `$pool` var added | Task 2 |
| `$gpu_provider` var added | Task 2 |
| `$provider` var added | Task 2 |
| `$gpu_util_metric` / `$gpu_mem_*` / `$power_metric` / `$gpu_temp_metric` custom vars | Task 2 |
| Panels use `{__name__=~"$gpu_util_metric"}` pattern | Task 2 |
| `up{job="apple-silicon-exporter"}` → `up{cluster=~"$cluster"}` | Task 2 |
| `instance=~"$node"` → `node_id=~"$node"` throughout node-deep-dive | Task 2 |
| `provider=~"$provider"` filter added to quality-monitor panels | Task 2 |
| `node_id` annotation added to all alerts | Task 1 |
| `HardwareExporterDown` alert added | Task 1 |
| Alert YAML syntax validation | Task 1 |
| Dashboard JSON idempotency | Task 3 |

**No spec gaps found.**

**Deferred (per spec §12 v2 items):**
- Grafana provisioning automation (dashboards are imported manually in v1)
- Apple-only panels (`apple_thermal_*`, `apple_ane_*`) show "No Data" on NVIDIA/CPU nodes — acceptable for v1; conditional panel visibility deferred to v2
