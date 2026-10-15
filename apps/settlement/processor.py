"""Núcleo do settlement: consome o stream `bids` e aplica cada lance UMA vez.

Conceitos que este arquivo exercita (bons assuntos de entrevista):

* Consumer group: várias réplicas dividem o trabalho; cada mensagem vai para um
  único consumidor.
* Pending Entries List (PEL): mensagem entregue e ainda não confirmada (XACK).
  Se o pod morrer no meio, a mensagem fica pendente e outro pod a reivindica
  (XAUTOCLAIM). Isso é redelivery => o mesmo lance pode chegar duas vezes.
* Idempotência: o script Lua marca `processed:{bid_id}` com SET NX. Se a chave
  já existe, é duplicata e nada é aplicado. Lua roda atômico no Redis, então não
  existe janela entre "verificar" e "aplicar".
"""
import time

import redis
from prometheus_client import Counter, Gauge, Histogram

STREAM = "bids"
DLQ_STREAM = "bids.dlq"
GROUP = "settlement"

PROCESSED = Counter(
    "aegis_settlement_processed_total", "Lances processados por resultado", ["result"]
)
# O SLO de "frescor" usa o bucket le="2": 99% dos lances liquidados em até 2 s.
LAG = Histogram(
    "aegis_settlement_lag_seconds",
    "Tempo entre o lance ser aceito na API e ser liquidado",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)
PENDING = Gauge(
    "aegis_settlement_pending_entries", "Mensagens entregues e ainda sem XACK"
)

# KEYS[1] = processed:{bid_id}   KEYS[2] = auction:{auction_id}
# ARGV    = bid_id, bidder, amount, ts_ms
APPLY_BID_LUA = """
if redis.call('EXISTS', KEYS[2]) == 0 then return 'unknown_auction' end
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 86400) == false then return 'duplicate' end
local status = redis.call('HGET', KEYS[2], 'status')
local ends = tonumber(redis.call('HGET', KEYS[2], 'ends_at_ms'))
if status ~= 'open' or tonumber(ARGV[4]) > ends then return 'rejected_closed' end
local cur = tonumber(redis.call('HGET', KEYS[2], 'highest_amount'))
if tonumber(ARGV[3]) > cur then
  redis.call('HSET', KEYS[2], 'highest_amount', ARGV[3], 'highest_bidder', ARGV[2], 'highest_bid_id', ARGV[1])
  return 'applied'
end
return 'outbid'
"""

REQUIRED_FIELDS = ("bid_id", "auction_id", "bidder", "amount", "ts_ms")


def _now_ms() -> int:
    return int(time.time() * 1000)


class Processor:
    def __init__(self, r: redis.Redis, consumer: str):
        self.r = r
        self.consumer = consumer
        self._apply = r.register_script(APPLY_BID_LUA)

    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def handle(self, entry_id: str, fields: dict) -> str:
        """Processa UMA mensagem e a confirma. Retorna o resultado."""
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        if missing:
            # Mensagem venenosa: se nunca der certo, ficaria reprocessando para
            # sempre. Vai para a DLQ para análise humana e é confirmada.
            self.r.xadd(DLQ_STREAM, {**fields, "reason": f"faltando {missing}"})
            self.r.xack(STREAM, GROUP, entry_id)
            PROCESSED.labels("invalid").inc()
            return "invalid"

        result = self._apply(
            keys=[f"processed:{fields['bid_id']}", f"auction:{fields['auction_id']}"],
            args=[fields["bid_id"], fields["bidder"], fields["amount"], fields["ts_ms"]],
        )
        # XACK só DEPOIS de aplicar. Se o pod morrer entre o Lua e o XACK, a
        # mensagem é reentregue e o Lua responde 'duplicate' (nada é aplicado
        # duas vezes). Esse é o ponto central do projeto.
        self.r.xack(STREAM, GROUP, entry_id)
        PROCESSED.labels(result).inc()
        LAG.observe(max(0.0, (_now_ms() - int(fields["ts_ms"])) / 1000))
        return result

    def poll_once(self, block_ms: int = 1000, count: int = 50) -> int:
        resp = self.r.xreadgroup(
            GROUP, self.consumer, {STREAM: ">"}, count=count, block=block_ms
        )
        n = 0
        for _stream, entries in resp or []:
            for entry_id, fields in entries:
                self.handle(entry_id, fields)
                n += 1
        return n

    def reclaim_once(self, min_idle_ms: int = 30_000, count: int = 50) -> int:
        """Reivindica mensagens de consumidores que morreram (idle > min_idle_ms)."""
        res = self.r.xautoclaim(
            STREAM, GROUP, self.consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count
        )
        entries = res[1]
        n = 0
        for entry_id, fields in entries:
            if fields:  # entradas apagadas do stream voltam vazias
                self.handle(entry_id, fields)
                n += 1
        return n

    def close_due_auctions(self, now_ms: int | None = None, grace_ms: int = 5000) -> int:
        """Fecha leilões cujo relógio acabou há mais de `grace_ms`.

        O período de graça dá tempo para lances aceitos por último (ts <= fim)
        ainda na fila serem processados. TRADE-OFF conhecido: se a fila tiver
        mais atraso que `grace_ms`, um lance válido pode ser rejeitado. Em
        produção você condicionaria o fechamento ao lag do consumer group.
        """
        now_ms = now_ms or _now_ms()
        closed = 0
        for auction_id in self.r.smembers("auctions"):
            key = f"auction:{auction_id}"
            h = self.r.hmget(key, "status", "ends_at_ms")
            if h[0] == "open" and h[1] and now_ms > int(h[1]) + grace_ms:
                self.r.hset(key, "status", "closed")  # idempotente
                closed += 1
        return closed

    def refresh_pending_gauge(self) -> None:
        try:
            info = self.r.xpending(STREAM, GROUP)
            PENDING.set(info["pending"] if isinstance(info, dict) else info[0])
        except redis.ResponseError:
            pass
