#!/usr/bin/env bash
# Diagnóstico "a API não responde pela borda": percorre a cadeia de fora para dentro
#   cliente -> porta do host -> HAProxy -> Traefik do prod -> pods da aplicação
# e para na PRIMEIRA camada quebrada, dizendo o que fazer. Por quê: "não aconteceu nada"
# pode ter 5 causas diferentes, e cada uma tem um conserto diferente.
# Sem `set -e` de propósito: é diagnóstico, precisa continuar e relatar.
set -u
cd "$(dirname "$0")/.."

PROD=k3d-aegis-prod
EDGE_PORT="${EDGE_PORT:-8080}"
HOST_HDR='Host: aegis.localhost'
# porta direta do Traefik do prod (mapeamento do k3d-prod.yaml), para pular a borda
PROD_DIRECT="$(sed -n 's/.*- port: \([0-9]\+\):80.*/\1/p' bootstrap/k3d-prod.yaml 2>/dev/null | head -1)"; PROD_DIRECT="${PROD_DIRECT:-8081}"
HAPROXY_BIND="$(sed -n 's/^[[:space:]]*bind[[:space:]]\+\*:\([0-9]\+\).*/\1/p' bootstrap/haproxy.cfg 2>/dev/null | head -1)"; HAPROXY_BIND="${HAPROXY_BIND:-8080}"

code() { curl -s -o /dev/null -m 4 -w '%{http_code}' "$@" 2>/dev/null || true; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✘\033[0m %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
stop_with() { printf '\n\033[1mPRÓXIMO PASSO:\033[0m %s\n\n' "$1"; exit 1; }

echo "Cadeia: curl -> localhost:$EDGE_PORT (HAProxy) -> Traefik do prod -> pods em 'aegis'"

step "1. O container da borda está de pé?"
st="$(docker ps --filter 'name=^aegis-edge$' --format '{{.Status}}' 2>/dev/null)"
if [[ -z "$st" ]]; then
  bad "container aegis-edge não está rodando"
  stop_with "rode  make edge  (se falhar com 'port is already allocated', veja docs/fase-0-fundacao.md, 'Porta ocupada')"
fi
ok "aegis-edge: $st"
inner="$(docker port aegis-edge 2>/dev/null | sed -n 's#^\([0-9]\+\)/tcp -> .*#\1#p' | head -1)"
published="$(docker port aegis-edge 2>/dev/null | sed -n 's#.*:\([0-9]\+\)$#\1#p' | head -1)"
echo "     porta do host publicada: ${published:-?}   porta interna publicada: ${inner:-?}   bind do haproxy.cfg: $HAPROXY_BIND"

step "2. O mapeamento de portas combina com o HAProxy?"
if [[ -n "$inner" && "$inner" != "$HAPROXY_BIND" ]]; then
  bad "o Docker encaminha para :$inner dentro do container, mas o HAProxy escuta em :$HAPROXY_BIND"
  stop_with "os dois lados precisam ser iguais. Com o Makefile novo isso é automático: rode  make edge  (ele relê o bind do haproxy.cfg). Se editou o -p à mão, refaça."
fi
ok "porta interna ($inner) = bind do HAProxy ($HAPROXY_BIND)"
if [[ -n "$published" && "$published" != "$EDGE_PORT" ]]; then
  echo "     ⚠ a borda está publicada em :$published, mas você está testando :$EDGE_PORT. Use  EDGE_PORT=$published  ou refaça com  make edge EDGE_PORT=$EDGE_PORT"
  EDGE_PORT="$published"
fi

step "3. A borda responde em http://localhost:$EDGE_PORT ?"
c="$(code -H "$HOST_HDR" "http://localhost:$EDGE_PORT/api/auctions")"
case "$c" in
  200) ok "HTTP 200: a API respondeu pela borda."
       echo; echo "Tudo certo. Teste o fluxo:"
       echo "  curl -s -H '$HOST_HDR' -X POST http://localhost:$EDGE_PORT/api/auctions -H 'content-type: application/json' -d '{\"title\":\"Teste\",\"starting_price\":100,\"duration_seconds\":60}'"
       exit 0 ;;
  000) bad "nenhuma resposta (conexão recusada ou resetada)"
       echo "     Últimas linhas do HAProxy:"; docker logs aegis-edge --tail 8 2>&1 | sed 's/^/       /'
       stop_with "o HAProxy não está atendendo nessa porta. Veja se o log acima tem erro de configuração; depois  make edge." ;;
  503) bad "HTTP 503: o HAProxy está no ar, mas NENHUM servidor atrás dele está saudável"
       echo "     O que o HAProxy diz sobre os servidores (últimas transições):"
       docker logs aegis-edge 2>&1 | grep -E 'Server .* is (UP|DOWN)' | tail -4 | sed 's/^/       /' ;;
  404) bad "HTTP 404: chegou no cluster, mas não há rota/aplicação para /api/auctions" ;;
  *)   bad "HTTP $c inesperado" ;;
esac

step "4. O Traefik do prod responde direto (sem a borda), em :$PROD_DIRECT ?"
d="$(code -H "$HOST_HDR" "http://localhost:$PROD_DIRECT/api/auctions")"
case "$d" in
  200) ok "HTTP 200 direto no prod: a aplicação está bem; o problema é entre a borda e o prod"
       stop_with "veja o 'is DOWN, reason: ...' acima. Reinicie a borda:  make edge" ;;
  404) bad "HTTP 404: o cluster está de pé, mas não existe rota para a API (aplicação não implantada)" ;;
  000) bad "nada responde em :$PROD_DIRECT"
       stop_with "o cluster prod ou o Traefik não está acessível. Rode  k3d cluster list  e  make status ." ;;
  *)   bad "HTTP $d" ;;
esac

step "5. A aplicação foi implantada no cluster?"
if ! compgen -G 'platform/apps/*.yaml' >/dev/null; then
  bad "platform/apps/ está vazio: você ainda não ligou a Fase 2"
  stop_with "make enable PHASE=2  ->  git diff  ->  git add -A && git commit -m 'feat: ativa fase 2' && git push   (o Argo CD só implanta o que está no Git)"
fi
ok "platform/apps/ tem Applications ligadas (Fase 2 ativada localmente)"
if ! kubectl --context "$PROD" get namespace aegis >/dev/null 2>&1; then
  bad "o namespace aegis não existe no prod: o Argo CD ainda não sincronizou"
  echo "     Applications:"; kubectl --context "$PROD" -n argocd get applications 2>&1 | sed 's/^/       /'
  stop_with "você fez o push para o GitHub (branch main, repositório público)? Confira também  make status  e a UI:  make argocd-ui"
fi
ok "namespace aegis existe"
pods="$(kubectl --context "$PROD" -n aegis get pods --no-headers 2>&1)"
echo "$pods" | sed 's/^/       /'
if grep -qE 'ImagePullBackOff|ErrImagePull' <<<"$pods"; then
  bad "pods sem imagem (ImagePullBackOff/ErrImagePull)"
  stop_with "as imagens não estão no cluster. Ponte local:  make images images-load  e depois  kubectl --context $PROD -n aegis delete pod -l 'app in (auction-svc,bid-svc,settlement-svc)'   (docs/fase-1-app-ci.md, passo 4)"
fi
if grep -qE 'CrashLoopBackOff|Error|Pending' <<<"$pods"; then
  bad "há pods com problema"
  stop_with "veja o motivo:  kubectl --context $PROD -n aegis describe pod <nome>   e   kubectl --context $PROD -n aegis logs <nome>"
fi
stop_with "os pods parecem saudáveis, mas a API ainda não responde. Veja o Ingress:  kubectl --context $PROD -n aegis get ingress,svc   e me envie a saída deste diagnóstico."
