#!/usr/bin/env bash
# Confere pré-requisitos ANTES de gastar ~15 minutos subindo clusters.
# Por quê: falhar cedo, com mensagem clara, é parte de uma boa experiência de plataforma.
set -euo pipefail
cd "$(dirname "$0")/.."

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33maviso\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mfalta\033[0m %s\n' "$*"; MISSING=1; }

echo "Ferramentas obrigatórias:"
for t in docker k3d kubectl git make; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else fail "$t"; fi
done
echo "Opcionais (por fase):"
for t in k6 python3 helm; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else warn "$t ausente (k6: fase 5; python3: tools/ e testes; helm: só para inspecionar charts)"; fi
done
[[ "${MISSING:-0}" == "1" ]] && { echo; echo "Instale o que faltou (links em docs/fase-0-fundacao.md)."; exit 1; }

echo "Docker:"
docker info >/dev/null 2>&1 || { echo "O daemon do Docker não responde. Inicie o Docker e tente de novo."; exit 1; }
mem_gb=$(docker info --format '{{.MemTotal}}' | awk '{printf "%d", $1/1024/1024/1024}')
if   (( mem_gb < 8 ));  then warn "só ~${mem_gb} GB para o Docker: suba UM cluster e ligue as fases aos poucos (veja 'Plano B de recursos')"
elif (( mem_gb < 14 )); then warn "~${mem_gb} GB: ligue as fases 3+ gradualmente e acompanhe 'docker stats'"
else ok "~${mem_gb} GB de memória para o Docker"
fi

echo "Configuração do repositório:"
if grep -rlq "__GITHUB_OWNER__" bootstrap deploy platform 2>/dev/null; then
  echo "  O placeholder __GITHUB_OWNER__ ainda está nos arquivos. O Argo CD não saberia de qual"
  echo "  repositório ler. Rode:  make configure OWNER=seu-usuario-github   (e faça commit + push)"
  exit 1
fi
ok "placeholders substituídos"
echo "preflight concluído."
