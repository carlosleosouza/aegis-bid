// Leilão Relâmpago: simula a corrida dos últimos segundos de um leilão.
//
//   k6 run -e BASE_URL=http://aegis.localhost:8090 -e PEAK_RPS=500 apps/loadgen/lightning.js
//
// PEAK_RPS: comece com 200-500 no notebook. 5000 exige máquina folgada (e o k6
// também consome CPU: se o gerador de carga for o gargalo, o teste mente).
//
// O que cada parte prova:
//  - rampa até o pico        -> KEDA deve escalar o settlement-svc pela FILA
//  - 10% de replays          -> a camada de idempotência devolve o MESMO bid_id
//  - thresholds              -> são os próprios SLOs: o teste FALHA se violá-los
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://aegis.localhost:8090";
const PEAK = parseInt(__ENV.PEAK_RPS || "500");
const duplicatesAccepted = new Counter("duplicates_wrongly_accepted");

export const options = {
  scenarios: {
    snipe: {
      executor: "ramping-arrival-rate",
      startRate: 20,
      timeUnit: "1s",
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { target: 50, duration: "20s" },   // aquecimento: leilão "normal"
        { target: PEAK, duration: "10s" }, // os últimos segundos: explosão
        { target: PEAK, duration: "30s" }, // sustenta o pico
        { target: 20, duration: "10s" },   // acabou
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.001"],       // SLO de disponibilidade: 99,9%
    http_req_duration: ["p(99)<300"],      // SLO de latência: p99 < 300 ms
    duplicates_wrongly_accepted: ["count==0"],
  },
};

export function setup() {
  const res = http.post(
    `${BASE}/api/auctions`,
    JSON.stringify({ title: "Relampago", starting_price: 100, duration_seconds: 300 }),
    { headers: { "Content-Type": "application/json" } }
  );
  check(res, { "leilao criado": (r) => r.status === 201 });
  return { auctionId: res.json("id") };
}

export default function (data) {
  const bidder = `user-${__VU}`;
  const key = `${__VU}-${__ITER}`;
  const payload = JSON.stringify({
    auction_id: data.auctionId,
    bidder,
    amount: 100 + Math.floor(Math.random() * 1_000_000),
  });
  const params = {
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
  };

  const res = http.post(`${BASE}/api/bids`, payload, params);
  check(res, { "lance aceito": (r) => r.status === 202 });

  // 10% das vezes o "cliente" reenvia o mesmo lance (ex.: timeout de rede).
  if (Math.random() < 0.1 && res.status === 202) {
    const replay = http.post(`${BASE}/api/bids`, payload, params);
    const same = replay.status === 200 && replay.json("duplicate") === true;
    if (!same) duplicatesAccepted.add(1);
  }
}
