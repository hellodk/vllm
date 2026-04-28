# apple-silicon-monitoring/tests/test_dashboard_patches.py
import json
import sys
import os
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

    def test_cluster_var_before_node_var(self):
        names = var_names(self.dash)
        assert "cluster" in names and "node" in names
        assert names.index("cluster") < names.index("node"), (
            f"cluster (pos {names.index('cluster')}) must come before "
            f"node (pos {names.index('node')}) for Grafana to resolve $cluster in $node query"
        )


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
