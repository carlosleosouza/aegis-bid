"""bid-svc: recebe lances e os publica no stream `bids` do Redis.

Camadas de proteção contra lance duplicado (detalhe em docs/adr/005):

 1. API (aqui): header Idempotency-Key. Repetir a mesma chave devolve o mesmo
    resultado sem publicar de novo. É uma otimização de resposta rápida.
 2. Stream: entrega "pelo menos uma vez". Um lance PODE aparecer duas vezes
    (retry do cliente após timeout, redelivery após crash do consumidor).
 3. Settlement: aplica cada bid_id UMA vez (SET NX atômico dentro de um script
    Lua). É esta camada que garante o efeito "exatamente uma vez".

O bid_id é DETERMINÍSTICO (hash de bidder + Idempotency-Key). Assim, mesmo que a
camada 1 falhe (ex.: timeout após o XADD ter dado certo), o retry gera o mesmo
bid_id e a camada 3 descarta a duplicata.
"""
import hashlib
import os
import time

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field

from aegis_common.metrics import instrument
from aegis_common.redis_client import make_redis

STREAM = "bids"

BIDS = Counter("aegis_bids_total", "Lances por desfecho", ["outcome"])


class BidIn(BaseModel):
    auction_id: str = Field(min_length=1, max_length=64)
    bidder: str = Field(min_length=1, max_length=64)
    amount: int = Field(gt=0, description="em centavos")


class HttpAuctionLookup:
    """Consulta o auction-svc e guarda em cache o que é IMUTÁVEL (ends_at_ms).

    POR QUE cache: (1) o auction-svc não vira gargalo durante o "sniping";
    (2) se o auction-svc cair, leilões já conhecidos continuam recebendo lances.
    É uma decisão de resiliência: degradar graciosamente em vez de falhar junto.
    """

    def __init__(self, base_url: str, timeout: float = 0.5):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._ends_at: dict[str, int] = {}

    def __call__(self, auction_id: str) -> int | None:
        if auction_id in self._ends_at:
            return self._ends_at[auction_id]
        resp = self._client.get(f"/api/auctions/{auction_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        ends_at_ms = int(resp.json()["ends_at_ms"])
        self._ends_at[auction_id] = ends_at_ms
        return ends_at_ms


def make_bid_id(bidder: str, idempotency_key: str) -> str:
    return hashlib.sha256(f"{bidder}:{idempotency_key}".encode()).hexdigest()[:32]


def create_app(redis_client=None, auction_lookup=None) -> FastAPI:
    app = FastAPI(title="bid-svc")
    r = redis_client or make_redis()
    lookup = auction_lookup or HttpAuctionLookup(
        os.getenv("AUCTION_SVC_URL", "http://auction-svc:8000")
    )
    instrument(app, "bid-svc")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        try:
            r.ping()
        except Exception:
            raise HTTPException(status_code=503, detail="redis indisponivel")
        return {"status": "ready"}

    @app.post("/api/bids", status_code=202)
    def place_bid(bid: BidIn, idempotency_key: str = Header(..., alias="Idempotency-Key")):
        # 1) o leilão existe e ainda está aberto?
        try:
            ends_at_ms = lookup(bid.auction_id)
        except Exception:
            BIDS.labels("error").inc()
            raise HTTPException(status_code=503, detail="auction-svc indisponivel")
        if ends_at_ms is None:
            BIDS.labels("not_found").inc()
            raise HTTPException(status_code=404, detail="leilao nao encontrado")
        now_ms = int(time.time() * 1000)
        if now_ms > ends_at_ms:
            BIDS.labels("rejected_closed").inc()
            raise HTTPException(status_code=409, detail="leilao encerrado")

        bid_id = make_bid_id(bid.bidder, idempotency_key)
        idem_key = f"idem:{bid_id}"

        try:
            # 2) camada 1: replay da mesma Idempotency-Key
            first_time = r.set(idem_key, "1", nx=True, ex=86400)
            if not first_time:
                BIDS.labels("replayed").inc()
                return JSONResponse(
                    status_code=200, content={"bid_id": bid_id, "duplicate": True}
                )
            # 3) publica no stream (camada 2: at-least-once)
            try:
                r.xadd(
                    STREAM,
                    {
                        "bid_id": bid_id,
                        "auction_id": bid.auction_id,
                        "bidder": bid.bidder,
                        "amount": bid.amount,
                        "ts_ms": now_ms,
                    },
                    maxlen=1_000_000,
                    approximate=True,
                )
            except Exception:
                # Libera a chave para o cliente poder tentar de novo. Se o XADD
                # tiver dado certo antes do timeout, o retry publica o MESMO
                # bid_id e o settlement descarta a duplicata (camada 3).
                r.delete(idem_key)
                raise
        except Exception:
            BIDS.labels("error").inc()
            raise HTTPException(status_code=503, detail="fila indisponivel")

        BIDS.labels("accepted").inc()
        return {"bid_id": bid_id, "duplicate": False}

    return app


def __getattr__(name):
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
