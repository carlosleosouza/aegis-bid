#!/usr/bin/env bash
# Constrói, testa e (opcionalmente) carrega nos clusters k3d as 3 imagens do AEGIS-Bid.
#
#   scripts/build-images.sh build    # docker build das 3 imagens
#   scripts/build-images.sh smoke    # sobe as imagens recém-construídas e prova que funcionam
#   scripts/build-images.sh load     # k3d image import nos clusters existentes (ponte local, ver Fase 1)
#   scripts/build-images.sh all      # build + smoke + load
#
# Variáveis: TAG (padrão: dev) · DRY_RUN=1 (só imprime os comandos)
#
# POR QUE o nome da imagem é lido do manifest e não digitado aqui:
# o Kubernetes só reaproveita uma imagem local se o NOME+TAG forem idênticos aos do
# manifest. Ler de deploy/base/bid-svc.yaml elimina a chance de os dois divergirem.
set -euo pipefail

cd "$(dirname "$0")/.."
TAG="${TAG:-dev}"
SERVICES=(auction bid settlement)

run() { if [[ -n "${DRY_RUN:-}" ]]; then echo "+ $*"; else "$@"; fi; }

# ghcr.io/<owner>/aegis-bid/bid:dev  ->  ghcr.io/<owner>/aegis-bid
prefix="$(grep -m1 -E '^\s+image: .*/aegis-bid/bid:' deploy/base/bid-svc.yaml | sed -E 's/.*image: *//; s#/bid:.*##')"
[[ -n "$prefix" ]] || { echo "✘ não achei a imagem do bid em deploy/base/bid-svc.yaml"; exit 1; }
if [[ "$prefix" == *__GITHUB_OWNER__* ]]; then
  echo "✘ o placeholder __GITHUB_OWNER__ ainda está nos manifests."
  echo "  Rode antes:  make configure OWNER=seu-usuario-github"
  exit 1
fi
img() { echo "${prefix}/$1:${TAG}"; }

do_build() {
  for s in "${SERVICES[@]}"; do
    echo "→ build $(img "$s")"
    # Contexto = apps/ (o Dockerfile copia common/ e <serviço>/). SERVICE escolhe qual serviço entra.
    run docker build -f apps/Dockerfile --build-arg "SERVICE=$s" -t "$(img "$s")" apps/
  done
  echo "✔ build concluído"
  run docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep "aegis-bid" || true
}

# --- helpers do smoke test ------------------------------------------------------
# As imagens python:slim não têm curl; usamos o próprio Python da imagem para fazer HTTP.
pycall() { # <container> <METHOD> <url> [json-body] [idempotency-key]
  docker exec -i "$1" python -c '
import sys, urllib.request
method, url, body, key = sys.argv[1:5]
headers = {"content-type": "application/json"}
if key: headers["Idempotency-Key"] = key
req = urllib.request.Request(url, data=body.encode() if body else None, method=method, headers=headers)
print(urllib.request.urlopen(req, timeout=5).read().decode())' "$2" "$3" "${4:-}" "${5:-}"
}

wait_ready() { # <container>
  for _ in $(seq 1 30); do
    if pycall "$1" GET http://127.0.0.1:8000/readyz >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "✘ $1 não ficou pronto em 30 s. Logs:"; docker logs "$1" 2>&1 | tail -20; return 1
}

do_smoke() {
  if [[ -n "${DRY_RUN:-}" ]]; then echo "+ (smoke: sobe redis + 3 imagens numa rede temporária e testa)"; return 0; fi
  # `net` é GLOBAL de propósito: o trap EXIT só dispara quando o script inteiro termina,
  # depois que as variáveis `local` da função já deixaram de existir (com `set -u`,
  # isso quebrava com "net: variável não associada" e a limpeza nunca rodava).
  net="aegis-smoke-$$"
  cleanup() {
    [[ -n "${net:-}" ]] || return 0
    docker rm -f "${net}-redis" "${net}-auction" "${net}-bid" "${net}-settlement" >/dev/null 2>&1 || true
    docker network rm "$net" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT
  docker network create "$net" >/dev/null

  echo "→ subindo redis + 3 serviços na rede $net"
  docker run -d --name "${net}-redis" --network "$net" --network-alias redis redis:7-alpine >/dev/null
  docker run -d --name "${net}-auction" --network "$net" --network-alias auction-svc \
    -e REDIS_URL=redis://redis:6379/0 "$(img auction)" >/dev/null
  docker run -d --name "${net}-bid" --network "$net" --network-alias bid-svc \
    -e REDIS_URL=redis://redis:6379/0 -e AUCTION_SVC_URL=http://auction-svc:8000 "$(img bid)" >/dev/null
  docker run -d --name "${net}-settlement" --network "$net" --network-alias settlement-svc \
    -e REDIS_URL=redis://redis:6379/0 "$(img settlement)" >/dev/null

  local fail=0
  for s in auction bid settlement; do
    if wait_ready "${net}-$s"; then echo "  ✔ $s: /readyz responde (a imagem sobe e fala com o Redis)"; else fail=1; fi
    uid="$(docker exec "${net}-$s" id -u)"
    if [[ "$uid" == "10001" ]]; then echo "  ✔ $s: roda como UID $uid (não-root; a Fase 7 exige isso)"
    else echo "  ✘ $s: UID=$uid (esperado 10001)"; fail=1; fi
  done
  [[ "$fail" -eq 0 ]] || { echo "RESULTADO: smoke com falhas ✘"; exit 1; }

  echo "→ fluxo ponta a ponta: cria leilão, dá lance e repete a mesma Idempotency-Key"
  created="$(pycall "${net}-auction" POST http://127.0.0.1:8000/api/auctions \
    '{"title":"Smoke","starting_price":1000,"duration_seconds":120}')"
  id="$(sed -E 's/.*"id":"([^"]+)".*/\1/' <<<"$created")"
  [[ -n "$id" && "$id" != "$created" ]] || { echo "  ✘ não consegui ler o id do leilão: $created"; exit 1; }
  body="{\"auction_id\":\"$id\",\"bidder\":\"ana\",\"amount\":1500}"
  first="$(pycall "${net}-bid" POST http://127.0.0.1:8000/api/bids "$body" smoke-1)"
  second="$(pycall "${net}-bid" POST http://127.0.0.1:8000/api/bids "$body" smoke-1)"
  grep -q '"duplicate":false' <<<"$first"  && echo "  ✔ 1º lance aceito"            || { echo "  ✘ 1º lance: $first";  fail=1; }
  grep -q '"duplicate":true'  <<<"$second" && echo "  ✔ repetição detectada (idempotência)" || { echo "  ✘ 2º lance: $second"; fail=1; }

  echo "→ aguardando o settlement aplicar o lance (até 15 s)"
  ok=0
  for _ in $(seq 1 15); do
    cur="$(pycall "${net}-auction" GET "http://127.0.0.1:8000/api/auctions/$id")"
    if grep -q '"highest_amount":1500' <<<"$cur"; then ok=1; break; fi
    sleep 1
  done
  [[ "$ok" -eq 1 ]] && echo "  ✔ settlement consumiu o stream: highest_amount = 1500" \
                    || { echo "  ✘ highest_amount não chegou a 1500: $cur"; fail=1; }

  [[ "$fail" -eq 0 ]] && echo "RESULTADO: imagens OK ✔" || { echo "RESULTADO: smoke com falhas ✘"; exit 1; }
}

do_load() {
  command -v k3d >/dev/null || { echo "✘ k3d não encontrado"; exit 1; }
  local clusters
  clusters="$(k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -E '^aegis-(prod|dr)$' || true)"
  [[ -n "$clusters" ]] || { echo "✘ nenhum cluster aegis-prod/aegis-dr encontrado. Rode 'make up' antes."; exit 1; }
  for c in $clusters; do
    echo "→ importando as imagens em $c"
    for s in "${SERVICES[@]}"; do run k3d image import "$(img "$s")" -c "$c"; done
  done
  echo "✔ imagens carregadas. Se os pods já existiam, reinicie-os (docs/fase-1-app-ci.md, passo 4)."
}

case "${1:-}" in
  build) do_build ;;
  smoke) do_smoke ;;
  load)  do_load ;;
  all)   do_build; do_smoke; do_load ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
