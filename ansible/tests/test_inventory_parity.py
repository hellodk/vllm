# ansible/tests/test_inventory_parity.py
# ANS-9: guard against drift between the dynamic source of truth (cluster.yml via
# inventory_plugin.py) and the static deployment inventory (inventories/llm/hosts.yml),
# and against LiteLLM port divergence.
import os

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_ANSIBLE = os.path.join(_HERE, "..")
_CLUSTER = os.path.join(_ANSIBLE, "cluster.yml")
_LLM_HOSTS = os.path.join(_ANSIBLE, "inventories", "llm", "hosts.yml")
_GROUP_VARS = os.path.join(_ANSIBLE, "group_vars", "all", "main.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _cluster_node_ids():
    c = _load(_CLUSTER)
    nodes = list(c.get("production_nodes", [])) + list(c.get("nodes", []))
    # Skip the &m4_defaults anchor holder (a list element with no id).
    return {n["id"] for n in nodes if "id" in n}


def _static_inventory_hosts():
    inv = _load(_LLM_HOSTS)
    hosts = set()
    for _group, gdef in (inv.get("all", {}).get("children", {}) or {}).items():
        for host in (gdef.get("hosts", {}) or {}):
            hosts.add(host)
    return hosts


def test_static_inventory_hosts_exist_in_cluster():
    """Every host in inventories/llm/hosts.yml must be a node in cluster.yml."""
    cluster_ids = _cluster_node_ids()
    missing = _static_inventory_hosts() - cluster_ids
    assert not missing, f"hosts in llm inventory but absent from cluster.yml: {sorted(missing)}"


def test_litellm_port_single_source_of_truth():
    """group_vars litellm_port and the engine catalog must agree (ANS-9)."""
    gv = _load(_GROUP_VARS)
    assert gv["litellm_port"] == 8080
    assert gv["llm_engine_catalog"]["litellm"]["port"] == 8080


def test_sglang_in_catalog_and_valid_providers():
    """SGLang must be discoverable (LLM-7)."""
    gv = _load(_GROUP_VARS)
    assert "sglang" in gv["llm_engine_catalog"]
    assert "sglang" in gv["valid_llm_providers"]
