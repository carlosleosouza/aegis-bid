# Fase 2: GitOps de verdade (merge de PR = deploy)

## Objetivo
A aplicação passa a ser entregue **nos dois clusters a partir do Git**, por um ApplicationSet. Entrega: *nenhum* `kubectl apply` manual no fluxo normal, e o **self-heal** do Argo CD provado.

## Por quê
- **Fonte da verdade única**: o que está no Git é o que deve rodar. Quem muda o cluster à mão cria *drift*; o Argo CD detecta e desfaz.
- **Auditoria de graça**: cada mudança em produção é um commit/PR com autor, revisão e possibilidade de `git revert`.
- **ApplicationSet com o gerador de clusters**: um único template gera um Application por cluster rotulado (`aegis.io/env` = `prod` ou `dr`). Adicionar um cluster = registrá-lo; nenhum YAML novo.
- **Ligar fase = commit**: `make enable PHASE=N` só *edita arquivos* (copia Applications de `platform/catalog/` para `platform/apps/` e descomenta linhas `#faseN#` nos overlays). Quem aplica é o Argo CD, depois do seu push.

## Passos

### 1. Faça o merge do PR do CI **antes** de ligar a fase
Os overlays começam com `newTag: dev`, tag que **não existe** no registry. O PR `chore(deploy)` aberto pelo CI troca `dev` por `sha-xxxxxxx`. Sem o merge, os pods ficam em `ImagePullBackOff`. Confirme também que os 3 pacotes do GHCR estão **públicos** (Fase 1, passo 5).

> **Sem CI verde ainda?** Dá para validar esta fase com a ponte local: `make images images-load` carrega imagens com a tag `dev` direto nos clusters (Fase 1, passo 4), e o Argo CD sincroniza normalmente. Ela serve só para estudo; antes da Fase 7 volte ao fluxo CI → GHCR.

### 2. Ligue a fase
```bash
git pull
make enable PHASE=2
git status && git diff                  # LEIA o que mudou
git add -A && git commit -m "feat: ativa fase 2 (gitops)" && git push
```
Ativa `fase2-argo-rollouts.yaml` (o `bid-svc` é um *Rollout*) e `fase2-aegis-app.yaml` (o ApplicationSet).

### 3. Acompanhe o sync
```bash
make argocd-ui                          # outra aba: https://localhost:8443
make status
```
O Argo CD cria `aegis-prod` e `aegis-dr`. Na primeira sincronização pode haver erro transitório porque o CRD `Rollout` ainda não existe: o ApplicationSet já usa `SkipDryRunOnMissingResource` e `retry` com backoff.

## Verificação
```bash
kubectl --context k3d-aegis-prod -n aegis get pods,rollouts     # pods Running
kubectl --context k3d-aegis-dr   -n aegis get pods              # DR: serviços com 0 réplicas (pilot light); só o Redis sobe
curl -s -H 'Host: aegis.localhost' http://localhost:8090/api/auctions       # via borda (HAProxy) → 200
curl -s -H 'Host: aegis.localhost' -X POST http://localhost:8090/api/auctions \
  -H 'content-type: application/json' \
  -d '{"title":"Teste","starting_price":100,"duration_seconds":60}'
```
Nos bastidores, o health check do HAProxy marca o backend `dr` como **DOWN** (sem aplicação) e o `prod` como **UP** (`docker logs aegis-edge` mostra as transições). Isso é o esperado: o DR só recebe tráfego quando o primário falha.

### Prova de self-heal (o momento da demo)
```bash
kubectl --context k3d-aegis-prod -n aegis delete service bid-svc
# em ~segundos o Argo CD recria o Service (selfHeal: true). Observe na UI: OutOfSync → Synced.
```
Depois uma mudança *legítima*: edite `replicas`/`resources` em um manifest, faça push e veja o Argo CD aplicar sem você tocar no cluster.

> `replicas` é ignorado de propósito (`ignoreDifferences`): quem manda nele é o KEDA (prod) e o script de failover (dr). Sem isso, o self-heal brigaria com eles.

### 4. Fixe as versões dos charts (reprodutibilidade)
Os Applications de plataforma usam `targetRevision: "*"` (última versão) para o primeiro sync funcionar sem você adivinhar versões. Depois do **primeiro sync saudável**, fixe:
```bash
kubectl --context k3d-aegis-prod -n argocd get applications \
  -o custom-columns=NOME:.metadata.name,REVISAO:.status.sync.revision
```
Para charts Helm, a revisão resolvida costuma ser a versão do chart (confirme na UI do Argo CD, campo *Revision*). Troque `"*"` por essa versão nos arquivos de `platform/apps/` **e** de `platform/catalog/` (o catálogo é a origem dos futuros `make enable`), commit e push.
**Por quê**: sem fixar, um upgrade do chart upstream pode quebrar seu ambiente numa terça-feira qualquer, e você não saberá que mudou nada.

## Se der errado
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `ImagePullBackOff` | PR do CI não mesclado ou pacote privado | passo 1 |
| Application `Unknown`/`ComparisonError` | repo privado ou owner errado | repo precisa ser público; confira `make configure` |
| DR `Unknown` no Argo CD | cluster não registrado/alcançável | `kubectl --context k3d-aegis-prod -n argocd get secret -l argocd.argoproj.io/secret-type=cluster` |
| App fica `OutOfSync` o tempo todo em `replicas` | falta `ignoreDifferences` | já configurado no ApplicationSet; confira se o arquivo ativo é o do catálogo atual |

## Pergunta de entrevista
*"O que acontece se alguém der `kubectl edit` em produção?"* Com `selfHeal`, o Argo CD detecta a divergência e reverte para o estado do Git. A mudança emergencial legítima passa pelo Git (PR com revisão rápida), e isso deixa trilha de auditoria. *Argo CD vs Flux*: veja [ADR 001](adr/001-argocd-vs-flux.md).
