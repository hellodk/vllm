#!/usr/bin/env python3
# apple-silicon-monitoring/scripts/patch_dashboards.py
# Usage: python patch_dashboards.py   (from project root or any directory)
# Applies all multi-hardware patches to the three Grafana dashboard JSON files.
# Idempotent: safe to run multiple times.

import json
from pathlib import Path

DASHBOARDS = Path(__file__).parent.parent / "dashboards"


# ---------------------------------------------------------------------------
# Variable builders
# ---------------------------------------------------------------------------

def _query_var(name, label, query, include_all=False, multi=False):
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
        v["allValue"] = ".*"
        v["current"] = {"selected": True, "text": "All", "value": "$__all"}
    return v


def _custom_var(name, label, options_str):
    options = options_str.split(",")
    default = options[0]
    return {
        "name": name,
        "type": "custom",
        "label": label,
        "query": options_str,
        "current": {"selected": True, "text": default, "value": default},
        "options": [
            {"selected": (o == default), "text": o, "value": o}
            for o in options
        ],
        "hide": 0,
        "includeAll": False,
        "multi": False,
        "refresh": 0,
        "skipUrlSync": False,
    }


def _upsert_var(var_list, new_var):
    """Replace existing var with same name, or append if new."""
    for i, v in enumerate(var_list):
        if v["name"] == new_var["name"]:
            var_list[i] = new_var
            return
    var_list.append(new_var)


# ---------------------------------------------------------------------------
# fleet-overview patches
# ---------------------------------------------------------------------------

def patch_fleet_overview(dash):
    vl = dash["templating"]["list"]

    # Fix cluster variable to use hardware-agnostic metric
    for v in vl:
        if v["name"] == "cluster":
            new_q = "label_values(up, cluster)"
            v["definition"] = new_q
            v["query"] = {"query": new_q, "refId": "StandardVariableQuery"}

    # Add new variables
    _upsert_var(vl, _query_var(
        "node", "Node",
        'label_values(up{cluster=~"$cluster"}, node_id)',
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
    for panel in dash.get("panels", []):
        for t in panel.get("targets", []):
            expr = t.get("expr", "")
            if not expr:
                continue

            # Nodes Online — remove apple job selector
            if 'job="apple-silicon-exporter"' in expr:
                expr = expr.replace(
                    'up{job="apple-silicon-exporter"} == 1',
                    'up{cluster=~"$cluster"} == 1'
                ).replace(
                    'up{job="apple-silicon-exporter"}',
                    'up{cluster=~"$cluster"}'
                )

            # Memory percentage ratio
            elif ("apple_gpu_memory_used_bytes" in expr
                  and "apple_gpu_memory_total_bytes" in expr):
                expr = ('({__name__=~"$gpu_mem_used_metric"} / '
                        '{__name__=~"$gpu_mem_total_metric"}) * 100')

            # GPU utilization (avg stat or heatmap)
            elif expr.strip() in (
                "apple_gpu_utilization_percent",
                "avg(apple_gpu_utilization_percent)",
            ):
                if expr.strip().startswith("avg("):
                    expr = 'avg({__name__=~"$gpu_util_metric"})'
                else:
                    expr = '{__name__=~"$gpu_util_metric"}'

            # Power
            elif "apple_system_power_watts" in expr:
                if expr.strip().startswith("sum("):
                    expr = 'sum({__name__=~"$power_metric"})'
                else:
                    expr = '{__name__=~"$power_metric"}'

            # tok/s — ensure all groupings use node_id
            elif "llm_tokens_per_second" in expr:
                import re
                expr = re.sub(r'\bby\s*\(\s*\w+\s*\)', 'by (node_id)', expr)

            t["expr"] = expr

    return dash


# ---------------------------------------------------------------------------
# node-deep-dive patches
# ---------------------------------------------------------------------------

def patch_node_deep_dive(dash):
    vl = dash["templating"]["list"]

    # Add cluster variable (before node)
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

    # Fix all panel expressions
    for panel in dash.get("panels", []):
        for t in panel.get("targets", []):
            expr = t.get("expr", "")
            if not expr:
                continue

            # Replace instance filter with node_id
            expr = expr.replace('instance=~"$node"', 'node_id=~"$node"')

            # Memory ratio (must check before individual memory metrics)
            if ("apple_gpu_memory_used_bytes" in expr
                    and "apple_gpu_memory_total_bytes" in expr):
                expr = ('({__name__=~"$gpu_mem_used_metric", node_id=~"$node"} / '
                        '{__name__=~"$gpu_mem_total_metric", node_id=~"$node"}) * 100')
            elif "apple_gpu_memory_total_bytes" in expr:
                expr = '{__name__=~"$gpu_mem_total_metric", node_id=~"$node"}'
            elif "apple_gpu_memory_used_bytes" in expr:
                expr = '{__name__=~"$gpu_mem_used_metric", node_id=~"$node"}'
            elif "apple_gpu_utilization_percent" in expr:
                expr = '{__name__=~"$gpu_util_metric", node_id=~"$node"}'
            elif "apple_gpu_temperature_celsius" in expr:
                expr = '{__name__=~"$gpu_temp_metric", node_id=~"$node"}'
            elif "apple_system_power_watts" in expr:
                expr = '{__name__=~"$power_metric", node_id=~"$node"}'

            t["expr"] = expr

    return dash


# ---------------------------------------------------------------------------
# quality-monitor patches
# ---------------------------------------------------------------------------

def patch_quality_monitor(dash):
    vl = dash["templating"]["list"]

    _upsert_var(vl, _query_var(
        "provider", "Provider",
        "label_values(llm_model_loaded, provider)",
        include_all=True, multi=True,
    ))

    # Add provider filter to every expr that filters on model
    for panel in dash.get("panels", []):
        for t in panel.get("targets", []):
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
