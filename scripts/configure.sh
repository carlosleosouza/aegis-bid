#!/usr/bin/env bash
# Troca o placeholder __GITHUB_OWNER__ pelo seu usuário/organização do GitHub.
#   ./scripts/configure.sh meu-usuario
#
# POR QUE existe: o GitOps exige que o Argo CD aponte para o SEU repositório, e o
# Kyverno só confia em imagens assinadas pelo SEU workflow. Sem trocar, nada fecha.
set -euo pipefail
cd "$(dirname "$0")/.."
OWNER="${1:-}"
[[ -n "$OWNER" ]] || { echo "uso: $0 <usuario-github>"; exit 2; }
OWNER="$(echo "$OWNER" | tr '[:upper:]' '[:lower:]')"   # o GHCR exige minúsculas

files=$(grep -rl --exclude-dir=.git --exclude-dir=node_modules '__GITHUB_OWNER__' bootstrap deploy platform docs README.md Makefile 2>/dev/null || true)
if [[ -z "$files" ]]; then echo "nada a trocar (já configurado?)"; exit 0; fi
# sed -i.bak funciona igual no Linux e no macOS; removemos os .bak depois.
for f in $files; do sed -i.bak "s/__GITHUB_OWNER__/${OWNER}/g" "$f" && rm -f "$f.bak"; done
echo "configurado para o owner '${OWNER}' em $(echo "$files" | wc -l | tr -d ' ') arquivo(s)."
echo "O repositório no GitHub precisa se chamar 'aegis-bid'. Próximo passo: git add -A && git commit && git push"
