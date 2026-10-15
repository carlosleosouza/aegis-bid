# AEGIS-Bid: ponto de entrada de TUDO. `make help` lista os alvos.
# Cada alvo é curto de propósito: leia o que ele faz antes de rodar (o guia de cada
# fase em docs/ explica o PORQUÊ).
SHELL := /bin/bash
PROD  := k3d-aegis-prod
DR    := k3d-aegis-dr
ARGOCD_VERSION ?= stable
VENV ?= .venv
# Usa o Python do .venv se ele existir (make venv); senão, o python3 do sistema.
PYTHON ?= $(if $(wildcard $(abspath $(VENV))/bin/python),$(abspath $(VENV))/bin/python,python3)
# Porta do HOST onde a borda (HAProxy) escuta. Troque se a 8080 estiver ocupada:
#   make edge EDGE_PORT=8090      (e use http://localhost:8090 nos curls dos guias)
EDGE_PORT ?= 8080
export EDGE_PORT

.DEFAULT_GOAL := help
.PHONY: help
help: ## Lista os alvos
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

# ---------------------------------------------------------------- fase 0
.PHONY: preflight configure network clusters argocd register root edge up down status
preflight: ## Confere docker/k3d/kubectl antes de começar
	@./scripts/preflight.sh

configure: ## Troca o placeholder pelo seu usuário GitHub: make configure OWNER=seu-usuario
	@./scripts/configure.sh "$(OWNER)"

network: ## Rede Docker compartilhada entre os clusters e a borda
	@docker network inspect aegis-net >/dev/null 2>&1 || docker network create aegis-net

clusters: network ## Cria os clusters k3d prod e dr
	k3d cluster create --config bootstrap/k3d-prod.yaml
	k3d cluster create --config bootstrap/k3d-dr.yaml
	@kubectl --context $(PROD) get nodes
	@kubectl --context $(DR) get nodes

argocd: ## Instala o Argo CD no cluster prod (hub)
	kubectl --context $(PROD) create namespace argocd --dry-run=client -o yaml | kubectl --context $(PROD) apply -f -
	kubectl --context $(PROD) apply -n argocd --server-side --force-conflicts \
	  -f https://raw.githubusercontent.com/argoproj/argo-cd/$(ARGOCD_VERSION)/manifests/install.yaml
	kubectl --context $(PROD) -n argocd wait --for=condition=available deployment --all --timeout=300s

register: ## Registra prod e dr no Argo CD
	./bootstrap/register-clusters.sh

root: ## Aplica a Application raiz (única coisa aplicada à mão)
	kubectl --context $(PROD) apply -f bootstrap/root-app.yaml

edge: network ## Sobe o HAProxy de borda em :$(EDGE_PORT) (padrão 8080; prod com failover para dr)
	-@docker rm -f aegis-edge >/dev/null 2>&1
	docker run -d --name aegis-edge --network aegis-net -p $(EDGE_PORT):8080 \
	  -v "$(CURDIR)/bootstrap/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" haproxy:lts-alpine

up: preflight clusters argocd register root edge ## Fase 0 completa: do zero ao Argo CD observando o Git
	@echo; echo "Pronto. Próximo: docs/fase-0-fundacao.md (verificação) e docs/fase-1-app-ci.md"

down: ## Destrói TUDO (clusters, borda, rede)
	-docker rm -f aegis-edge
	-k3d cluster delete aegis-prod aegis-dr
	-docker network rm aegis-net

status: ## Visão rápida: apps do Argo CD e pods
	@kubectl --context $(PROD) -n argocd get applications 2>/dev/null || true
	@echo; kubectl --context $(PROD) -n aegis get pods,rollouts 2>/dev/null || true

# ---------------------------------------------------------------- fases 1-8
.PHONY: dev dev-down images image-smoke images-load venv test lint slo-rules dashboard enable
dev: ## App SEM Kubernetes (docker compose): auction:8001 bid:8002 settlement:8003
	docker compose up --build -d
	@echo "teste rápido: veja docs/fase-1-app-ci.md"

dev-down: ## Derruba o docker compose
	docker compose down -v

images: ## Constrói as 3 imagens (docker build) com o nome que os manifests esperam
	./scripts/build-images.sh build

image-smoke: ## Sobe as imagens construídas e prova que funcionam (readyz, não-root, lance + idempotência)
	./scripts/build-images.sh smoke

images-load: ## Ponte local: carrega as imagens nos clusters k3d (sem GHCR/CI). Ver Fase 1, passo 4
	./scripts/build-images.sh load

venv: ## Cria o .venv com as dependências de teste/lint (rode uma vez antes de make test)
	python3 -m venv $(VENV)
	$(abspath $(VENV))/bin/pip install -q -r apps/requirements-dev.txt
	@echo "✔ $(VENV) pronto. Agora: make test"

test: ## Testes unitários (apps + tools). Se faltar dependência: make venv
	@$(PYTHON) -c 'import pytest, fakeredis, yaml' 2>/dev/null || { echo "✘ faltam dependências de teste (pytest, fakeredis, PyYAML). Rode: make venv"; exit 1; }
	cd apps && $(PYTHON) -m pytest -q -rs
	$(PYTHON) -m pytest -q tools/tests

lint: ## yamllint nos manifests (precisa do make venv)
	@$(PYTHON) -c 'import yamllint' 2>/dev/null || { echo "✘ yamllint ausente. Rode: make venv"; exit 1; }
	$(PYTHON) -m yamllint -c .yamllint.yml deploy platform bootstrap chaos .github

slo-rules: ## Regenera regras de SLO e dashboard a partir das especificações
	$(PYTHON) tools/slo_gen.py platform/observability/slos.yaml > platform/observability/slo-rules.generated.yaml
	$(PYTHON) tools/dashboard_gen.py > platform/observability/dashboard-saude-do-leilao.yaml
	@echo "regenerado. Revise com git diff e comite."

dashboard: slo-rules ## Alias de slo-rules

enable: ## Liga uma fase (só edita arquivos): make enable PHASE=3
	@./scripts/enable-phase.sh "$(PHASE)"

# ---------------------------------------------------------------- acesso
.PHONY: argocd-password argocd-ui prom-ui grafana-ui rollout-watch
argocd-password: ## Senha do admin do Argo CD
	@kubectl --context $(PROD) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

argocd-ui: ## UI do Argo CD em https://localhost:8443 (usuário: admin)
	kubectl --context $(PROD) -n argocd port-forward svc/argocd-server 8443:443

prom-ui: ## Prometheus em http://localhost:9090
	kubectl --context $(PROD) -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090

grafana-ui: ## Grafana em http://localhost:3000 (admin / aegis-lab)
	kubectl --context $(PROD) -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80

rollout-watch: ## Acompanha o canary do bid-svc (requer o plugin kubectl-argo-rollouts)
	kubectl --context $(PROD) argo rollouts get rollout bid-svc -n aegis --watch

# ---------------------------------------------------------------- fases 5-8
.PHONY: lightning chaos chaos-list postmortem gate failover restore-prod
lightning: ## Leilão Relâmpago (k6): make lightning PEAK=500
	k6 run -e BASE_URL=http://aegis.localhost:$(EDGE_PORT) -e PEAK_RPS=$(or $(PEAK),500) apps/loadgen/lightning.js

chaos-list: ## Lista experimentos de caos
	$(PYTHON) tools/chaos_director.py --list

chaos: ## Roda um experimento: make chaos ID=kill-bid-pod  (precisa de `make prom-ui` em outro terminal)
	$(PYTHON) tools/chaos_director.py --id "$(ID)"

postmortem: ## Gera o postmortem: make postmortem FILE=postmortems/data/<arquivo>.json
	$(PYTHON) tools/postmortem.py "$(FILE)"

gate: ## Error Budget Gate local (precisa de `make prom-ui`): make gate [FIX=1]
	$(PYTHON) tools/budget_gate.py $(if $(FIX),--is-fix,)

failover: ## Desastre simulado: derruba prod, promove dr, mede RTO/RPO
	./scripts/failover.sh

restore-prod: ## Religa o cluster prod parado pelo failover
	k3d cluster start aegis-prod
