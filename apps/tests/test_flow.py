"""Testes do fluxo de ponta a ponta (sem Kubernetes, sem Redis real).

Leia estes testes como a ESPECIFICAÇÃO do comportamento de correção do sistema.
"""
import time

import pytest
from fastapi.testclient import TestClient

from auction.main import create_app as create_auction_app
from bid.main import create_app as create_bid_app, make_bid_id
from settlement.processor import DLQ_STREAM, GROUP, STREAM, Processor


@pytest.fixture
def auction_client(redis_client):
    return TestClient(create_auction_app(redis_client))


@pytest.fixture
def bid_client(redis_client, auction_client):
    def lookup(auction_id):
        resp = auction_client.get(f"/api/auctions/{auction_id}")
        return None if resp.status_code == 404 else resp.json()["ends_at_ms"]

    return TestClient(create_bid_app(redis_client, lookup))


@pytest.fixture
def proc(redis_client):
    p = Processor(redis_client, consumer="test-1")
    p.ensure_group()
    return p


def new_auction(auction_client, duration=60, start=100):
    resp = auction_client.post(
        "/api/auctions",
        json={"title": "Relogio", "starting_price": start, "duration_seconds": duration},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def bid(bid_client, auction_id, bidder, amount, key):
    return bid_client.post(
        "/api/bids",
        json={"auction_id": auction_id, "bidder": bidder, "amount": amount},
        headers={"Idempotency-Key": key},
    )


def test_happy_path_highest_bid_wins(auction_client, bid_client, proc):
    a = new_auction(auction_client)
    assert bid(bid_client, a, "ana", 150, "k1").status_code == 202
    assert bid(bid_client, a, "bruno", 300, "k2").status_code == 202
    assert bid(bid_client, a, "carla", 200, "k3").status_code == 202
    assert proc.poll_once(block_ms=10) == 3
    view = auction_client.get(f"/api/auctions/{a}").json()
    assert view["highest_amount"] == 300
    assert view["highest_bidder"] == "bruno"


def test_replay_with_same_idempotency_key_publishes_once(auction_client, bid_client, redis_client):
    a = new_auction(auction_client)
    first = bid(bid_client, a, "ana", 150, "same-key")
    second = bid(bid_client, a, "ana", 150, "same-key")
    assert first.status_code == 202 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert first.json()["bid_id"] == second.json()["bid_id"]
    assert redis_client.xlen(STREAM) == 1


def test_settlement_discards_duplicate_delivery(auction_client, redis_client, proc):
    """Camada 3: mesmo que a mesma mensagem entre duas vezes no stream
    (retry + falha da camada 1), o efeito é aplicado uma única vez."""
    a = new_auction(auction_client)
    bid_id = make_bid_id("ana", "dup")
    fields = {"bid_id": bid_id, "auction_id": a, "bidder": "ana", "amount": 500,
              "ts_ms": int(time.time() * 1000)}
    redis_client.xadd(STREAM, fields)
    redis_client.xadd(STREAM, fields)
    proc.poll_once(block_ms=10)
    from settlement.processor import PROCESSED
    assert PROCESSED.labels("duplicate")._value.get() >= 1
    assert auction_client.get(f"/api/auctions/{a}").json()["highest_amount"] == 500


def test_bid_after_end_is_rejected_by_api(auction_client, bid_client, redis_client):
    a = new_auction(auction_client)
    redis_client.hset(f"auction:{a}", "ends_at_ms", int(time.time() * 1000) - 1000)
    # o lookup do teste não usa cache; relê o valor alterado
    resp = bid(bid_client, a, "ana", 150, "late")
    assert resp.status_code == 409


def test_unknown_auction_returns_404(bid_client):
    assert bid(bid_client, "nao-existe", "ana", 150, "k").status_code == 404


def test_late_bid_in_queue_is_rejected_by_settlement(auction_client, redis_client, proc):
    """Lance aceito pela API ANTES do fim, mas com ts depois do fim ao ser
    processado, não vence: a decisão usa o timestamp do lance."""
    a = new_auction(auction_client)
    end = int(redis_client.hget(f"auction:{a}", "ends_at_ms"))
    redis_client.xadd(STREAM, {"bid_id": "late1", "auction_id": a, "bidder": "ana",
                               "amount": 999, "ts_ms": end + 1})
    proc.poll_once(block_ms=10)
    assert auction_client.get(f"/api/auctions/{a}").json()["highest_amount"] == 100


def test_close_due_auctions_sets_winner(auction_client, bid_client, redis_client, proc):
    a = new_auction(auction_client, duration=5)
    bid(bid_client, a, "ana", 150, "k1")
    proc.poll_once(block_ms=10)
    end = int(redis_client.hget(f"auction:{a}", "ends_at_ms"))
    assert proc.close_due_auctions(now_ms=end + 1000, grace_ms=5000) == 0  # ainda na graça
    assert proc.close_due_auctions(now_ms=end + 6000, grace_ms=5000) == 1
    view = auction_client.get(f"/api/auctions/{a}").json()
    assert view["status"] == "closed" and view["winner"] == "ana"


def test_crashed_consumer_message_is_reclaimed_and_applied_once(auction_client, redis_client):
    """Simula o caos 'matar pod': consumidor A lê e morre sem XACK; B reivindica."""
    a = new_auction(auction_client)
    redis_client.xadd(STREAM, {"bid_id": "b1", "auction_id": a, "bidder": "ana",
                               "amount": 700, "ts_ms": int(time.time() * 1000)})
    dead = Processor(redis_client, consumer="pod-morto")
    dead.ensure_group()
    redis_client.xreadgroup(GROUP, "pod-morto", {STREAM: ">"}, count=10)  # lê e "morre"
    assert redis_client.xpending(STREAM, GROUP)["pending"] == 1

    alive = Processor(redis_client, consumer="pod-vivo")
    assert alive.reclaim_once(min_idle_ms=0) == 1
    assert redis_client.xpending(STREAM, GROUP)["pending"] == 0
    assert auction_client.get(f"/api/auctions/{a}").json()["highest_amount"] == 700


def test_poison_message_goes_to_dlq(redis_client, proc):
    redis_client.xadd(STREAM, {"lixo": "1"})
    proc.poll_once(block_ms=10)
    assert redis_client.xlen(DLQ_STREAM) == 1
    assert redis_client.xpending(STREAM, GROUP)["pending"] == 0


def test_metrics_endpoint_exposes_slo_series(auction_client):
    auction_client.get("/api/auctions")
    body = auction_client.get("/metrics").text
    assert "aegis_http_requests_total" in body
    assert 'route="/api/auctions"' in body
    assert "aegis_http_request_duration_seconds_bucket" in body
    assert 'le="0.3"' in body  # o SLO de latência depende deste bucket
