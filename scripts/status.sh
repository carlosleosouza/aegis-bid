#!/usr/bin/env bash
# Visão geral do ambiente, com o MAPA de onde cada peça roda.
# Não usa `set -e` de propósito: é uma ferramenta de diagnóstico, deve continuar e
# mostrar o que existe e o que ainda não existe, em vez de parar no primeiro erro.
set -u

PROD=k3d-aegis-prod
DR=k3d-aegis-dr

hdr() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
have_ctx() { kubectl config get-contexts -o name 2>/dev/null | grep -qx "$1"; }

cat <<EOF
MAPA (leia isto primeiro)
  • Argo CD roda SÓ no cluster  $PROD  (o "hub"), no namespace  argocd.
  • O cluster  $DR  não tem Argo CD próprio: ele é um DESTINO registrado no Argo do prod.
  • A aplicação (auction/bid/settlement) vive no namespace  aegis  de CADA cluster e só
    aparece a partir da Fase 2. Antes disso, "namespace aegis não existe" é o esperado.
  • Para olhar um cluster específico:  kubectl --context $PROD ...   ou   --context $DR ...
EOF

hdr "Clusters k3d"
k3d cluster list 2>&1

hdr "Borda (HAProxy, container Docker aegis-edge)"
docker ps -a --filter 'name=^aegis-edge$' --format '  {{.Names}}   {{.Status}}   {{.Ports}}' 2>&1 | grep . || echo "  (container aegis-edge não existe: rode make edge)"

for ctx in "$PROD" "$DR"; do
  hdr "[$ctx] nós"
  if have_ctx "$ctx"; then kubectl --context "$ctx" get nodes 2>&1; else echo "  (contexto $ctx não existe: cluster não foi criado?)"; fi
done

hdr "[$PROD] Argo CD: pods do namespace argocd"
kubectl --context "$PROD" -n argocd get pods 2>&1

hdr "[$PROD] Argo CD: Applications  (SYNC = igual ao Git?  HEALTH = saudável?)"
kubectl --context "$PROD" -n argocd get applications \
  -o custom-columns='NOME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,DESTINO:.spec.destination.server' 2>&1
echo "  (só 'aegis-root' = normal na Fase 0/1: platform/apps está vazio até você rodar 'make enable PHASE=2' e fazer commit + push)"

hdr "[$PROD] Argo CD: clusters registrados como destino"
kubectl --context "$PROD" -n argocd get secret -l argocd.argoproj.io/secret-type=cluster \
  -o custom-columns='SECRET:.metadata.name,ENV:.metadata.labels.aegis\.io/env' 2>&1

for ctx in "$PROD" "$DR"; do
  hdr "[$ctx] namespace aegis (a aplicação)"
  if ! have_ctx "$ctx"; then echo "  (contexto $ctx não existe)"; continue; fi
  if kubectl --context "$ctx" get namespace aegis >/dev/null 2>&1; then
    kubectl --context "$ctx" -n aegis get pods 2>&1
    kubectl --context "$ctx" -n aegis get rollouts 2>/dev/null || true
  else
    echo "  namespace aegis ainda não existe: normal até a Fase 2."
  fi
done
echo
