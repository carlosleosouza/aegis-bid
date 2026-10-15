# Fase 4: Entrega progressiva e Error Budget Gate

## Objetivo
Uma versão ruim do `bid-svc` é **revertida sozinha** durante o canary, e um serviço sem budget **não recebe feature nova**. Entrega: o rollback automático documentado (com prints/log).

## Por quê
Deploy é a maior causa de incidentes. Em vez de confiar em revisão humana, a plataforma *mede* o canary contra o SLO e decide. O **Error Budget Gate** fecha o ciclo SRE: budget sobrando → pode arriscar; budget acabando → só correção.

## Como funciona
- `bid-svc` é um **Rollout** (Argo Rollouts) com 4 réplicas e passos `25% → pausa 60 s → 50% → pausa 60 s`. Sem *traffic router*, a porcentagem é aproximada pela proporção de réplicas (1 pod ≈ 25%).
- O componente `canary-analysis` adiciona duas `AnalysisTemplate`:
  - `error-budget-available`: o budget restante do SLO de disponibilidade é > 20%? (`or vector(1)`: sem dados, o portão **abre**. *Fail-open* proposital, senão o gate vira motivo para ser desligado.)
  - `canary-health`: avalia **só os pods canary** (filtrados por `rollouts_pod_template_hash`, label copiado pelo `ServiceMonitor`): erro 5xx < 1%, p99 < 300 ms e burn rate do serviço inteiro < 14,4x.
- **Gate no CI** (`tools/budget_gate.py`): PR de feature é bloqueado se o budget < 20%; PR de correção (`--is-fix`) passa.

## Passos

### 1. Ligue a fase
```bash
make enable PHASE=4
git diff && git add -A && git commit -m "feat: ativa fase 4 (canary + budget gate)" && git push
```
Instale o plugin para ver o rollout (opcional, mas muito didático): https://argo-rollouts.readthedocs.io/en/stable/installation/#kubectl-plugin-installation

### 2. Deploy saudável
Faça uma mudança trivial na app (ex.: um texto de resposta), faça push na `main`, deixe o CI abrir o PR de tags e **mescle**. Acompanhe:
```bash
make rollout-watch
```
**Esperado**: passos 25% → 50% → 100%, análises `Successful`, nova revisão *Healthy*. Com pouco tráfego o p99 vem `NaN` (tratado como "ainda sem dados": não reprova); gere carga em outro terminal para exercitar de verdade:
```bash
for i in $(seq 1 600); do curl -s -o /dev/null -H 'Host: aegis.localhost' http://localhost:8080/api/auctions; done
```

### 3. Deploy RUIM (a prova)
Introduza um defeito visível ao SLI do canary. Opções (escolha uma, faça push, mescle o PR de tags e gere tráfego de **lances**, pois a análise filtra `route="/api/bids"`):
- Faça o `bid-svc` devolver 500 em parte das requisições (ex.: um `if random() < 0.5: raise HTTPException(500)` temporário em `apps/bid/main.py`).
- Ou adicione um `time.sleep(0.5)` no handler para estourar o p99.

Gere lances continuamente (precisa de um leilão aberto; veja a Fase 1 para os comandos) enquanto observa `make rollout-watch`.
**Esperado**: a `AnalysisRun` falha, o Rollout fica **Degraded/Aborted** e volta para a revisão estável. Registre o horário e os números em `docs/LICOES.md`.
Depois **reverta o defeito** (`git revert`).

### 4. O gate de CI (local)
```bash
make prom-ui                       # outro terminal
make gate                          # budget >= 20%: liberado
make gate FIX=1                    # PR de correção sempre passa
```
Para ver o bloqueio, consuma budget (deploy ruim da etapa 3, ou o experimento de latência da Fase 6) e rode `make gate` de novo.
O job `budget-gate` do workflow só roda se existir a variável de repositório `PROMETHEUS_URL` **e** um runner self-hosted com acesso ao Prometheus: o GitHub não alcança o Prometheus do seu notebook. Isso é uma limitação real do laboratório: diga isso na entrevista.

## Verificação
- Deploy saudável promovido; deploy ruim abortado automaticamente (evidência: saída do `rollout-watch` + `kubectl -n aegis get analysisrun`).
- `make gate` retorna 0 com budget alto e ≠ 0 para feature com budget baixo.

## Se der errado
| Sintoma | Causa provável |
|---|---|
| Análise `Error`/sem dados | Prometheus não está coletando (`Status → Targets`), ou o label `rollouts_pod_template_hash` não chegou (confira `podTargetLabels` no ServiceMonitor) |
| Rollout nunca falha com defeito | falta tráfego em `/api/bids`; a query só considera essa rota |
| Rollout travado em *Paused* | passo `pause` com duração (60 s) é normal; sem duração exigiria `promote` manual |

## Pergunta de entrevista
*"Como o canary decide?"* Consultas PromQL com condição de sucesso/falha por métrica e `failureLimit`; se falhar, o controlador aborta e escala a revisão estável. *Canary vs blue/green*: [ADR 004](adr/004-canary-vs-blue-green.md).
