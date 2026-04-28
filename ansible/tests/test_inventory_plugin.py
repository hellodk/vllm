# ansible/tests/test_inventory_plugin.py
import json
import sys
import os
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import inventory_plugin as ip

SAMPLE_CLUSTER = {
    "cluster": {
        "name": "test-cluster",
        "environment": "test",
        "otel_gateway": {"primary": "10.0.0.1:4317", "secondary": ""},
        "prometheus_url": "http://10.0.0.1:9090",
        "models_dir": "~/models",
        "versions": {
            "otel_agent": "0.96.0",
            "dcgm_exporter": "3.3.6",
            "apple_silicon_exporter": "1.0.0",
            "node_exporter": "1.8.2",
            "amdgpu_exporter": "1.0.0",
        },
    },
    "nodes": [
        {
            "id": "node-apple",
            "hostname": "mac-m3",
            "ip": "192.168.1.10",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "macos",
            "gpu_provider": "apple",
            "chip": "m3",
            "ram_gb": 16,
            "models_dir": "/Users/dk/models",
            "pools": ["fast"],
            "maintenance": False,
            "metric_labels": {},
            "llm_endpoints": [
                {"provider": "ollama", "url": "http://127.0.0.1:11434/v1", "api_key": None}
            ],
        },
        {
            "id": "node-nvidia",
            "hostname": "linux-rtx",
            "ip": "192.168.1.20",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "linux",
            "gpu_provider": "nvidia",
            "chip": "i7-12700k",
            "ram_gb": 64,
            "vram_gb": 4,
            "models_dir": "/home/dk/models",
            "pools": ["fast"],
            "maintenance": False,
            "metric_labels": {},
            "llm_endpoints": [],
        },
        {
            "id": "node-maint",
            "hostname": "mac-m1",
            "ip": "192.168.1.30",
            "user": "dk",
            "ssh_port": 22,
            "become": True,
            "become_method": "sudo",
            "os": "macos",
            "gpu_provider": "apple",
            "chip": "m1",
            "ram_gb": 8,
            "models_dir": "/Users/dk/models",
            "pools": [],
            "maintenance": True,
            "metric_labels": {},
            "llm_endpoints": [],
        },
    ],
}


def test_maintenance_nodes_excluded():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "node-maint" not in inv["_meta"]["hostvars"]
    assert "node-maint" not in inv.get("all", {}).get("hosts", [])


def test_all_active_nodes_present():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hostvars = inv["_meta"]["hostvars"]
    assert "node-apple" in hostvars
    assert "node-nvidia" in hostvars


def test_host_connection_vars():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["ansible_host"] == "192.168.1.10"
    assert hv["ansible_user"] == "dk"
    assert hv["ansible_port"] == 22
    assert hv["ansible_become"] is True
    assert hv["ansible_become_method"] == "sudo"


def test_hydra_vars_injected():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["hydra_node_id"] == "node-apple"
    assert hv["hydra_os"] == "macos"
    assert hv["hydra_gpu_provider"] == "apple"
    assert hv["hydra_pools"] == ["fast"]
    assert hv["hydra_chip"] == "m3"


def test_group_by_os():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "os_macos" in inv
    assert "node-apple" in inv["os_macos"]["hosts"]
    assert "os_linux" in inv
    assert "node-nvidia" in inv["os_linux"]["hosts"]


def test_group_by_gpu_provider():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    assert "hw_apple" in inv
    assert "node-apple" in inv["hw_apple"]["hosts"]
    assert "hw_nvidia" in inv
    assert "node-nvidia" in inv["hw_nvidia"]["hosts"]


def test_invalid_gpu_provider_raises():
    bad = dict(SAMPLE_CLUSTER)
    bad["nodes"] = [dict(SAMPLE_CLUSTER["nodes"][0], gpu_provider="quantum")]
    with pytest.raises(ValueError, match="gpu_provider"):
        ip.build_inventory(bad)


def test_invalid_os_raises():
    bad = dict(SAMPLE_CLUSTER)
    bad["nodes"] = [dict(SAMPLE_CLUSTER["nodes"][0], os="windows")]
    with pytest.raises(ValueError, match="os"):
        ip.build_inventory(bad)


def test_cluster_vars_available():
    inv = ip.build_inventory(SAMPLE_CLUSTER)
    hv = inv["_meta"]["hostvars"]["node-apple"]
    assert hv["hydra_cluster_name"] == "test-cluster"
    assert hv["hydra_otel_gateway_primary"] == "10.0.0.1:4317"
