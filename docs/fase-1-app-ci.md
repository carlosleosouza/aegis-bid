# Fase 1: Aplicação e CI com cadeia de suprimentos

## Objetivo
Entender a aplicação rodando **sem Kubernetes** e ter um pipeline que testa, escaneia, publica, gera SBOM e **assina** cada imagem. Entrega: pipeline verde e imagens assinadas no GHCR.

## Por quê
Entender a aplicação *antes* de pensar em plataforma evita depurar dois problemas ao mesmo tempo. O pipeline com supply chain responde a perguntas de entrevista que "CI/CD bonito" não responde: *como você sabe que o que roda no cluster é o que o seu pipeline construiu?*

## A aplicação em 2 minutos
Três serviços FastAPI falando com um Redis:
- **auction-svc**: cria e consulta leilões (`POST/GET /api/auctions`).
- **bid-svc**: recebe lances (`POST /api/bids`, header `Idempotency-Key` obrigatório) e publica no stream `bids`.
- **settlement-svc**: consome o stream (consumer group), aplica cada lance **uma vez** e fecha leilões vencidos.

A regra de ouro: *o mesmo lance nunca é aplicado duas vezes e o maior lance nunca é sobrescrito por um menor*. Leia [ADR 005](adr/005-idempotencia-do-lance.md) e o docstring de `apps/bid/main.py` antes de seguir.

## Passos

### 1. Rode os testes e leia-os
```bash
make test
```
Os testes em `apps/tests/test_flow.py` são a melhor documentação do comportamento: replay com a mesma chave, redelivery após crash do consumidor (`XAUTOCLAIM`), mensagem venenosa indo para a DLQ.

### 2. Suba a aplicação com docker compose
```bash
make dev
```
Portas: auction `:8001`, bid `:8002`, settlement `:8003`.

```bash
# cria um leilão de 120 s (valores em centavos)
curl -s -X POST localhost:8001/api/auctions -H 'content-type: application/json' \
  -d '{"title":"Relógio","starting_price":1000,"duration_seconds":120}'
# anote o "id" da resposta e use abaixo
ID=COLE_O_ID

curl -s -X POST localhost:8002/api/bids -H 'content-type: application/json' \
  -H 'Idempotency-Key: abc-123' -d "{\"auction_id\":\"$ID\",\"bidder\":\"ana\",\"amount\":1500}"
# repita o MESMO comando: deve voltar "duplicate": true
curl -s localhost:8001/api/auctions/$ID     # highest_amount deve ser 1500
curl -s localhost:8002/metrics | grep aegis_bids_total
```
**Verificação**: o segundo POST devolve `duplicate: true`; `aegis_bids_total{outcome="accepted"}` é 1 e `replayed` é 1. Encerre com `make dev-down`.

### 3. Construa as imagens e entenda o Dockerfile
O Kubernetes não roda código, roda **imagens**. Até aqui o `make dev` construiu as imagens por baixo dos panos; agora você faz isso de propósito.

Existe **um único** `apps/Dockerfile` para os três serviços. O argumento `SERVICE` escolhe qual entra na imagem:

```bash
make configure OWNER=seu-usuario     # uma vez só; o nome da imagem depende do seu usuário
make images                          # = 3x docker build
```

O que o alvo executa, para cada serviço (`auction`, `bid`, `settlement`):
```bash
docker build -f apps/Dockerfile --build-arg SERVICE=bid \
  -t ghcr.io/seu-usuario/aegis-bid/bid:dev apps/
```

| Detalhe do Dockerfile | Por quê |
|---|---|
| contexto de build é `apps/`, não a raiz | o Dockerfile copia `common/` (métricas, Redis) e a pasta do serviço; o resto do repo não entra na imagem |
| **multi-stage** (`deps` → imagem final) | o `pip` e o cache ficam para trás: imagem menor, menos superfície de ataque, scan do Trivy mais limpo |
| `USER 10001` (numérico) | a política `runAsNonRoot` da Fase 7 só consegue verificar um UID numérico |
| nome = `ghcr.io/<owner>/aegis-bid/<svc>:dev` | é **exatamente** o que está em `deploy/base/*.yaml`. O Kubernetes só reaproveita uma imagem se nome e tag forem idênticos |

**Prove que as imagens funcionam** antes de levá-las ao cluster:
```bash
make image-smoke
```
O teste sobe um Redis e as três imagens numa rede Docker temporária e confere, nesta ordem: `/readyz` de cada serviço (a imagem sobe *e* alcança o Redis), UID 10001 (não-root), e o fluxo ponta a ponta (cria leilão, dá um lance, repete a mesma `Idempotency-Key`, espera o settlement aplicar). Ele limpa tudo ao terminar.

**Verificação**: termina com `RESULTADO: imagens OK ✔`. Se falhar, ele imprime os logs do container que não ficou pronto.

> Isto verifica a **imagem**. Verificar a **origem** dela (quem construiu, sem adulteração) é papel do CI e da assinatura, nos passos 5 e 6.

### 4. Como a imagem chega ao cluster (dois caminhos)
Os pods puxam a imagem de um registry, e o cluster não enxerga o cache do seu `docker build`. Há dois caminhos:

**Caminho A: o oficial (CI → GHCR).** O workflow do passo seguinte constrói, escaneia, publica e assina; o PR `chore(deploy)` troca `dev` por `sha-xxxxxxx`. É o fluxo que você apresenta em entrevista, mas exige o repositório no GitHub funcionando.

**Caminho B: a ponte local (sem GHCR).** Útil para validar a Fase 2 mesmo que o CI ainda não esteja verde:
```bash
make up            # clusters precisam existir (Fase 0)
make images        # se ainda não construiu
make images-load   # k3d image import nos clusters aegis-prod e aegis-dr
```
Por que funciona sem mexer nos manifests: o `k3d image import` copia a imagem para dentro dos nós com **o mesmo nome e tag** (`:dev`). Como `dev` não é `latest`, o Kubernetes usa `imagePullPolicy: IfNotPresent`, acha a imagem no nó e nunca vai ao GHCR.

Cuidados:
- **Reconstruiu a mesma tag `dev`?** O nó não busca de novo (a imagem "já está presente"). Rode `make images images-load` e recrie os pods: `kubectl --context k3d-aegis-prod -n aegis delete pod -l 'app in (auction-svc,bid-svc,settlement-svc)'`. Use o `delete pod` por label porque o `bid-svc` vira um `Rollout` do Argo Rollouts na Fase 4 e o `kubectl rollout restart` nativo não cobre esse tipo (o Redis fica de fora de propósito: não tem persistência e perderia os leilões). Repita com `--context k3d-aegis-dr` se o DR estiver de pé.
- **A ponte é só para estudo.** Ela não gera assinatura nem SBOM, então a política da Fase 7 (só imagens assinadas) vai barrar essas imagens quando for para `Enforce`. Antes de ligar a Fase 7, migre para o caminho A.
- **Depois do merge do PR do CI** as tags passam a ser `sha-xxxxxxx`, que a ponte não cobre: daí em diante o caminho A é obrigatório.

### 5. Ative o CI
O workflow `.github/workflows/ci.yml` roda no push para `main`:

| Etapa | Por quê |
|---|---|
| testes + `make slo-rules` sem diff + `yamllint` + `kubectl kustomize` | nada quebrado ou desatualizado chega ao build |
| build **sem publicar** + Trivy (`CRITICAL,HIGH` com correção) | imagem vulnerável nunca chega ao registry |
| publicação por **digest** | a tag é mutável; o digest identifica exatamente estes bytes |
| SBOM (Syft, SPDX) | responde "estou afetado pela CVE X?" em segundos |
| `cosign sign` + `cosign attest` (keyless) | prova *quem* construiu; sem chave para vazar |
| PR que atualiza as tags em `deploy/overlays/*` | o deploy é o *merge do PR* (GitOps) |

Configurações únicas no GitHub:
1. `Settings → Actions → General → Workflow permissions`: marque **Allow GitHub Actions to create and approve pull requests**.
2. Depois do primeiro build, em `Packages`, torne cada pacote (`aegis-bid/auction`, `/bid`, `/settlement`) **público**. Pacotes do GHCR nascem privados e o cluster não consegue baixá-los sem credenciais (causa clássica de `ImagePullBackOff`).

### 6. Verifique a assinatura (se tiver o cosign instalado)
```bash
cosign verify ghcr.io/SEU-USUARIO/aegis-bid/bid@sha256:<digest-do-log-do-CI> \
  --certificate-identity-regexp '^https://github.com/SEU-USUARIO/aegis-bid/\.github/workflows/ci\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Verificação final
Run do CI verde, três imagens em `Packages` com tag `sha-xxxxxxx`, artefatos `sbom-*` no run, e um PR `chore(deploy)` aberto pelo bot. **Não faça o merge ainda**: a Fase 2 explica o que acontece.

## Se der errado
| Sintoma | Causa provável |
|---|---|
| Trivy reprova | atualize a base `python:3.12-slim` ou a dependência apontada; o scan *deve* bloquear |
| `create-pull-request` falha com 403 | falta a permissão do passo 5.1 |
| `cosign sign` falha em `id-token` | o job precisa de `permissions: id-token: write` (já está no workflow) |
| Loop de builds | o job `build` ignora commits `chore(deploy)` justamente para evitar isso |

## Pergunta de entrevista
*"Por que assinar imagens e para que serve um SBOM?"* A assinatura prova a origem e a integridade (só o *meu* workflow produz imagens aceitas); o SBOM é o inventário de componentes, que permite responder rapidamente a uma nova CVE sem reabrir imagens. Juntos, viram *política* na Fase 7: o cluster recusa o que não vier do pipeline.
