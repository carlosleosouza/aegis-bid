"""settlement-svc: worker + endpoints HTTP mínimos (probes e /metrics).

O worker roda em uma thread dentro do mesmo processo para o Pod ter UM
container simples. Em produção, muita gente separa worker e API; aqui a
simplicidade ajuda a estudar o comportamento do KEDA e do caos.
"""
import logging
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException

from aegis_common.metrics import instrument
from aegis_common.redis_client import make_redis
from settlement.processor import Processor

log = logging.getLogger("settlement")


def run_worker(proc: Processor, stop: threading.Event) -> None:
    last_housekeeping = 0.0
    while not stop.is_set():
        try:
            proc.ensure_group()
            proc.poll_once(block_ms=1000)
            if time.monotonic() - last_housekeeping > 10:
                proc.reclaim_once()
                proc.close_due_auctions()
                proc.refresh_pending_gauge()
                last_housekeeping = time.monotonic()
        except redis.RedisError as e:
            # Redis indisponível (ex.: experimento de caos): não derrube o Pod,
            # espere e tente de novo. O readyz já reflete o problema.
            log.warning("redis indisponivel: %s", e)
            stop.wait(1.0)


def create_app(redis_client=None, start_worker: bool = True) -> FastAPI:
    r = redis_client or make_redis()
    # block=1000ms precisa de um socket_timeout maior que o bloqueio; o cliente
    # padrão usa 1s, então criamos um timeout próprio para o worker.
    worker_r = redis_client or redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=5.0,
    )
    proc = Processor(worker_r, consumer=socket.gethostname())
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        thread = None
        if start_worker:
            thread = threading.Thread(target=run_worker, args=(proc, stop), daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(title="settlement-svc", lifespan=lifespan)
    instrument(app, "settlement-svc")

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

    return app


def __getattr__(name):
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
