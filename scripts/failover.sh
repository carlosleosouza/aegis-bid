#!/usr/bin/env bash
# Simula a perda TOTAL do cluster primário e promove o DR, medindo RTO e (aprox.) RPO.
#   make failover        (com a stack da fase 8 no ar; rode `make lightning` em outro
#                         terminal para ver um RPO diferente de zero)
#
# Definições (para a entrevista):
#   RTO (Recovery Time Objective): quanto tempo o serviço fica fora do ar.
#   RPO (Recovery Point Objective): quanto DADO se perde (aqui: atraso da replicação
#       do Redis no instante da falha, em bytes de log de replicação).
set -euo pipefail
PROD=k3d-aegis-prod
DR=k3d-aegis-dr
EDGE="${EDGE_URL:-http://localhost:8080}"
code() { curl -s -o /dev/null -m 2 -w '%{http_code}' -H 'Host: aegis.localhost' "$EDGE/api/auctions" || true; }
rcli() { kubectl --context "$1" -n aegis exec deploy/redis -- redis-cli "${@:2}" | tr -d '\r'; }

echo "== 1. Antes da falha =="
echo "borda responde: $(code)"
prod_off=$(rcli $PROD info replication | awk -F: '/^master_repl_offset/{print $2}')
dr_off=$(rcli $DR info replication | awk -F: '/^slave_repl_offset/{print $2}')
echo "offset de replicação  primário=$prod_off  dr=$dr_off"
echo "RPO aproximado (atraso em bytes): $((prod_off - dr_off))"

echo "== 2. DESASTRE: parando o cluster primário inteiro (inclui o Argo CD hub) =="
t_fail=$(date +%s)
k3d cluster stop aegis-prod

echo "== 3. Promovendo o Redis do DR a primário =="
rcli $DR replicaof no one
echo "role agora: $(rcli $DR role | head -1)"

echo "== 4. Ligando os serviços do DR (saindo de 'pilot light') =="
kubectl --context $DR -n aegis scale deploy/auction-svc --replicas=2
kubectl --context $DR -n aegis scale deploy/settlement-svc --replicas=2
kubectl --context $DR -n aegis scale rollout/bid-svc --replicas=4

echo "== 5. Aguardando a borda voltar a responder 200 (agora via DR) =="
until [[ "$(code)" == "200" ]]; do sleep 1; done
t_ok=$(date +%s)
echo
echo "RTO medido (falha -> primeiro 200 pela borda): $((t_ok - t_fail)) s"
echo "O DR está servindo. O Argo CD (hub) está fora; o DR continua rodando o que já tinha."
echo "Para restaurar o primário: k3d cluster start aegis-prod  (veja docs/fase-8 sobre failback)"
