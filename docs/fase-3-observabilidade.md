# Fase 3: Observabilidade e SLOs

## Objetivo
Medir o que o usuário sente (SLIs), declarar metas (SLOs) e alertar pela **velocidade de consumo do orçamento de erro** (burn rate), não por thresholds arbitrários. Entrega: dashboard **"Saúde do Leilão"** e alertas multi-janela.

## Por quê
- Sem SLO, "está bom?" vira opinião. Com SLO, vira número, e o **error budget** transforma confiabilidade em *decisão de negócio* (Fase 4: o budget decide se o deploy segue).
- **Burn rate** em vez de "CPU > 80%": alerta pelo sintoma real do usuário e com urgência proporcional (queimar 2% do budget em 1 hora acorda alguém; queimar 10% em 3 dias abre um ticket).
- **Regras geradas**: cada SLO exige ~20 regras iguais em fórmula; à mão, um typo vira um alerta que nunca dispara. A fonte da verdade é `platform/observability/slos.yaml`.

## Os 3 SLOs
| SLO | Meta | SLI (fração de eventos *ruins*) |
|---|---|---|
| `bid-availability` | 99,9% | lances (`POST /api/bids`) com 5xx |
| `bid-latency` | 99% em ≤ 300 ms | lances acima do bucket `le="0.3"` |
| `settlement-freshness` | 99% liquidados em ≤ 2 s | lances com `aegis_settlement_lag_seconds` > 2 s |

O bucket `0.3` existe no histograma de `apps/common/aegis_common/metrics.py` **de propósito**: se não fosse um limite de bucket, o SLI de latência ficaria impreciso. Esse arquivo é um *contrato*: renomear uma métrica quebra as regras de SLO (há um teste que cruza PromQL × código).

## Passos

### 1. Ligue a fase
```bash
make enable PHASE=3
git diff && git add -A && git commit -m "feat: ativa fase 3 (observabilidade)" && git push
```
Ativa o `kube-prometheus-stack` (só no prod), as regras/dashboard e o `ServiceMonitor` (componente `monitoring` do overlay prod).

### 2. Entenda os valores do chart (leia o YAML)
Em `platform/catalog/fase3-kube-prometheus-stack.yaml`:
- `*SelectorNilUsesHelmValues: false`: sem isso o Prometheus **ignora** ServiceMonitors e regras que não carregam o label do release Helm.
- `ServerSideApply=true`: os CRDs do chart são grandes demais para o `apply` comum.
- `kubeEtcd/Scheduler/...: false`: no k3s esses componentes não são expostos como no Kubernetes padrão; ligados, só gerariam alertas falsos.
- `honorLabels: true` no ServiceMonitor: o label `service` vem *dentro* da métrica; sem isso viraria `exported_service` e as consultas de SLO deixariam de casar.

### 3. Gere tráfego e olhe as métricas
```bash
make prom-ui        # http://localhost:9090  (em um terminal)
# em outro terminal, gere carga leve:
for i in $(seq 1 200); do curl -s -o /dev/null -H 'Host: aegis.localhost' http://localhost:8080/api/auctions; done
```
No Prometheus:
- `Status → Targets`: os 3 serviços *UP*.
- `sum(rate(aegis_http_requests_total[1m])) by (service, code)`
- `aegis:error_budget_remaining:ratio` (aparece após alguns minutos de dados)

### 4. Abra o dashboard
```bash
make grafana-ui     # http://localhost:3000   usuário: admin   senha: aegis-lab (LAB ONLY)
```
Procure **AEGIS-Bid: Saúde do Leilão**. Painéis: budget restante (30 dias), burn rate da disponibilidade, lances por segundo (por desfecho), latência p50/p95/p99, taxa de erro 5xx, fila × réplicas × frescor (a história do KEDA, Fase 5), tráfego por versão do bid-svc (canary visível, Fase 4), reinícios de containers e duplicatas suprimidas pelo settlement.

### 5. Altere um SLO (o ciclo completo)
Edite o `objective` de um SLO em `platform/observability/slos.yaml` e rode:
```bash
make slo-rules && git diff platform/observability    # veja as regras recalculadas
```
O CI **falha** se você editar o YAML e esquecer de regenerar (job `test`).

## Verificação
- Targets *UP*, painéis com dados, `aegis:error_budget_remaining:ratio{slo="bid-availability"}` ≈ 1 em ambiente saudável.
- Alertas em `Status → Rules` (Prometheus) como `AegisErrorBudgetFastBurn`. *Não há receptor configurado no Alertmanager*: ver o alerta *firing* na UI é suficiente para o aprendizado.

## Desafios (para aprofundar)
1. **SLO 4 de correção** (0 lances duplicados aplicados): exponha o contador no settlement (o resultado `duplicate` já existe em `aegis_settlement_processed_total`) e discuta *por que* ele não é um SLO de taxa comum.
2. Troque o gerador por **Sloth** ou **Pyrra** e compare a saída com a de `tools/slo_gen.py`.
3. Adicione **traces** com OpenTelemetry (Tempo + Grafana) e correlacione métrica → log → trace. Não está implementado: é a extensão natural desta fase.

## Pergunta de entrevista
*"Por que alertar por burn rate e não por threshold?"* Porque o threshold ignora o contexto: 1% de erro por 2 minutos e 1% por 3 dias têm impactos completamente diferentes no budget. Multi-janela (longa + curta) evita alertas por soluços e faz o alerta *limpar rápido* quando o problema acaba. Referência: Google SRE Workbook, cap. 5.
