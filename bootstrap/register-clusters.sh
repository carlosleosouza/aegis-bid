#!/usr/bin/env bash
# Registra os DOIS clusters no Argo CD (que roda no cluster primário, o "hub").
#
# POR QUE declarativo (Secret) e não `argocd cluster add`: não exige o CLI do
# Argo e é exatamente o que o `argocd cluster add` faz por baixo dos panos.
# Os ApplicationSets (platform/apps) selecionam clusters pelo label aegis.io/env.
set -euo pipefail

register() {
  local name="$1" env="$2" ctx="$3" server="$4"
  local ca cert key
  # --raw: sem isso o kubectl esconde os certificados ("DATA+OMITTED").
  cert=$(kubectl config view --raw --minify --context "$ctx" -o jsonpath='{.users[0].user.client-certificate-data}')
  key=$(kubectl config view --raw --minify --context "$ctx" -o jsonpath='{.users[0].user.client-key-data}')

  if [[ "$name" == "aegis-prod" ]]; then
    config='{"tlsClientConfig":{"insecure":false}}'
  else
    # LAB ONLY: o certificado do API server do DR não inclui o nome
    # k3d-aegis-dr-serverlb nos SANs, então desligamos a verificação do servidor.
    # Em produção: use SANs corretos e caData. Anote isto em docs/LICOES.md.
    config=$(printf '{"tlsClientConfig":{"insecure":true,"certData":"%s","keyData":"%s"}}' "$cert" "$key")
  fi

  kubectl --context k3d-aegis-prod -n argocd apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: cluster-${name}
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: cluster
    aegis.io/env: ${env}
type: Opaque
stringData:
  name: ${name}
  server: ${server}
  config: '${config}'
EOF
}

# O cluster onde o Argo roda é acessado pelo endereço interno do Kubernetes.
register aegis-prod prod k3d-aegis-prod https://kubernetes.default.svc
# O DR é acessado pelo nome do container do loadbalancer na rede Docker aegis-net.
register aegis-dr dr k3d-aegis-dr https://k3d-aegis-dr-serverlb:6443

echo "clusters registrados:"
kubectl --context k3d-aegis-prod -n argocd get secret -l argocd.argoproj.io/secret-type=cluster \
  -o custom-columns=NOME:.metadata.name,ENV:.metadata.labels.aegis\\.io/env
