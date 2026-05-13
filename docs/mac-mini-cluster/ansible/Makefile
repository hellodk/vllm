# ============================================================
# Hydra Pilot — Ansible Operations
# ============================================================
#
# Quick start:
#   make install    → install Ansible dependencies
#   make ping       → test connectivity to both Mac Minis
#   make deploy     → deploy entire cluster
#   make health     → verify everything is running
#
# See: ansible-operations-guide.md for full documentation
# ============================================================

.PHONY: install deploy health ping check logs

# ─── SETUP ───────────────────────────────────────────────────

install:                          ## Install Ansible Galaxy deps
	ansible-galaxy install -r requirements.yml

ping:                             ## Test SSH connectivity
	ansible all -m ping

check:                            ## Dry run (show what would change)
	ansible-playbook playbooks/site.yml --check --diff

inventory:                        ## Show cluster inventory
	ansible-inventory --graph --vars

# ─── DEPLOY ──────────────────────────────────────────────────

deploy:                           ## Full cluster deployment
	ansible-playbook playbooks/site.yml

deploy-gateway:                   ## Deploy gateway node only
	ansible-playbook playbooks/site.yml --limit gateway

deploy-inference:                 ## Deploy inference node only
	ansible-playbook playbooks/site.yml --limit inference

deploy-step:                      ## Deploy one phase at a time (interactive)
	@echo "Available tags: common, docker, vllm, redis, monitoring, gateway, validate"
	@read -p "Enter tag: " tag && ansible-playbook playbooks/site.yml --tags $$tag

# ─── HEALTH ──────────────────────────────────────────────────

health:                           ## Full health check
	ansible-playbook playbooks/health-check.yml

# ─── RESTART ─────────────────────────────────────────────────

restart:                          ## Restart all services
	ansible-playbook playbooks/rolling-restart.yml

restart-vllm:                     ## Restart vLLM-MLX inference
	ansible-playbook playbooks/rolling-restart.yml --tags vllm

restart-litellm:                  ## Restart LiteLLM gateway
	ansible-playbook playbooks/rolling-restart.yml --tags litellm

restart-monitoring:               ## Restart Prometheus/Grafana/Loki
	ansible-playbook playbooks/rolling-restart.yml --tags monitoring

# ─── IDE CONFIGS ─────────────────────────────────────────────

setup-devs:                       ## Generate IDE configs for developers
	ansible-playbook playbooks/setup-developers.yml

# ─── LOGS ────────────────────────────────────────────────────

logs-vllm:                        ## Tail vLLM-MLX logs
	ansible inference -m command -a "tail -50 /opt/hydra/logs/vllm-mlx.log"

logs-litellm:                     ## Tail LiteLLM logs
	ansible gateway -m command -a "tail -50 /opt/hydra/logs/litellm.log"

logs-errors:                      ## Show recent errors across cluster
	ansible all -m shell -a "grep -i 'error\|exception' /opt/hydra/logs/*.log | tail -20"

# ─── HELP ────────────────────────────────────────────────────

help:                             ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
