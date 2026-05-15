#!/usr/bin/env python3
# ansible/inventory_plugin.py
# Usage: ansible-inventory -i inventory_plugin.py --list
import json
import os
import sys
import yaml

VALID_OS = {"macos", "linux"}
VALID_GPU_PROVIDERS = {"apple", "nvidia", "amd", "none"}
VALID_LLM_PROVIDERS = {"llamacpp", "ollama", "vllm-mlx", "litellm"}

_CLUSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cluster.yml")


def _load_cluster(path: str = _CLUSTER_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_inventory(cluster: dict) -> dict:
    cfg = cluster["cluster"]
    nodes = cluster["nodes"]

    inv: dict = {
        "_meta": {"hostvars": {}},
        "all": {"hosts": [], "children": []},
    }

    groups: dict[str, list[str]] = {}

    for node in nodes:
        if node.get("maintenance", False):
            continue

        nid = node["id"]

        # Validate enums
        if node["os"] not in VALID_OS:
            raise ValueError(f"os '{node['os']}' for node '{nid}' must be one of {VALID_OS}")
        if node["gpu_provider"] not in VALID_GPU_PROVIDERS:
            raise ValueError(
                f"gpu_provider '{node['gpu_provider']}' for node '{nid}' "
                f"must be one of {VALID_GPU_PROVIDERS}"
            )
        for ep in node.get("llm_endpoints", []):
            if ep["provider"] not in VALID_LLM_PROVIDERS:
                raise ValueError(
                    f"llm provider '{ep['provider']}' for node '{nid}' "
                    f"must be one of {VALID_LLM_PROVIDERS}"
                )

        # Resolve salt and jenkins config from cluster.yml
        salt_cfg = cluster.get("salt", {})
        salt_master = salt_cfg.get("master", {})
        salt_config = salt_cfg.get("config", {})
        jenkins_cfg = cluster.get("jenkins", {})

        # Host vars — ansible connection + hydra metadata
        inv["_meta"]["hostvars"][nid] = {
            # Ansible connection
            "ansible_host": node["ip"],
            "ansible_user": node["user"],
            "ansible_port": node.get("ssh_port", 22),
            "ansible_become": node.get("become", False),
            "ansible_become_method": node.get("become_method", "sudo"),
            # Hydra node identity
            "hydra_node_id": nid,
            "hydra_hostname": node["hostname"],
            "hydra_os": node["os"],
            "hydra_gpu_provider": node["gpu_provider"],
            "hydra_chip": node.get("chip", "unknown"),
            "hydra_ram_gb": node.get("ram_gb", 0),
            "hydra_vram_gb": node.get("vram_gb", 0),
            "hydra_pools": node.get("pools", []),
            "hydra_models_dir": node.get("models_dir", cfg.get("models_dir", "~/models")),
            "hydra_cpu_inference": node.get("cpu_inference", False),
            "hydra_metric_labels": node.get("metric_labels", {}),
            "hydra_llm_endpoints": node.get("llm_endpoints", []),
            # Cluster-wide vars
            "hydra_cluster_name": cfg["name"],
            "hydra_environment": cfg["environment"],
            "hydra_otel_gateway_primary": cfg["otel_gateway"]["primary"],
            "hydra_otel_gateway_secondary": cfg["otel_gateway"].get("secondary", ""),
            "hydra_versions": cfg.get("versions", {}),
            # Salt / Fleet Platform vars (per-node)
            "hydra_fleet_token": node.get("fleet_token") or "",
            "hydra_salt_master_ip": salt_master.get("ip", ""),
            "hydra_salt_fleet_api_url": salt_config.get("fleet_api_url", ""),
            "hydra_salt_sbom_schedule": salt_config.get("sbom_schedule_time", "02:00am"),
            "hydra_salt_sbom_timeout": salt_config.get("sbom_scan_timeout", 600),
            "hydra_salt_log_level": salt_config.get("log_level", "info"),
            # Jenkins iOS build agent vars (per-node)
            "hydra_ios_build_agent": node.get("ios_build_agent", False),
            "hydra_jenkins_secret": node.get("jenkins_secret") or "",
            "hydra_jenkins_host": jenkins_cfg.get("host", ""),
            "hydra_jenkins_agent_name": jenkins_cfg.get("agent_name", nid),
            "hydra_jenkins_user": jenkins_cfg.get("user", "dk"),
            "hydra_pulse_repo_dir": jenkins_cfg.get("pulse_repo_dir", "/Users/dk/pulse"),
        }

        inv["all"]["hosts"].append(nid)

        # Group by OS
        os_group = f"os_{node['os']}"
        groups.setdefault(os_group, []).append(nid)

        # Group by GPU provider
        hw_group = f"hw_{node['gpu_provider']}"
        groups.setdefault(hw_group, []).append(nid)

        # Group by pool membership
        for pool in node.get("pools", []):
            pool_group = f"pool_{pool}"
            groups.setdefault(pool_group, []).append(nid)

        # Group iOS build agents (macOS nodes with ios_build_agent: true)
        if node.get("ios_build_agent", False) and node["os"] == "macos":
            groups.setdefault("ios_agents", []).append(nid)

    for group, hosts in groups.items():
        inv[group] = {"hosts": hosts}
        inv["all"]["children"].append(group)

    # Add Salt Master as a separate host (control plane, not a Hydra inference node)
    salt_cfg = cluster.get("salt", {})
    salt_master = salt_cfg.get("master", {})
    salt_config = salt_cfg.get("config", {})
    if salt_master.get("ip"):
        mid = salt_master.get("id", "fleet-control")
        inv["_meta"]["hostvars"][mid] = {
            "ansible_host": salt_master["ip"],
            "ansible_user": salt_master.get("user", "dk"),
            "ansible_port": salt_master.get("ssh_port", 22),
            "ansible_become": salt_master.get("become", True),
            "ansible_become_method": salt_master.get("become_method", "sudo"),
            # Salt master identity vars
            "hydra_node_id": mid,
            "hydra_hostname": salt_master.get("hostname", mid),
            "hydra_os": salt_master.get("os", "linux"),
            "hydra_salt_master_os": salt_master.get("os", "linux"),
            "hydra_salt_auto_accept": salt_config.get("auto_accept_keys", True),
            "hydra_salt_log_level": salt_config.get("log_level", "info"),
            "hydra_salt_kri_app_dir": salt_master.get("kri_app_dir", "/home/dk/Documents/git/kri"),
            "hydra_salt_kri_venv_dir": salt_master.get("kri_venv_dir", "/home/dk/Documents/git/kri/.venv"),
            "hydra_salt_kri_user": salt_master.get("kri_user", "dk"),
        }
        inv["salt_master"] = {"hosts": [mid]}
        inv["all"]["children"].append("salt_master")

    return inv


def main():
    if "--list" in sys.argv:
        cluster = _load_cluster()
        print(json.dumps(build_inventory(cluster), indent=2))
    elif "--host" in sys.argv:
        print(json.dumps({}))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
