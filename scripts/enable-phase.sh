#!/usr/bin/env bash
# "Liga" uma fase do projeto. Não aplica nada no cluster: só MODIFICA ARQUIVOS no
# Git. Quem aplica é o Argo CD depois do commit/push. Esse é o ponto do GitOps.
#   ./scripts/enable-phase.sh 3
set -euo pipefail
cd "$(dirname "$0")/.."
N="${1:-}"
[[ "$N" =~ ^[2-8]$ ]] || { echo "uso: $0 <fase 2-8>"; exit 2; }

copied=0
for f in platform/catalog/fase${N}-*.yaml; do
  [[ -e "$f" ]] || continue
  cp "$f" "platform/apps/$(basename "$f")"
  echo "ativado: platform/apps/$(basename "$f")"
  copied=$((copied+1))
done

toggled=0
for k in deploy/overlays/prod/kustomization.yaml deploy/overlays/dr/kustomization.yaml; do
  if grep -q "^#fase${N}# " "$k"; then
    sed -i.bak "s/^#fase${N}# //" "$k" && rm -f "$k.bak"
    echo "componente ligado em $k"
    toggled=$((toggled+1))
  fi
done

[[ $((copied+toggled)) -gt 0 ]] || echo "a fase $N não liga nada automaticamente (veja docs/fase-$N-*.md)"
echo
echo "Agora VEJA o que mudou e publique:"
echo "  git status && git diff"
echo "  git add -A && git commit -m 'feat: ativa fase $N' && git push"
