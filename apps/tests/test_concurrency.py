"""Concorrência contra um Redis REAL (o fakeredis não reproduz limites de pool).

Pulado automaticamente se `redis-server` não estiver instalado.
Cenário: 200 retries simultâneos da MESMA Idempotency-Key. Esperado:
exatamente 1 lance entra no stream, 199 respostas "duplicate" e ZERO erros.
"""
import concurrent.futures as cf
import shutil
import socket
import subprocess
import time

import pytest
import redis
from fastapi.testclient import TestClient

from aegis_common.redis_client import make_redis
from bid.main import create_app as create_bid_app

pytestmark = pytest.mark.skipif(
    shutil.which("redis-server") is None, reason="redis-server nao instalado"
)


@pytest.fixture
def real_redis_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    probe = redis.Redis.from_url(url)
    for _ in range(50):
        try:
            probe.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


def test_make_redis_usa_pool_com_backpressure(monkeypatch, real_redis_url):
    monkeypatch.setenv("REDIS_URL", real_redis_url)
    assert isinstance(make_redis().connection_pool, redis.BlockingConnectionPool)


def test_200_retries_simultaneos_geram_um_unico_lance(monkeypatch, real_redis_url):
    monkeypatch.setenv("REDIS_URL", real_redis_url)
    # pool pequeno de propósito: 200 threads disputando 8 conexões tem que ESPERAR, não falhar
    monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "8")
    r = make_redis()
    far_future = int(time.time() * 1000) + 60_000
    client = TestClient(create_bid_app(r, lambda auction_id: far_future))

    def go(_):
        return client.post(
            "/api/bids",
            json={"auction_id": "a1", "bidder": "leo", "amount": 100},
            headers={"Idempotency-Key": "k1"},
        )

    with cf.ThreadPoolExecutor(200) as ex:
        responses = list(ex.map(go, range(200)))

    codes = [resp.status_code for resp in responses]
    assert codes.count(202) == 1, codes  # exatamente um aceito
    assert codes.count(200) == 199, codes  # os demais: duplicata reconhecida
    assert r.xlen("bids") == 1  # e só um lance na fila
