# Fase 5: Autoscaling por evento e o Leilão Relâmpago

## Objetivo
Ver o `settlement-svc` escalar **pelo tamanho da fila** durante a corrida dos últimos segundos de um leilão. Entrega: um gráfico que mostra fila, réplicas e latência de liquidação juntos.

## Por quê
CPU é um sintoma *tardio*: a fila crescendo é a *causa*. Quando há atraso, já precisamos de mais consumidores, mesmo com CPU baixa. O HPA clássico não enxerga isso; o **KEDA** escala por métricas externas (aqui, o *lag* do consumer group do Redis Streams). A fila também **desacopla** produtor e consumidor: a API de lances continua rápida enquanto o settlement "alcança".

## Passos

### 1. Pré-requisito
`make test` verde e fases 2 a 4 funcionando. Instale o k6 (https://k6.io/docs/get-started/installation/).

### 2. Ligue a fase
```bash
make enable PHASE=5
git diff && git add -A && git commit -m "feat: ativa fase 5 (keda)" && git push
```
Instala o KEDA e o componente `autoscaling` (`ScaledObject` do settlement: 1 a 10 réplicas, ~1 réplica para cada 100 mensagens em atraso, `pollingInterval: 5`).

> **Sobre o `cooldownPeriod: 60`**: no KEDA ele só vale para escala **a zero**. Com `minReplicaCount: 1`, a descida de N para 1 é feita pelo HPA que o KEDA cria, cuja janela de estabilização de scale-down é, por padrão no Kubernetes, de 300 s. Ou seja: espere ~5 min depois do pico para ver as réplicas voltarem (confira com `kubectl -n aegis get hpa -o yaml`, campo `behavior`). O estudo aprofundado explica como encurtar isso.

> O scaler `redis-streams` do KEDA tem parâmetros que mudaram entre versões. Se o `ScaledObject` não ficar `READY`, confira `kubectl -n aegis describe scaledobject settlement-svc` e a documentação do scaler para a versão que o seu cluster instalou.

### 3. Observe antes de carregar
```bash
kubectl --context k3d-aegis-prod -n aegis get scaledobject,hpa
kubectl --context k3d-aegis-prod -n aegis get pods -l app=settlement-svc -w     # outro terminal
make prom-ui                                                                    # outro terminal
```

### 4. Dispare o Leilão Relâmpago
```bash
make lightning PEAK=300         # comece pequeno; aumente (500, 1000...) se o seu notebook aguentar
```
Perfil do teste (`apps/loadgen/lightning.js`): 20 s de aquecimento (50 req/s) → rampa para o pico em 10 s → sustenta 30 s → desce. Cada VU manda lances únicos, e 10% reenviam o *mesmo* lance (replay) para provar a idempotência sob carga.

> **Cuidado com o gerador de carga**: o k6 também consome CPU. Se ele for o gargalo, o teste mente. Comece com 200-500 req/s; 5.000 exige máquina folgada.

### 5. Leia o resultado
O k6 termina com os `thresholds` (que são os próprios SLOs): `http_req_failed < 0,1%`, `p(99) < 300 ms` e `duplicates_wrongly_accepted == 0`. Em paralelo, no Grafana o painel **Fila × réplicas × frescor** já junta isso. No Prometheus, compare no mesmo intervalo:
- `aegis_settlement_pending_entries` (fila pendente)
- `kube_deployment_status_replicas{deployment="settlement-svc"}` (réplicas)
- `histogram_quantile(0.99, sum by (le) (rate(aegis_settlement_lag_seconds_bucket[1m])))` (atraso de liquidação)
- `aegis:error_budget_remaining:ratio` (quanto o teste custou em budget)

## Verificação
- Réplicas do settlement sobem durante o pico e voltam a 1 depois da janela de estabilização do HPA (~5 min por padrão; veja a nota acima).
- Thresholds do k6 passam; `duplicates_wrongly_accepted` = 0.
- Você consegue explicar, olhando o gráfico, **a ordem dos eventos**: fila cresce → KEDA reage (até ~5 s de polling) → pods novos levam tempo para ficar *Ready* → fila drena.

## Experimento extra (ótimo para entrevista)
Rode de novo **com o autoscaling desligado** (escale o `ScaledObject` para `min=max=1` num commit) e compare o p99 do lag de liquidação. Esse "antes e depois" vale mais do que qualquer descrição.

## Se der errado
| Sintoma | Causa provável |
|---|---|
| `curl: aegis.localhost` não resolve | adicione `127.0.0.1 aegis.localhost` ao `/etc/hosts` |
| k6 reporta muitos 503 | Redis/bid saturados ou notebook sem recursos: reduza `PEAK` e veja `docker stats` |
| KEDA não escala | `ScaledObject` não `READY` (nota acima); confira o endereço `redis.aegis.svc.cluster.local:6379` e o nome do consumer group `settlement` |
| Réplicas sobem e descem toda hora | ajuste `advanced.horizontalPodAutoscalerConfig.behavior` (janela de estabilização do HPA); o `cooldownPeriod` não atua com `minReplicaCount: 1` |

## Pergunta de entrevista
*"Por que escalar por fila e não por CPU?"* A fila mede o trabalho *pendente* (causa); a CPU mede o esforço *atual* (efeito). Com consumidores I/O-bound, a CPU pode estar baixa e o atraso alto. Para o trade-off de infraestrutura de fila, veja [ADR 002](adr/002-redis-streams-vs-nats-vs-kafka.md).
