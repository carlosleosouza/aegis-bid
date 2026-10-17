# AEGIS-Bid: roteiro de estudo passo a passo (S00 a S19)

Este roteiro serve para você **entender** o projeto, não só executá-lo. Os guias `docs/fase-N-*.md` dizem *o que rodar*; este diz *o que pensar antes, o que olhar durante e o que explicar depois*.

## Como usar (o método de cada sessão)

Toda sessão segue o mesmo ciclo:

| Etapa | O que fazer | Por quê |
|---|---|---|
| **Leia** | os arquivos indicados, *antes* de rodar | comando sem leitura vira receita de bolo |
| **Preveja** | escreva 1 a 3 frases: "eu espero que ... porque ..." | só dá para aprender com o erro de uma previsão que existe |
| **Faça** | os comandos da sessão | |
| **Observe** | o que comparar com a previsão | a surpresa é o que fica na memória |
| **Explique** | responda as perguntas em voz alta, sem consultar | se você não consegue dizer, ainda não entendeu |
| **Checkpoint** | condição para avançar | falha aqui = investigue, não avance |
| **Registre** | uma linha em `docs/LICOES.md` | é o material da pergunta "me conte de um problema" |

Regras: uma sessão por vez; um commit por sessão (`git commit -m "estudo S06: aprendi que ..."`); se a previsão falhar, descubra o porquê antes de seguir.

## O mapa em um minuto

```
código ─► CI (testa, escaneia, assina) ─► PR troca a tag ─► merge ─► Argo CD aplica ─► cluster
                                                                           │
          Prometheus mede ─► SLO/budget ─► canary aborta / gate bloqueia ◄─┘
          Chaos Mesh quebra de propósito ─► você compara com a hipótese
          Kyverno recusa o que não é seu ─► DR assume se o prod cair
```

| Fase | Pergunta que a fase responde |
|---|---|
| 0 | Onde as coisas rodam, e como recrio tudo com um comando? |
| 1 | A aplicação funciona? Como a imagem nasce e por que confio nela? |
| 2 | Quem aplica no cluster? Só o Git. |
| 3 | Como sei se está "bom"? SLO, budget, burn rate. |
| 4 | Como um deploy ruim se desfaz sozinho? |
| 5 | Como a plataforma reage a um pico? |
| 6 | O sistema resiste ao que eu *acho* que ele resiste? |
| 7 | Como o cluster recusa o que não é confiável? |
| 8 | Se o prod morrer inteiro, quanto tempo e quantos dados eu perco? |

## Onde você está agora

Confira antes de continuar:

```bash
make status                                   # clusters, borda, Argo CD, Applications
git fetch && git log -1 --format='%h %s' origin/main
```

| Se... | Você está em... |
|---|---|
| só existe a Application `aegis-root`, e `platform/apps/` está vazio | fim da Fase 1, antes de ligar a Fase 2 (comece em S05 ou S06) |
| o job `build` do CI está verde e há um PR `chore(deploy)` | S05 concluída; falta S06 |
| o namespace `aegis` tem pods `Running` | Fase 2 de pé; vá para S07 |

As sessões S00 a S04 você já executou na prática. Vale reler os "Explique" delas: executar não é o mesmo que entender.

---

# PARTE 1: Fundação, aplicação e GitOps (Fases 0, 1 e 2)

## S00 · Ambiente e repositório (1 h)

**Objetivo**: ter as ferramentas e o repositório certo, sabendo *por que* o Git importa tanto.

**Leia**: `docs/fase-0-fundacao.md` (seções Objetivo, Por quê, Pré-requisitos).

**Faça**
```bash
make preflight                                  # confere docker, k3d, kubectl, memória
grep -rl '__GITHUB_OWNER__' deploy bootstrap platform | head   # deve imprimir NADA após make configure
git remote -v                                   # aponta para o seu repositório PÚBLICO aegis-bid
```

**Explique**
1. Por que o repositório precisa ser público e chamar-se exatamente `aegis-bid`? (O Argo CD lê do GitHub, não da sua pasta; o nome entra na identidade da assinatura.)
2. O que o placeholder `__GITHUB_OWNER__` quebraria se ficasse?

**Checkpoint**: `make preflight` sem erro e o `grep` sem saída.

## S01 · A aplicação por dentro (2 h)

**Objetivo**: entender a regra central do projeto: *o mesmo lance nunca é aplicado duas vezes, e o maior lance nunca é sobrescrito por um menor*.

**Leia, nesta ordem**
1. `docs/adr/005-idempotencia-do-lance.md`
2. `apps/bid/main.py`: `make_bid_id` e `place_bid`
3. `apps/settlement/processor.py`: o script Lua `APPLY_BID_LUA` e `handle`
4. `apps/tests/test_flow.py`

**Preveja** (escreva antes de ler os testes): se o `bid-svc` publicar a mesma mensagem duas vezes no stream, o que impede o leilão de contar o lance duas vezes?

**Faça**
```bash
make test        # 12 + 19 passam; 2 "skipped" sem redis-server (veja fase-1, passo 1)
```
Depois faça o laboratório de mutações de `docs/estudo/02-a-aplicacao.md`: quebre o código de propósito e veja qual teste acusa.

**Explique**
1. Por que o `SET NX` no bid (passo 8 do fluxo) é só uma otimização e a garantia está no Lua do settlement?
2. Por que o `XACK` vem **depois** de aplicar o lance? O que aconteceria na ordem inversa se o pod morresse no meio?
3. O que significa a resposta **202**? (aceito na fila, não vencedor)

**Checkpoint**: você desenha no papel a cadeia `POST → idem → XADD → XREADGROUP → Lua → XACK`.

## S02 · A aplicação rodando, sem Kubernetes (1,5 h)

**Objetivo**: ver os dados dentro do Redis para o fluxo deixar de ser abstrato.

**Faça**
```bash
make dev
ID=$(curl -s -X POST localhost:8001/api/auctions -H 'content-type: application/json' \
  -d '{"title":"Relógio","starting_price":1000,"duration_seconds":600}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "$ID"

# o MESMO lance duas vezes
for i in 1 2; do curl -s -X POST localhost:8002/api/bids -H 'content-type: application/json' \
  -H 'Idempotency-Key: abc-123' -d "{\"auction_id\":\"$ID\",\"bidder\":\"ana\",\"amount\":1500}"; echo; done
```

**Preveja**: depois dos dois POSTs, quantas mensagens existem no stream `bids`? Quantas chaves `idem:*`?

**Observe dentro do Redis**
```bash
docker compose exec redis redis-cli XLEN bids                         # 1
docker compose exec redis redis-cli --scan --pattern 'idem:*'         # 1 chave
docker compose exec redis redis-cli --scan --pattern 'processed:*'    # 1 chave (o settlement aplicou)
docker compose exec redis redis-cli HGETALL "auction:$ID"             # highest_amount 1500
docker compose exec redis redis-cli XINFO GROUPS bids                 # grupo "settlement", pending 0
curl -s localhost:8002/metrics | grep aegis_bids_total                # accepted=1, replayed=1
```
Agora um lance **menor** (`amount: 1200`, outra `Idempotency-Key`) e veja `highest_amount` continuar 1500.

**Explique**: qual das três chaves (`idem:`, `processed:`, o stream) protege contra o quê?

**Checkpoint**: `XLEN bids` = 1 após o replay. Encerre com `make dev-down`.

## S03 · Fundação, teoria: ler antes de entender o `make up` (1,5 h)

**Leia**: `bootstrap/k3d-prod.yaml`, `bootstrap/register-clusters.sh`, `bootstrap/root-app.yaml`, `bootstrap/haproxy.cfg` e o alvo `up` do `Makefile`.

**Faça (só leitura)**
```bash
make -n up        # mostra, sem executar, os comandos que o make up roda
```

**Explique**
1. Por que há **uma única** coisa aplicada à mão (`root-app`)? O que ela observa? (`platform/apps/` no GitHub, `prune` e `selfHeal` ligados.)
2. Para que serve a rede `aegis-net`? (Argo do prod alcançar o DR; Redis do DR replicar do prod; HAProxy falar com os dois.)
3. No `haproxy.cfg`: por que o health check é `GET /api/auctions` esperando 200 e não um simples TCP? Por que o `dr` é `backup`? Por que `init-addr ... none`?
4. Por que o Argo CD roda **só** no prod? Qual o risco? (Pergunta de entrevista da Fase 0.)

**Checkpoint**: você explica por que o DR "não tem Argo CD próprio" e ainda assim recebe a aplicação.

## S04 · Fundação, prática: `make up` e o que ele criou (2 h)

**Faça**
```bash
make status
docker network inspect aegis-net --format '{{range .Containers}}{{.Name}}{{"\n"}}{{end}}'
docker logs aegis-edge 2>&1 | grep -E 'Server .* is (UP|DOWN)' | tail
make argocd-password && make argocd-ui        # https://localhost:8443, usuário admin
```

**Preveja**: antes da Fase 2, o servidor `prod` do HAProxy está UP ou DOWN? Por quê? (DOWN: não há aplicação respondendo 200; o Traefik responde 404.)

**Observe**: na UI, `aegis-root` Synced/Healthy; em *Settings → Clusters*, `aegis-prod` e `aegis-dr`.

**A prova de reprodutibilidade**: `time (make down && make up)`. Anote o tempo; é um número para o README e a entrevista.

**Checkpoint**: tudo recriado, e você sabe dizer o que cada etapa do `up` criou (preflight, clusters, argocd, register, root, edge).

**Registre**: o problema de porta que você teve e como o `EDGE_PORT` o resolve.

## S05 · CI com cadeia de suprimentos (2 h)

**Objetivo**: entender o que cada etapa do pipeline *prova*.

**Leia**: `.github/workflows/ci.yml` (jobs `test`, `build`, `update-manifests`) e a tabela do passo 5 de `docs/fase-1-app-ci.md`.

**Preveja**: se você alterar um texto na API e der push na `main`, quais jobs rodam e em que ordem? (`test` → `build` ×3 → `update-manifests`; o `budget-gate` só roda em PR.)

**Faça**
1. Dê push de uma mudança trivial. Em *Actions*, abra o run e identifique cada etapa do `build`.
2. Confirme os três pacotes públicos em *Packages* e teste sem login:
   ```bash
   docker logout ghcr.io
   docker pull ghcr.io/carlosleosouza/aegis-bid/bid:sha-<7chars>
   ```
3. Verifique a assinatura (cosign instalado):
   ```bash
   cosign verify ghcr.io/carlosleosouza/aegis-bid/bid@sha256:<digest-do-log> \
     --certificate-identity-regexp '^https://github.com/carlosleosouza/aegis-bid/\.github/workflows/ci\.yml@refs/heads/main$' \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com
   ```
4. Baixe o artefato `sbom-*` do run e abra: é o inventário de pacotes da imagem.

**Explique**
1. Por que o Trivy roda **antes** de publicar? (Imagem vulnerável nunca chega ao registry.)
2. Por que publicar e assinar por **digest** e não por tag? (Tag é mutável; digest identifica os bytes.)
3. O que significa "keyless"? Que identidade o `cosign verify` está conferindo? (Quem assinou: este workflow, nesta branch.)
4. Por que o `build` ignora commits `chore(deploy)`? (Evitar loop.)

**Checkpoint**: jobs verdes, PR `chore(deploy)` aberto, `docker pull` sem login funcionando, `cosign verify` imprimindo "verified".

## S06 · GitOps: merge de PR = deploy (2 h)

**Objetivo**: ver o Argo CD ser o único a aplicar.

**Leia**: `platform/catalog/fase2-aegis-app.yaml` (o ApplicationSet), `deploy/overlays/prod/kustomization.yaml` e `scripts/enable-phase.sh`.

**Preveja**: o que `make enable PHASE=2` altera no seu disco? Altera algo no cluster? (Só arquivos. Quem aplica é o Argo CD, depois do push.)

**Faça**
```bash
# 1. Merge do PR do CI (troca newTag: dev por sha-xxxxxxx), depois:
git pull
make enable PHASE=2
git status && git diff             # LEIA: copiou 2 Applications e mexeu em nada mais
git add -A && git commit -m "feat: ativa fase 2 (gitops)" && git push
make status                        # repita até as Applications aparecerem
```

**Observe**: o ApplicationSet gera `aegis-prod` e `aegis-dr` a partir de **um** template (gerador `clusters`, label `aegis.io/env`).

**Verificação**
```bash
kubectl --context k3d-aegis-prod -n aegis get pods,rollouts      # prod: pods Running
kubectl --context k3d-aegis-dr   -n aegis get pods               # dr: só o Redis (serviços com 0 réplicas)
make doctor                                                      # deve terminar em HTTP 200
```

**Explique**
1. Por que o DR tem os serviços com 0 réplicas? (Pilot light: economia; o failover os liga.)
2. Por que o ApplicationSet ignora `replicas` (`ignoreDifferences`)? Quem manda nelas? (KEDA no prod, script de failover no DR.)
3. Se o merge do PR não tivesse acontecido antes, o que você veria? (`ImagePullBackOff`: a tag `dev` não existe no registry.)

**Checkpoint**: `make doctor` termina com 200 e o fluxo do curl (criar leilão, lance duplicado, `highest_amount`) funciona pela borda.

## S07 · Self-heal, drift e versões fixadas (1,5 h)

**Objetivo**: provar com as próprias mãos que *o cluster obedece ao Git*.

**Experimento A: o que o Argo desfaz**
```bash
kubectl --context k3d-aegis-prod -n aegis delete service bid-svc
kubectl --context k3d-aegis-prod -n aegis get svc -w     # o Service volta em segundos
```
**Preveja** antes: volta? Em quanto tempo? Observe na UI o `OutOfSync → Synced`.

**Experimento B: o que o Argo propositalmente NÃO desfaz**
```bash
kubectl --context k3d-aegis-prod -n aegis scale deploy/auction-svc --replicas=3
kubectl --context k3d-aegis-prod -n aegis get deploy auction-svc   # continua 3
```
**Preveja**: volta para 2? (Não: `replicas` está em `ignoreDifferences`, senão o self-heal brigaria com o KEDA.) Devolva com `--replicas=2`.

**Experimento C: mudança legítima pelo Git**: altere `resources` em `deploy/base/auction-svc.yaml`, faça commit e push e acompanhe o Argo aplicar sem você tocar no cluster. Depois `git revert` e veja voltar.

**Fixe as versões**: siga o passo 4 de `docs/fase-2-gitops.md` (troque `"*"` pela versão resolvida nos arquivos de `platform/apps/` **e** `platform/catalog/`).

**Explique**: "O que acontece se alguém der `kubectl edit` em produção?" Responda com A e B como evidência.

**Checkpoint**: A volta, B permanece, C aplica pelo Git. **Registre** o que te surpreendeu.

---

# PARTE 2: Observabilidade, entrega segura e escala (Fases 3, 4 e 5)

## S08 · Prometheus, Grafana e a coleta de métricas (2 h)

**Leia**: `apps/common/aegis_common/metrics.py` (note o bucket `0.3` e o label `route`) e `deploy/components/monitoring/`.

**Faça**
```bash
make enable PHASE=3
git diff && git add -A && git commit -m "feat: ativa fase 3" && git push
# espere o Application kube-prometheus-stack ficar Healthy (leva alguns minutos)
make prom-ui        # terminal 1: http://localhost:9090
for i in $(seq 1 200); do curl -s -o /dev/null -H 'Host: aegis.localhost' http://localhost:8080/api/auctions; done
```

**Preveja**: em `Status → Targets`, quantos alvos da aplicação ficam UP? (Os 3 serviços, via ServiceMonitor.)

**No Prometheus, rode e leia cada resultado**
```
sum(rate(aegis_http_requests_total[1m])) by (service, code)
histogram_quantile(0.99, sum by (le) (rate(aegis_http_request_duration_seconds_bucket[1m])))
```
(Se o nome do histograma diferir, abra `metrics.py` e use o nome real: é o contrato.)

**Explique**
1. Por que `serviceMonitorSelectorNilUsesHelmValues: false`? (Sem isso o Prometheus ignora o que não tem o label do release Helm.)
2. Por que `honorLabels: true`? (Senão `service` vira `exported_service` e as consultas de SLO deixam de casar.)
3. Por que `route` usa o *template* da rota e não o caminho real? (Cardinalidade infinita.)

**Checkpoint**: alvos UP e a consulta de taxa devolvendo dados.

## S09 · SLOs, burn rate e alertas (2 h)

**Leia**: `platform/observability/slos.yaml` e `tools/slo_gen.py`. Depois a seção "Os 3 SLOs" de `docs/fase-3-observabilidade.md`.

**Conceitos para fixar** (escreva com suas palavras)
- **SLI**: a medida (fração de lances com 5xx).
- **SLO**: a meta (99,9%).
- **Error budget**: o que sobra para errar (0,1%).
- **Burn rate**: a velocidade com que o budget é gasto. 1× esgota o budget exatamente no fim da janela; 14,4× esgota em ~2 dias.

**Faça**
```bash
make grafana-ui                    # http://localhost:3000, admin / aegis-lab
```
Abra **AEGIS-Bid: Saúde do Leilão** e identifique cada painel. Depois o ciclo completo de um SLO:
```bash
# edite "objective" de um SLO em platform/observability/slos.yaml, então:
make slo-rules && git diff platform/observability
```
**Preveja**: se você editar o `slos.yaml` e esquecer o `make slo-rules`, o que acontece? (O job `test` do CI falha no `git diff --exit-code`.) Reverta a edição depois.

**Explique**: por que alertar por burn rate em duas janelas (longa + curta) e não por "erro > 1%"?

**Checkpoint**: em `Status → Rules` aparecem regras `AegisErrorBudgetFastBurn`; `aegis:error_budget_remaining:ratio{slo="bid-availability"}` ≈ 1.

**Registre** o achado de retenção: o Prometheus guarda 7 dias, então o "budget de 30 dias" é calculado sobre no máximo 7.

## S10 · Canary saudável (1,5 h)

**Leia**: `deploy/base/bid-svc.yaml` (é um **Rollout**, 4 réplicas, passos 25% → 50%) e `deploy/components/canary-analysis/`.

**Faça**
```bash
make enable PHASE=4
git diff && git add -A && git commit -m "feat: ativa fase 4" && git push
make rollout-watch                 # precisa do plugin kubectl-argo-rollouts; senão use kubectl get rollout -w
```
Mude algo trivial no `bid-svc`, dê push, deixe o CI abrir o PR, faça o merge e acompanhe. Gere tráfego de lances em outro terminal.

**Preveja**: quantos pods do canary existem no passo 25%? (1 de 4. Sem *traffic router*, a % é a proporção de réplicas.)

**Explique**
1. O que as duas `AnalysisTemplate` perguntam? (`error-budget-available`: sobra > 20%? `canary-health`: só os pods canary, erro < 1%, p99 < 300 ms.)
2. Por que o gate é *fail-open* (`or vector(1)`)? (Um gate que trava por falta de dado vira motivo para ser desligado.)

**Checkpoint**: Rollout chega a 100% com análises `Successful`.

## S11 · Deploy ruim revertido e gate de budget (2 h)

**Faça** (passo 3 de `docs/fase-4-error-budget.md`)
1. **Escreva sua previsão** em `LICOES.md`: "o canary vai abortar em ~X minutos porque ...".
2. Introduza um defeito temporário no `bid-svc` (500 em parte das requisições, ou `time.sleep(0.5)`), push, merge do PR de tags.
3. Gere **lances** continuamente (a análise filtra `route="/api/bids"`), observe `make rollout-watch` e `kubectl -n aegis get analysisrun`.
4. Reverta o defeito (`git revert`).
5. O gate local:
   ```bash
   make prom-ui          # outro terminal
   make gate             # budget >= 20%: libera
   make gate FIX=1       # correção sempre passa
   ```

**Observe**: a `AnalysisRun` falha, o Rollout fica Degraded/Aborted e volta sozinho para a revisão estável.

**Explique**: como o canary decidiu? Qual a diferença entre canary e blue/green? (ADR 004.) Por que o job `budget-gate` do CI exige um runner self-hosted? (O GitHub não alcança o Prometheus do seu notebook.)

**Checkpoint**: evidência do rollback automático (saída do `rollout-watch` e do `get analysisrun`). **Registre** horário e números.

## S12 · KEDA e o Leilão Relâmpago (2 h)

**Leia**: `deploy/components/autoscaling/` (o `ScaledObject`) e `apps/loadgen/lightning.js`.

**Faça**
```bash
make enable PHASE=5
git diff && git add -A && git commit -m "feat: ativa fase 5" && git push
kubectl --context k3d-aegis-prod -n aegis get scaledobject,hpa        # READY=True
kubectl --context k3d-aegis-prod -n aegis get pods -l app=settlement-svc -w    # terminal 2
make lightning PEAK=300            # comece pequeno
```

**Preveja**: a fila cresce primeiro ou as réplicas sobem primeiro? Quanto tempo as réplicas demoram para voltar a 1 depois do pico? (~5 min: janela de scale-down do HPA, não o `cooldownPeriod` do KEDA.)

**Observe, no Prometheus, no mesmo intervalo**
```
max(aegis_settlement_pending_entries)
kube_deployment_status_replicas{deployment="settlement-svc"}
histogram_quantile(0.99, sum by (le) (rate(aegis_settlement_lag_seconds_bucket[1m])))
```
Use `max()` e **nunca** `sum()` no primeiro: cada réplica publica o mesmo valor global.

**Experimento extra**: repita com `min=max=1` no `ScaledObject` (num commit) e compare o p99 do lag. Esse "antes e depois" é o seu gráfico de entrevista.

**Explique**: por que escalar por fila e não por CPU? (Fila = causa/trabalho pendente; CPU = efeito/tardio. ADR 002.)

**Checkpoint**: thresholds do k6 passam, `duplicates_wrongly_accepted == 0`, e você narra a ordem: fila cresce → KEDA reage (≤ 5 s) → pods novos demoram a ficar Ready → fila drena.

---

# PARTE 3: Caos, segurança e DR (Fases 6, 7 e 8)

## S13 · Caos com método científico: primeiro experimento (2 h)

**Leia**: `chaos/catalog.yaml` (hipóteses escritas **antes**) e `tools/chaos_director.py`.

**Faça**
```bash
make enable PHASE=6
git diff && git add -A && git commit -m "feat: ativa fase 6" && git push
kubectl config use-context k3d-aegis-prod       # o Director usa o contexto atual
make prom-ui                                    # outro terminal
make chaos-list
```
**Antes de rodar**, escreva em `LICOES.md`: *"Eu espero que, ao matar 1 pod do bid, o erro fique abaixo de 1% porque ..."*. Só então:
```bash
make chaos ID=kill-bid-pod
ls postmortems/data/
make postmortem FILE=postmortems/data/<arquivo>.json
```

**Explique**: por que a hipótese vem antes? (Escrita depois, vira racionalização.) Por que hipótese refutada é a melhor descoberta?

**Checkpoint**: um JSON em `postmortems/data/`, um postmortem `.md`, e você diz se confirmou ou refutou, e por quê.

## S14 · Demais experimentos e postmortem (2 h)

Rode na ordem de "surpresa": `kill-settlement-pod`, `auction-svc-outage`, `bid-latency-500ms`, `bid-redis-partition`.

- `bid-latency-500ms` é o **controle**: o p99 *deve* estourar e o alerta de burn rate deve disparar (veja `Status → Rules`). Se não disparar, o SLO está cego.
- Após `kill-settlement-pod`, confira que não houve perda nem duplicidade:
  ```bash
  kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli xlen bids
  kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli xpending bids settlement
  kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli --scan --pattern 'processed:*' | wc -l
  ```

**Explique**: qual hipótese foi refutada e o que você mudou? Essa é a pergunta que separa projetos de portfólio.

**Checkpoint**: um veredito por experimento, com números.

## S15 · Kyverno em modo Audit (1,5 h)

**Leia**: `platform/policies/` (`verify-signed-images.yaml`, `disallow-root.yaml`, `require-limits-labels.yaml`).

**Faça**
```bash
make enable PHASE=7
git diff && git add -A && git commit -m "feat: ativa fase 7" && git push
kubectl --context k3d-aegis-prod get policyreport -n aegis -o wide
```
**Preveja**: as imagens do seu CI aparecem `pass` na verificação de assinatura? (Sim, se owner, repo, `ci.yml` e branch `main` baterem com a identidade da assinatura.)

**Explique**: por que `Audit` primeiro, `Enforce` depois? (Ligar `Enforce` direto derruba deploys legítimos; `Audit` mostra o impacto antes.)

**Checkpoint**: `policyreport` sem `fail` para a aplicação.

## S16 · Enforce, NetworkPolicy e provas de bloqueio (2 h)

**Faça**
1. Em `platform/policies/*.yaml` troque `failureAction: Audit` por `Enforce`; commit, push, espere o Argo aplicar.
2. A prova:
   ```bash
   kubectl --context k3d-aegis-prod -n aegis run intruso --image=ghcr.io/carlosleosouza/aegis-bid/bid:dev-nao-assinada
   kubectl --context k3d-aegis-prod -n aegis run root-test --image=nginx --restart=Never
   ```
   **Preveja**: os dois são rejeitados? (Sim, o webhook do Kyverno recusa com a mensagem da política.) Guarde a saída.
3. Isolamento de rede:
   ```bash
   kubectl --context k3d-aegis-prod -n aegis run np-test --rm -it --image=busybox:1.36 --restart=Never -- \
     sh -c 'nc -zv -w 2 redis 6379; echo exit=$?'
   ```
   **Preveja**: conecta? (Não: o pod não tem os labels permitidos.)

**Armadilha**: com `Enforce`, uma tag ainda não assinada nos overlays deixa o app `Degraded`. Isso é a política funcionando; corrija a causa, não a política.

**Checkpoint**: imagem não assinada e container root rejeitados; Redis inacessível ao pod sem permissão.

## S17 · Replicação do Redis e RPO (1,5 h)

**Leia**: `deploy/components/redis-expose/` e `deploy/components/dr-replication/`.

**Faça**
```bash
make enable PHASE=8
git diff && git add -A && git commit -m "feat: ativa fase 8" && git push
kubectl --context k3d-aegis-dr -n aegis exec deploy/redis -- redis-cli info replication | head -12
# esperado: role:slave, master_link_status:up
```
Crie um leilão pela borda e confirme que ele aparece no DR (`redis-cli dbsize` no Redis do DR).

**Armadilha conhecida**: as NetworkPolicies da Fase 7 (`default-deny`) podem bloquear a replicação (egress do DR, ingress do prod). Se `master_link_status:down`, acrescente as regras e registre em `LICOES.md`: é um achado de entrevista legítimo, causado pela interação entre duas fases.

**Explique**: o que é RPO e por que a replicação assíncrona nunca dá RPO = 0?

**Checkpoint**: `master_link_status:up` e o dado do leilão visível no DR.

## S18 · Failover, RTO e failback (2 h)

**Preveja**: quantos segundos entre derrubar o prod e o primeiro 200 pela borda? Escreva o número.

**Faça**
```bash
make lightning PEAK=200      # opcional, em outro terminal: dá RPO interessante
make failover
```
Anote o **RTO** e o **RPO** que o script imprime. Depois:
```bash
curl -s -H 'Host: aegis.localhost' http://localhost:8080/api/auctions     # o que foi replicado
make restore-prod
```

**Explique**
1. Por que o Argo CD ficou fora do ar durante o failover e o que isso significa? (O DR continua rodando, mas não recebe mudanças.)
2. Por que o failback não é só `restore-prod`? (O prod volta com dados antigos; sem sincronizar DR → prod há split-brain/perda de escrita.)
3. Pilot light vs warm standby: o trade-off de custo × RTO.

**Checkpoint**: RTO e RPO medidos e anotados; API do DR aceitando lances.

## S19 · Fechamento: números, README e ensaio (3 h)

1. Preencha uma tabela com **seus** números: tempo do `make up`, tempo do canary, p99 antes/depois do KEDA, veredito de cada experimento, RTO e RPO.
2. Revise `docs/LICOES.md`: escolha 3 problemas reais que você enfrentou (porta ocupada, PR bloqueado pelo GitHub, lint no CI, `ImagePullBackOff`...). Cada um é uma resposta pronta para "me conte de um problema".
3. Ensaie, em voz alta e cronometrando, as perguntas de `docs/ENTREVISTA.md`.
4. Desenhe de memória o mapa do início deste arquivo.

**Checkpoint final**: você consegue contar em 5 minutos, sem consultar: (a) o caminho de uma mudança do commit ao pod; (b) o caminho de um lance e por que nunca é aplicado duas vezes; (c) uma falha que você provocou e o que aprendeu.

---

## Se o tempo for curto (trilha essencial)

S00 a S11 (Fases 0 a 4, ~21 h). Cobre GitOps, SLO, canary e gate de budget, que é o coração da história do projeto.
