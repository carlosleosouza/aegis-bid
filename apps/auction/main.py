"""auction-svc: cria e consulta leilões.

Modelo no Redis (hash `auction:{id}`):
  title, starting_price, ends_at_ms, status (open|closed),
  highest_amount, highest_bidder, highest_bid_id

Quem ATUALIZA o maior lance e fecha o leilão é o settlement-svc. Este serviço
só cria e lê. (Compartilhar o Redis entre serviços é um atalho consciente para
manter o projeto pequeno; veja docs/adr/005-idempotencia-do-lance.md.)
"""
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from aegis_common.metrics import instrument
from aegis_common.redis_client import make_redis

AUCTIONS_INDEX = "auctions"


class AuctionIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    starting_price: int = Field(ge=0, description="em centavos")
    duration_seconds: int = Field(ge=5, le=86400)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _view(auction_id: str, h: dict) -> dict:
    status = h["status"]
    # "closing" = o relógio acabou mas o settlement ainda não fechou oficialmente.
    if status == "open" and _now_ms() > int(h["ends_at_ms"]):
        status = "closing"
    view = {
        "id": auction_id,
        "title": h["title"],
        "starting_price": int(h["starting_price"]),
        "ends_at_ms": int(h["ends_at_ms"]),
        "status": status,
        "highest_amount": int(h["highest_amount"]),
        "highest_bidder": h.get("highest_bidder") or None,
    }
    if status == "closed":
        view["winner"] = view["highest_bidder"]
    return view


def create_app(redis_client=None) -> FastAPI:
    app = FastAPI(title="auction-svc")
    r = redis_client or make_redis()
    instrument(app, "auction-svc")

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

    @app.post("/api/auctions", status_code=201)
    def create_auction(body: AuctionIn):
        auction_id = uuid.uuid4().hex[:12]
        ends_at_ms = _now_ms() + body.duration_seconds * 1000
        pipe = r.pipeline()
        pipe.hset(
            f"auction:{auction_id}",
            mapping={
                "title": body.title,
                "starting_price": body.starting_price,
                "ends_at_ms": ends_at_ms,
                "status": "open",
                # O preço inicial funciona como "maior lance" zero: o primeiro
                # lance precisa superá-lo.
                "highest_amount": body.starting_price,
                "highest_bidder": "",
                "highest_bid_id": "",
            },
        )
        pipe.sadd(AUCTIONS_INDEX, auction_id)
        pipe.execute()
        return _view(auction_id, r.hgetall(f"auction:{auction_id}"))

    @app.get("/api/auctions/{auction_id}")
    def get_auction(auction_id: str):
        h = r.hgetall(f"auction:{auction_id}")
        if not h:
            raise HTTPException(status_code=404, detail="leilao nao encontrado")
        return _view(auction_id, h)

    @app.get("/api/auctions")
    def list_auctions(limit: int = 20):
        ids = sorted(r.smembers(AUCTIONS_INDEX))[: max(1, min(limit, 100))]
        out = []
        for auction_id in ids:
            h = r.hgetall(f"auction:{auction_id}")
            if h:
                out.append(_view(auction_id, h))
        return {"auctions": out}

    return app


def _build_default_app() -> FastAPI:
    return create_app()


# `uvicorn auction.main:app` precisa de um objeto `app`. Criamos de forma lazy
# para que importar este módulo nos testes não tente conectar no Redis.
def __getattr__(name):
    if name == "app":
        globals()["app"] = _build_default_app()
        return globals()["app"]
    raise AttributeError(name)
