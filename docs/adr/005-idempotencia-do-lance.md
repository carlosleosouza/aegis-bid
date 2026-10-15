# ADR 005: Onde garantir a idempotência do lance (API, fila ou banco)?

**Status**: aceita · **Contexto**: lance duplicado significa prejuízo. Clientes fazem *retry* após timeout; consumidores morrem no meio do processamento; filas entregam *pelo menos uma vez*.

## O problema
Não existe "exatamente uma vez" de ponta a ponta numa rede que falha. O que existe é **entrega at-least-once + efeito idempotente**. A pergunta é *onde* colocar a barreira que garante o efeito único.

## Decisão: defesa em três camadas, com a garantia real na última
1. **API (`bid-svc`)**: header `Idempotency-Key` obrigatório. A primeira vez grava `idem:{bid_id}` com `SET NX` (TTL 24 h) e publica no stream; repetir a chave devolve `duplicate: true` **sem republicar**. É uma *otimização* de resposta rápida, não a garantia.
2. **Stream (Redis Streams)**: at-least-once. Um lance **pode** aparecer duas vezes (retry do cliente depois de um `XADD` que deu certo mas cujo ack se perdeu; redelivery após crash do consumidor).
3. **Settlement**: aplica cada `bid_id` **uma única vez** com um script **Lua** atômico (`SET processed:{bid_id} NX` + comparação do maior lance). Se a chave já existe, é duplicata e nada é aplicado. **Esta camada é a garantia.**

### O detalhe que fecha o buraco: `bid_id` determinístico
`bid_id = sha256(bidder + ":" + Idempotency-Key)`. Assim, mesmo que a camada 1 falhe (timeout *depois* do `XADD`; a chave de idempotência é liberada para o cliente tentar de novo), o retry gera o **mesmo** `bid_id` e a camada 3 descarta a duplicata. Um `bid_id` aleatório quebraria isso.

### Por que `XACK` só depois de aplicar
Se o pod morrer entre o Lua e o `XACK`, a mensagem é reentregue (ou reivindicada por `XAUTOCLAIM`) e o Lua responde `duplicate`: nada é aplicado duas vezes. A ordem inversa (ack antes) *perderia* lances.

## Alternativas
| Onde | A favor | Contra |
|---|---|---|
| Só na API | barato | não cobre redelivery da fila nem crash do consumidor |
| Só no consumidor | cobre tudo | resposta ao cliente não distingue duplicata; carga desnecessária na fila |
| Banco com constraint única | forte e familiar | exige banco transacional no caminho crítico; aqui o estado está no Redis |
| Transação distribuída / exactly-once do broker | semântica forte | complexidade e custo desproporcionais |

## Consequências
- Os testes (`apps/tests/test_flow.py`) cobrem: replay com a mesma chave (publica uma vez), redelivery (aplica uma vez), consumidor que morre (mensagem reivindicada e aplicada uma vez), mensagem venenosa (DLQ).
- Fechamento do leilão tem um **período de graça** (5 s): se a fila atrasar mais que isso, um lance válido pode ser rejeitado. Em produção, condicione o fechamento ao lag do consumer group.
- Redis compartilhado entre serviços é um atalho consciente (ADR 002).
- **SLO de correção** (desafio da Fase 3): "0 duplicatas aplicadas". Diferente dos SLOs de taxa, ele é uma *invariante*: qualquer violação é um incidente.

## Resposta curta para entrevista
*"Como evitar lance duplicado em sistema distribuído?"* Aceitando que a entrega é at-least-once e tornando o **efeito** idempotente: chave de idempotência na borda para responder rápido, `bid_id` determinístico para que retries colidam, e uma operação atômica (Lua) no ponto onde o efeito acontece.
