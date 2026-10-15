# ADR 002: Redis Streams (e não NATS JetStream nem Kafka)

**Status**: aceita · **Contexto**: o `bid-svc` precisa entregar lances ao `settlement-svc` de forma durável, com vários consumidores e redelivery em caso de crash.

## Decisão
Usar **Redis Streams** com *consumer group* (`settlement`), `XACK` após processar e `XAUTOCLAIM` para reivindicar mensagens de consumidores mortos.

## Por quê
- O Redis **já existe** no projeto (estado dos leilões): zero componentes novos para operar num notebook.
- Entrega **at-least-once** com *Pending Entries List*: ensina exatamente os conceitos que importam (redelivery, idempotência, DLQ).
- O KEDA tem scaler nativo para Redis Streams: escala pelo *lag* do grupo.
- RPO/DR: a replicação do Redis cobre fila e estado com o mesmo mecanismo.

## Alternativas
| Opção | A favor | Contra (aqui) |
|---|---|---|
| **NATS JetStream** | leve, ótimo modelo de consumidores, boa semântica | mais um componente; o ganho é pequeno nesta escala |
| **Kafka** | throughput altíssimo, retenção/replay, ecossistema | pesado demais para um notebook (brokers, KRaft, memória); complexidade operacional desproporcional ao problema |

**Por que *não* Kafka?** O gargalo deste sistema não é throughput de log, é **correção sob concorrência** (lance duplicado, ordem do maior lance). Kafka resolveria um problema que não temos e adicionaria um que não queremos.

## Consequências / limites assumidos
- Redis compartilhado entre serviços é um **atalho consciente** (acoplamento de dados). Em produção, cada serviço teria seu armazenamento e a fila seria um componente à parte.
- Durabilidade do Redis (AOF) e replicação assíncrona significam RPO > 0 (veja Fase 8).
- Sem partições nem ordem global entre consumidores: por isso o maior lance é decidido por comparação atômica (Lua), não pela ordem de chegada.

## Quando eu mudaria
Replay histórico longo, múltiplos consumidores independentes lendo o mesmo log, ou volumes que excedam a memória do Redis → Kafka (ou Redpanda). Menos infraestrutura com semântica parecida → NATS JetStream.
