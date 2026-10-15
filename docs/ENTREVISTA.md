# Roteiro de entrevista

## O pitch (30 segundos)
> "Construí uma plataforma de leilões em tempo real onde **o pipeline decide se um deploy pode seguir com base no orçamento de erro do serviço**. Um canary mede cada versão contra os SLOs e reverte sozinho se piorar. Um *Chaos Director* roda experimentos com hipótese escrita antes e gera o postmortem em Markdown. Tudo é GitOps em dois clusters, com imagens assinadas verificadas pelo cluster e failover com RTO e RPO que eu **medi**."

## Roteiro de 10 minutos
| Tempo | O que mostrar | Dica |
|---|---|---|
| 1 min | Problema e pitch | Diga *por que leilão*: picos nos últimos segundos, lance duplicado = prejuízo, latência importa |
| 2 min | Diagrama (README) | Dois clusters, Argo CD hub, borda HAProxy, fluxo bid → stream → settlement |
| 5 min | **Demo** (3 cenas abaixo) | Grave um vídeo de 3 min como plano B |
| 2 min | O que mudaria em produção | Use a seção "Limitações" abaixo: mostra maturidade |

### As 3 cenas da demo
1. **Self-heal + GitOps**: apague um `Service` à mão e veja o Argo CD recriar; depois mude um valor por PR.
2. **Deploy ruim revertido sozinho**: `make rollout-watch` com um defeito proposital; mostre a `AnalysisRun` falhando e o rollback (Fase 4).
3. **Leilão Relâmpago**: `make lightning` com o painel *Fila × réplicas × frescor* aberto (Fase 5). Se der tempo, `make failover` com o RTO.

## Perguntas que você vai conseguir responder (com o seu material)
| Pergunta | Onde está a base |
|---|---|
| Como evitar lance duplicado em sistema distribuído? | [ADR 005](adr/005-idempotencia-do-lance.md) |
| Como definir SLO e por que burn rate e não threshold? | [Fase 3](fase-3-observabilidade.md) |
| Como o canary com análise automática decide? | [Fase 4](fase-4-error-budget.md), [ADR 004](adr/004-canary-vs-blue-green.md) |
| Como você garante que só código confiável roda no cluster? | [Fase 1](fase-1-app-ci.md) + [Fase 7](fase-7-seguranca.md) |
| Como mediu RTO/RPO do failover? | [Fase 8](fase-8-dr-postmortem.md) |
| Por que Argo CD? E o risco do hub? | [ADR 001](adr/001-argocd-vs-flux.md) |
| Por que Redis Streams e não Kafka? | [ADR 002](adr/002-redis-streams-vs-nats-vs-kafka.md) |
| Por que escalar por fila e não por CPU? | [Fase 5](fase-5-autoscaling.md) |
| Qual experimento refutou sua hipótese? | **o seu** `docs/LICOES.md` |

## "O que você faria diferente?" (prepare 3 respostas)
Escolha as que forem verdadeiras para você depois de executar:
1. **Traffic routing real** (service mesh ou ingress com peso) no lugar de canary por proporção de réplicas, e **mTLS** entre serviços.
2. **Armazenamento por serviço** e uma fila dedicada, em vez do Redis compartilhado; **replicação síncrona** ou multi-região real para reduzir o RPO.
3. **SLO por cliente/leilão** e SLO de **correção** (0 duplicatas) como invariante com alerta imediato.
4. **Failback automatizado** com sincronização reversa e proteção contra split-brain.
5. **Segredos** em um gerenciador (External Secrets + Vault/OpenBao) em vez de valores de laboratório.
6. **Traces** (OpenTelemetry + Tempo) correlacionados às métricas e logs.

## Limitações assumidas (diga você mesmo, antes de perguntarem)
- Laboratório local: Redis sem senha/TLS, replicação em NodePort aberta, TLS do DR desligado para o Argo CD, senha fixa no Grafana, tudo marcado como `LAB ONLY`.
- O gate de CI roda localmente (o GitHub não alcança o Prometheus do notebook).
- Hub do GitOps é ponto único de falha da *entrega*.
- Não implementados: OpenTelemetry/traces, OpenCost/FinOps, Crossplane, failback automático.

## Checklist de "pronto para apresentar"
- [ ] `make down && make up` funciona do zero (tempo anotado)
- [ ] Nenhum `kubectl apply` manual no fluxo normal (só o `root-app` no bootstrap)
- [ ] 3 SLOs com alertas de burn rate e o dashboard mostrando dados
- [ ] Um rollback automático **documentado** (log/print da `AnalysisRun`)
- [ ] Um experimento de caos com hipótese, veredito e postmortem gerado
- [ ] Failover medido (RTO e RPO anotados)
- [ ] Imagens assinadas e política do Kyverno em `Enforce`
- [ ] `docs/LICOES.md` com erros **reais** seus
- [ ] Vídeo de 3 minutos como plano B

## Como contar no currículo/LinkedIn (modelo; ajuste aos seus números reais)
- *Projeto AEGIS-Bid (plataforma de leilões): GitOps multi-cluster com Argo CD, entrega progressiva com Argo Rollouts e **gate de error budget**; SLOs com alertas de burn rate (Prometheus/Grafana); autoscaling por fila (KEDA); chaos engineering com Chaos Mesh e postmortem automatizado; supply chain com Trivy, Syft e Cosign verificados por Kyverno; DR com RTO de **X s** e RPO de **Y** medidos.*
