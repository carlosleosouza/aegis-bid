# 02 · A aplicação por dentro

Três serviços FastAPI (`auction`, `bid`, `settlement`) conversando por um Redis. Este arquivo explica cada um, o modelo de dados, os testes e traz um laboratório de mutações. Todas as saídas marcadas como **capturada** vêm de uma execução real (Redis real e os três serviços rodando) feita durante a revisão.

## 1. Modelo de dados no Redis

Redis guarda tudo: estado dos leilões, fila, idempotência. É um atalho consciente (ADR 002).

| Chave | Tipo | Quem escreve | Para quê | TTL |
|---|---|---|---|---|
| `auction:{id}` | hash | auction (cria), settlement (atualiza maior lance e fecha) | estado do leilão: `title`, `starting_price`, `ends_at_ms`, `status`, `highest_amount`, `highest_bidder`, `highest_bid_id` | não |
| `auctions` | set | auction | índice de ids para listar | não |
| `idem:{bid_id}` | string | bid | camada 1: "já vi esta chave de idempotência" | 24 h |
| `bids` | stream | bid (`XADD`) | a fila de lances (aprox. 1.000.000 de entradas no máximo) | não |
| `bids.dlq` | stream | settlement | mensagens inválidas ("venenosas") para análise humana | não |
| `processed:{bid_id}` | string | settlement (dentro do Lua) | camada 3: "já apliquei este lance" | 24 h |
| grupo `settlement` | consumer group do stream `bids` | settlement | divide o trabalho entre réplicas e guarda o que está pendente | n/a |

**Saída capturada** (depois de 2 lances aceitos e liquidados num leilão):

```
chaves: auction:8cd8b6d5756a auctions bids idem:2ec0... idem:cecc... processed:2ec0... processed:cecc...
XLEN bids: 2
XPENDING: 0
hash do leilão:
  title Relogio | starting_price 1000 | ends_at_ms 1791975941708
  status open | highest_amount 1500 | highest_bidder ana | highest_bid_id 2ec0fbed...
TTL idem: 86398
```

Observe que cada lance deixa **duas** marcas: `idem:*` (feita pela API) e `processed:*` (feita pelo settlement). São camadas diferentes, com propósitos diferentes.

## 2. `common/aegis_common`: o contrato de métricas e o cliente Redis

### 2.1 `metrics.py`: o contrato que sustenta os SLOs

Duas métricas, e o resto do projeto depende dos nomes e labels delas:

- `aegis_http_requests_total{service, route, method, code}`: **Counter**, só sobe.
- `aegis_http_request_duration_seconds_bucket{service, route, le}`: **Histogram**, com os buckets `0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5`.

Decisões de projeto e o porquê:

| Decisão | Por quê | O que quebraria |
|---|---|---|
| O bucket `0.3` existe de propósito | o SLO é "99% em até 300 ms"; o SLI é a fração de requisições com duração `<= 0.3`. Sem um limite de bucket em 0.3 o cálculo seria impreciso | SLI de latência errado, em silêncio |
| `route` usa o **template** (`/api/auctions/{auction_id}`), nunca o caminho real | caminho real = um valor por leilão = cardinalidade infinita = Prometheus morrendo | explosão de séries |
| `/metrics`, `/healthz`, `/readyz` ficam fora das métricas | probes e scrapes não são tráfego de usuário; distorceriam a disponibilidade | disponibilidade inflada |
| Se a requisição levanta exceção, o código vira `"500"` | uma falha não tratada precisa contar como 5xx | erros invisíveis |
| 4xx **não** são erros do SLI | só 5xx é falha do serviço; um 404 ou 409 é o cliente errando. Veja o SLO em `04-...md` | tratar 409 como erro derrubaria a disponibilidade a cada leilão encerrado |

**Saída capturada** das linhas do histograma (28 lances; repare que os buckets são **cumulativos**: `le="0.025"` já inclui os do `le="0.01"`):

```
..._bucket{le="0.01", route="/api/bids"} 27.0
..._bucket{le="0.025",route="/api/bids"} 28.0
..._bucket{le="0.3",  route="/api/bids"} 28.0
..._bucket{le="+Inf", route="/api/bids"} 28.0
..._count{route="/api/bids"} 28.0
..._sum{route="/api/bids"}   0.0599
```

`_count` é o total de observações; `_sum` é a soma das durações (média = sum/count); `+Inf` sempre iguala `_count`.

### 2.2 `redis_client.py`: por que o pool é "bloqueante"

`make_redis()` usa `redis.BlockingConnectionPool` com `max_connections=64` (configurável por `REDIS_MAX_CONNECTIONS`) e `timeout=1.0` de espera por conexão.

- O pool **padrão** do redis-py, ao estourar o limite, levanta `MaxConnectionsError` na hora. Sob a rajada do Leilão Relâmpago isso vira 503 para lances **válidos**.
- O pool **bloqueante** faz a requisição *esperar* uma conexão livre (backpressure) e só falha depois de 1 s.
- Os timeouts de socket de 1 s são curtos de propósito: sob caos de rede (experimento de partição, Fase 6) é melhor falhar rápido com 503 do que pendurar threads.
- O comentário do arquivo registra o caso real: 200 retries simultâneos da mesma chave geravam 37 respostas 503 com o pool padrão.

## 3. `auction-svc`: cria e consulta leilões

Endpoints:

| Método e rota | O que faz | Códigos |
|---|---|---|
| `POST /api/auctions` | cria o leilão. Corpo: `title` (1 a 120), `starting_price` (>= 0, em centavos), `duration_seconds` (5 a 86400) | 201; 422 se inválido |
| `GET /api/auctions/{id}` | devolve a visão do leilão | 200; 404 |
| `GET /api/auctions?limit=20` | lista (máx. 100, ordenada por id) | 200 |
| `GET /healthz`, `GET /readyz` | probes. `readyz` faz `PING` no Redis e devolve 503 se falhar | 200 ou 503 |
| `GET /metrics` | métricas Prometheus | 200 |

Pontos para entender:

- O **preço inicial funciona como "maior lance zero"**: `highest_amount` nasce igual a `starting_price`, então o primeiro lance precisa superá-lo.
- O auction-svc **só cria e lê**. Quem atualiza o maior lance e fecha o leilão é o settlement.
- O status tem três estados na visão, mas só dois no Redis:

```
        (no Redis)                      (só na visão HTTP)               (no Redis)
   status = "open"  ──relógio passa de ends_at_ms──►  "closing"  ──settlement fecha──►  status = "closed"
                                                                                       (+ campo "winner" na visão)
```

`closing` é **derivado** em `_view()`: o relógio acabou, mas o settlement ainda não fechou oficialmente (ele espera 5 s de graça). **Saída capturada** de um leilão de 5 s com um lance de Carla:

```
t~1s : status open     highest_amount 300  highest_bidder carla
t~7s : status closing  highest_amount 300  highest_bidder carla
t~24s: status closed   highest_amount 300  highest_bidder carla   winner carla
lance depois do fim: {"detail":"leilao encerrado"} -> HTTP 409
```

## 4. `bid-svc`: aceita lances e os publica

### 4.1 Contrato HTTP (todos os desfechos, capturados)

| Situação | Resposta | Rótulo em `aegis_bids_total` |
|---|---|---|
| Lance novo | **202** `{"bid_id":"2ec0...","duplicate":false}` | `accepted` |
| Mesma `Idempotency-Key` repetida | **200** `{"bid_id":"2ec0...","duplicate":true}` (mesmo `bid_id`) | `replayed` |
| Falta o cabeçalho `Idempotency-Key` | **422** `{"detail":[{"type":"missing","loc":["header","Idempotency-Key"],...}]}` | (não chega a contar) |
| `amount` <= 0 ou corpo inválido | **422** | (não chega a contar) |
| Leilão inexistente | **404** `{"detail":"leilao nao encontrado"}` | `not_found` |
| Leilão encerrado | **409** `{"detail":"leilao encerrado"}` | `rejected_closed` |
| auction-svc fora do ar (e leilão não está em cache) | **503** `auction-svc indisponivel` | `error` |
| Redis/fila indisponível | **503** `fila indisponivel` | `error` |

**Saída capturada** do contador da requisição depois dessas chamadas:

```
aegis_http_requests_total{code="202", route="/api/bids", ...} 2.0
aegis_http_requests_total{code="200", route="/api/bids", ...} 1.0
aegis_http_requests_total{code="422", route="/api/bids", ...} 2.0
aegis_http_requests_total{code="404", route="/api/bids", ...} 1.0
aegis_bids_total{outcome="accepted"} 2.0   aegis_bids_total{outcome="replayed"} 1.0   aegis_bids_total{outcome="not_found"} 1.0
```

Note que **só os 5xx contam como falha** do SLO de disponibilidade. 200, 202, 404, 409 e 422 não queimam o budget.

### 4.2 A cadeia de decisões em `place_bid`

1. `lookup(auction_id)` devolve `ends_at_ms` ou `None`. A classe `HttpAuctionLookup` guarda em cache (por processo) só o que é **imutável**, o `ends_at_ms`. Por quê: o auction-svc não vira gargalo no "sniping" e, se ele cair, leilões já conhecidos continuam recebendo lances (degradação graciosa). Um leilão não encontrado **não** entra no cache.
2. Se `agora > ends_at_ms` → 409.
3. `bid_id = sha256(bidder + ":" + chave)[:32]`: **determinístico**.
4. `SET idem:{bid_id} 1 NX EX 86400`:
   - falhou o `NX` → já existe → responde 200 `duplicate: true` e **não republica**;
   - passou → segue para o `XADD`.
5. `XADD bids {bid_id, auction_id, bidder, amount, ts_ms}`. Se o `XADD` falhar, a chave `idem:` é **apagada** para o cliente poder tentar de novo, e responde 503.
6. Responde 202.

### 4.3 As três camadas contra lance duplicado (ADR 005)

```
  cliente ──retry──► [1] API: SET NX idem:{bid_id}      otimização: resposta rápida, evita republicar
                         │ publica
                         ▼
                     [2] Stream: entrega "pelo menos uma vez"   ⇒ a mesma mensagem PODE chegar 2x
                         │
                         ▼
                     [3] Settlement: Lua atômico  SET processed:{bid_id} NX   ◄── ESTA é a garantia
```

Por que `bid_id` **determinístico** fecha o buraco: se a camada 1 falhar (por exemplo, um timeout *depois* de o `XADD` ter dado certo, o que libera a chave `idem:`), o retry do cliente gera o **mesmo** `bid_id`, entra de novo no stream, e a camada 3 descarta. Com `bid_id` aleatório, o retry viraria um lance novo e a camada 3 não reconheceria. (Isso é a mutação M4 abaixo.)

## 5. `settlement-svc`: o consumidor que decide

### 5.1 Estrutura

- `main.py`: sobe o worker numa **thread** dentro do mesmo processo (um container simples por pod, bom para estudar KEDA e caos) e expõe `healthz`, `readyz`, `metrics`.
- `processor.py`: a classe `Processor` com a lógica.
- O worker roda em loop: `ensure_group()` → `poll_once(block_ms=1000)` e, a cada **10 s**, a manutenção (`reclaim_once`, `close_due_auctions`, `refresh_pending_gauge`).
- Se o Redis cair, o worker **não derruba o pod**: registra aviso, espera 1 s e tenta de novo. O `readyz` reflete o problema.

### 5.2 Vocabulário de Redis Streams (essencial)

| Termo | Significado |
|---|---|
| **Consumer group** | várias réplicas dividem o trabalho; cada mensagem vai para **um** consumidor do grupo |
| `XREADGROUP ... >` | "me dê mensagens novas, que ainda não foram entregues a ninguém do grupo" |
| **PEL** (Pending Entries List) | mensagens entregues a um consumidor e **ainda sem `XACK`** |
| `XACK` | "terminei esta mensagem"; sai da PEL |
| `XAUTOCLAIM` | "reivindico mensagens pendentes há mais de N ms de outro consumidor" (recuperação de pod morto) |
| **lag** | quantas mensagens do stream o grupo ainda **não leu** (é o que o KEDA mede) |
| **DLQ** | fila de mensagens que nunca darão certo (aqui, o stream `bids.dlq`) |

### 5.3 O script Lua, linha a linha

```lua
if redis.call('EXISTS', KEYS[2]) == 0 then return 'unknown_auction' end          -- (a)
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 86400) == false then return 'duplicate' end   -- (b)
local status = redis.call('HGET', KEYS[2], 'status')
local ends = tonumber(redis.call('HGET', KEYS[2], 'ends_at_ms'))
if status ~= 'open' or tonumber(ARGV[4]) > ends then return 'rejected_closed' end  -- (c)
local cur = tonumber(redis.call('HGET', KEYS[2], 'highest_amount'))
if tonumber(ARGV[3]) > cur then                                                  -- (d)
  redis.call('HSET', KEYS[2], 'highest_amount', ARGV[3], 'highest_bidder', ARGV[2], 'highest_bid_id', ARGV[1])
  return 'applied'
end
return 'outbid'                                                                  -- (e)
```

`KEYS[1] = processed:{bid_id}`, `KEYS[2] = auction:{id}`, `ARGV = bid_id, bidder, amount, ts_ms`.

| Passo | O que faz | Por que nesta ordem |
|---|---|---|
| (a) | o leilão existe? Se não, `unknown_auction` | antes de marcar como processado, para não "queimar" o `bid_id` à toa |
| (b) | `SET NX`: primeira vez marca `processed:`; se já existia, `duplicate` | **é a barreira**: atômica, sem janela entre "verificar" e "marcar" |
| (c) | status não é `open`, ou o **timestamp do lance** passou do fim → `rejected_closed` | a decisão usa o `ts_ms` do lance, não a hora em que o settlement processou |
| (d) | só aplica se o valor for **maior** que o atual | o maior lance nunca é sobrescrito por um menor, mesmo com mensagens fora de ordem |
| (e) | senão, `outbid` | o lance era válido, só não ganhou |

Por que **Lua**: o Redis executa o script inteiro de forma atômica. Sem isso, entre "verificar o maior lance" e "gravar" outro consumidor poderia gravar um valor maior e você o sobrescreveria. Há mais de um consumidor (réplicas) e mensagens fora de ordem, portanto a comparação **precisa** ser atômica.

**Saída capturada**: dois lances (1500 e 1200) deixaram `applied=1` e `outbid=1`; o lance menor foi aceito pela API (202), mas perdeu na liquidação. É o comportamento correto.

### 5.4 `handle()`: a ordem que importa

```
1. campos obrigatórios presentes?   não ─► XADD bids.dlq + XACK + contador "invalid"
2. result = Lua(apply)              aplica (ou descobre duplicata/fechado/menor)
3. XACK                             SÓ AGORA confirma
4. contador por resultado + histograma de atraso (agora − ts_ms)
```

O **`XACK` só depois de aplicar** é o ponto central: se o pod morrer entre o Lua e o `XACK`, a mensagem continua pendente, é reentregue (ou reivindicada) e o Lua responde `duplicate`, então nada é aplicado duas vezes. Se o `XACK` viesse antes, um crash no meio **perderia** o lance.

Resultados possíveis (rótulos de `aegis_settlement_processed_total`): `applied`, `outbid`, `duplicate`, `rejected_closed`, `unknown_auction`, `invalid`.

> Observação de projeto: `unknown_auction` é confirmado (`XACK`) e **não** vai para a DLQ. Discussão para entrevista: seria melhor mandar para a DLQ, para análise?

### 5.5 Recuperação, fechamento e o gauge

- `reclaim_once(min_idle_ms=30000)`: `XAUTOCLAIM` com `start_id="0-0"` reivindica mensagens órfãs há mais de **30 s** e as processa. Como a manutenção roda a cada 10 s, a recuperação de um pod morto leva entre ~30 e ~40 s.
- `close_due_auctions(grace_ms=5000)`: fecha leilões cujo fim passou há mais de **5 s**. A "graça" dá tempo para lances aceitos no último instante ainda na fila serem processados. **Trade-off** (documentado no código e no ADR 005): se a fila atrasar mais que 5 s, um lance válido pode ser rejeitado. Em produção, condicione o fechamento ao lag do grupo.
- `refresh_pending_gauge()`: atualiza `aegis_settlement_pending_entries` com o total de pendentes do grupo.

**Cuidado ao consultar:** cada réplica publica o **mesmo** valor global. Agregue com `max()`, nunca `sum()`.

## 6. O lag, capturado: o sinal que o KEDA lê

Experimento real: com o settlement ligado a fila está em dia; derrubei o settlement e enviei 25 lances; religuei.

```
settlement ligado:     entries-read 3   lag 0    pending 0
(settlement parado, 25 lances enviados, XLEN bids = 28)
settlement parado:     entries-read 3   lag 25   pending 0     ← fila acumulando
leilão ainda mostra:   30 uw3                                  ← a API continuou aceitando; a liquidação atrasou
(settlement religado)
após religar:          entries-read 28  lag 0    pending 0     ← drenou
leilão agora:          125 uq25
```

Três lições em uma tela: a fila **desacopla** produtor e consumidor (a API segue aceitando); o **lag** mede trabalho pendente (causa), diferente da CPU (efeito); e o sistema **se recupera sozinho** quando o consumidor volta. Repare que `pending` ficou 0: as mensagens ainda não tinham sido *entregues*, só estavam no stream. `pending` só sobe quando um consumidor lê e morre antes do `XACK`.

## 7. Dockerfile e docker compose

`apps/Dockerfile` serve os três serviços (argumento `SERVICE`):

| Escolha | Por quê |
|---|---|
| Multi-stage (`deps` e imagem final) | a imagem final não carrega `pip` nem cache: menor, menos superfície de ataque, scan mais limpo |
| `USER 10001` (numérico) | a política do Kyverno exige `runAsNonRoot`, e o Kubernetes só consegue verificar isso quando o `USER` é numérico |
| `PYTHONPATH=/app:/app/common` | permite `import aegis_common` e `import bid` sem instalar pacote |
| `CMD ... uvicorn ${SERVICE}.main:app` | um objeto `app` criado de forma *lazy* (via `__getattr__`) para que importar o módulo nos testes não tente conectar ao Redis |

`docker-compose.yml` (`make dev`) sobe Redis e os três serviços sem Kubernetes, nas portas 8001 (auction), 8002 (bid) e 8003 (settlement).

**Na prática**: para construir as imagens com o nome que os manifests esperam, testá-las e levá-las ao cluster, siga o `docs/fase-1-app-ci.md` (passos 3 e 4): `make images`, `make image-smoke` e `make images-load`.

## 8. O gerador de carga: `lightning.js` (k6)

Cenário `ramping-arrival-rate`: 20 s a 50 req/s (aquecimento), rampa até `PEAK_RPS` em 10 s, sustenta 30 s, desce em 10 s. Cada iteração envia um lance único e, em **10% dos casos**, reenvia o **mesmo** lance (replay). Os `thresholds` são os próprios SLOs e o teste **falha** se violá-los:

- `http_req_failed < 0,1%` (disponibilidade 99,9%);
- `p(99) < 300 ms` (latência);
- `duplicates_wrongly_accepted == 0` (se um replay não voltar como `duplicate: true`, conta como duplicata indevida).

Cuidado: o k6 também consome CPU. Se ele for o gargalo, o teste mente. Comece com `PEAK_RPS` de 200 a 500.

## 9. Os testes são a especificação

12 testes da aplicação (10 em `test_flow.py`, 2 em `test_concurrency.py`). Leia-os como a especificação de correção.

| Teste | O que prova |
|---|---|
| `test_happy_path_highest_bid_wins` | o maior lance vence, independentemente da ordem |
| `test_replay_with_same_idempotency_key_publishes_once` | camada 1: replay devolve 200 e **não** republica (`XLEN == 1`) |
| `test_settlement_discards_duplicate_delivery` | camada 3: mesma mensagem 2x no stream, efeito único |
| `test_bid_after_end_is_rejected_by_api` | 409 após o fim |
| `test_unknown_auction_returns_404` | 404 |
| `test_late_bid_in_queue_is_rejected_by_settlement` | a decisão usa o `ts_ms` do lance |
| `test_close_due_auctions_sets_winner` | respeita a graça de 5 s e define o vencedor |
| `test_crashed_consumer_message_is_reclaimed_and_applied_once` | pod morre sem `XACK`; outro reivindica; aplica uma vez |
| `test_poison_message_goes_to_dlq` | mensagem inválida vai para a DLQ e é confirmada |
| `test_metrics_endpoint_exposes_slo_series` | o contrato de métricas (inclui `le="0.3"`) |
| `test_make_redis_usa_pool_com_backpressure` | o pool é o bloqueante (precisa de `redis-server`) |
| `test_200_retries_simultaneos_geram_um_unico_lance` | 200 threads, mesma chave: 1 aceito (202), 199 duplicatas (200), 1 mensagem no stream (precisa de `redis-server`) |

Os testes de fluxo usam `fakeredis` (com `lupa`, para executar o Lua). Os 2 de concorrência usam um Redis **real** em porta aleatória e são pulados se não houver `redis-server` instalado. Os testes de `tools/` (19) cobrem os manifests e as ferramentas; veja `04-slo-e-ferramentas-internas.md`.

## 10. Laboratório de mutações (resultados reais)

Ideia: um teste só vale se **falha quando o código fica errado**. Apliquei 11 mutações a uma cópia do repositório e rodei as duas suítes. Resultados (**capturados**):

| # | Mutação | Resultado | Leitura |
|---|---|---|---|
| M0 | nenhuma (controle) | 12 e 19 passam | linha de base |
| M1 | remover `nx=True` da chave `idem:` no bid-svc | **falham 2**: concorrência e replay | camada 1 protegida |
| M2 | remover a detecção de duplicata no Lua | **falha 1**: `test_settlement_discards_duplicate_delivery` | camada 3 protegida |
| M3 | **`XACK` antes de aplicar** | **nenhum teste falha** | **lacuna: a regra central do ADR 005 não tem teste** |
| M4 | `bid_id` aleatório em vez de determinístico | **falham 2**: concorrência e replay | determinismo protegido |
| M5 | remover o bucket `0.3` do histograma | **falha 1**: `test_metrics_endpoint...` | contrato de latência protegido |
| M6 | renomear `aegis_http_requests_total` | falha só **1** (o teste da app); o teste de contrato de PromQL **passa** | **lacuna: o teste de PromQL é enganado por um docstring que ainda cita o nome antigo** |
| M7 | pool sem backpressure | **falham 2** (precisa de `redis-server`) | pool protegido |
| M8 | gate fail-closed (sem dados bloqueia) | **falha 1**: `test_budget_gate_policy` | política fail-open protegida |
| M9 | mudar o objetivo do SLO sem regenerar as regras | **falham 2**: limiares e "regras em dia" | o CI pega o esquecimento |
| M10 | label do chaos que não casa com nenhum pod | **falha 1**: `test_chaos_selectors_match_real_app_labels` | experimento nunca "erra o alvo" em silêncio |
| M11 | container sem `limits` | **falha 1**: `test_workloads_satisfy_our_own_kyverno_policies` | a app respeita as próprias políticas |

### Exercício 1: escreva o teste que pega M3

Objetivo: um teste que **passa** com o código correto e **falha** quando o `XACK` vem antes do Lua. Dica: simule um pod que morre *entre ler e aplicar* e confira se a mensagem **continua pendente**.

<details>
<summary>Solução (verificada: passa no código correto, falha na M3)</summary>

Acrescente ao final de `apps/tests/test_flow.py`:

```python
def test_message_stays_pending_if_apply_crashes(auction_client, redis_client):
    """XACK só depois de aplicar: se o pod morre no meio, a mensagem fica pendente."""
    a = new_auction(auction_client)
    redis_client.xadd(STREAM, {"bid_id": "x1", "auction_id": a, "bidder": "ana",
                               "amount": 700, "ts_ms": int(time.time() * 1000)})
    p = Processor(redis_client, consumer="c1")
    p.ensure_group()

    def boom(*args, **kwargs):
        raise RuntimeError("pod morreu entre ler e aplicar")

    p._apply = boom
    with pytest.raises(RuntimeError):
        p.poll_once(block_ms=10)
    assert redis_client.xpending(STREAM, GROUP)["pending"] == 1
```

Por que funciona: no código correto o `_apply` levanta antes do `XACK`, então a mensagem fica na PEL (`pending == 1`). Com o `XACK` antes, `pending` seria 0 e o teste falharia. Resultado medido: com o código correto, 11 passam no arquivo; com M3, 1 falha.
</details>

### Exercício 2: conserte o teste de contrato de métricas (M6)

Em `tools/tests/test_manifests.py`, `test_promql_metrics_and_labels_exist_in_app_code` compara os nomes usados no PromQL com **todo o texto** dos arquivos `.py`, inclusive docstrings. Escreva uma versão que compare só com os nomes **registrados** (`Counter("...")`, `Histogram("...")`, `Gauge("...")`).

<details>
<summary>Solução (verificada: passa no código correto, falha na M6)</summary>

Acrescente ao final de `tools/tests/test_manifests.py`:

```python
def test_promql_metrics_are_registered_in_app_code():
    """Compara com os nomes REGISTRADOS (Counter/Histogram/Gauge), não com texto solto:
    um docstring ou comentário com o nome antigo não pode enganar o teste."""
    registered = set()
    for p in glob.glob(os.path.join(ROOT, "apps", "**", "*.py"), recursive=True):
        if "/tests/" in p:
            continue
        registered |= set(re.findall(r'(?:Counter|Histogram|Gauge)\(\s*"(aegis_[a-z_]+)"', open(p).read()))
    queries = open(os.path.join(ROOT, "platform/observability/slos.yaml")).read()
    queries += open(os.path.join(ROOT, "deploy/components/canary-analysis/analysis-templates.yaml")).read()
    used = set(re.findall(r"\b(aegis_[a-z_]+?)(?:_bucket|_count|_sum)?[\{\[]", queries))
    assert used
    for m in used:
        base = re.sub(r"_total$", "", m)
        assert m in registered or base in registered, f"métrica {m} não está registrada no código"
```

Resultado medido: no código correto, 11 passam; com M6, 1 falha. Repare no `re.sub(r"_total$", ...)`: o `prometheus_client` registra o Counter sem o sufixo `_total` no código e o acrescenta ao expor.
</details>

### Como repetir o laboratório com segurança

```bash
git switch -c estudo/mutacoes        # ramo descartável
# edite UM trecho (ex.: tire nx=True de apps/bid/main.py)
make test                            # veja o que falha
git checkout -- .                    # desfaça antes da próxima mutação
git switch main && git branch -D estudo/mutacoes
```

**Registre** em `docs/LICOES.md`: qual mutação você achou que seria pega e não foi (M3 e M6 são os candidatos).

## Próximo passo

Faça as sessões S01 e S02 do roteiro (`05-roteiro-parte-1-fundacao-app-gitops.md`).
