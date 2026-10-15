#!/usr/bin/env python3
"""Chaos Director: executa experimentos de caos como EXPERIMENTOS CIENTÍFICOS.

Ciclo de cada experimento:
  1. HIPÓTESE escrita antes ("se X, o SLO se mantém")        -> chaos/catalog.yaml
  2. Mede o estado estável (SLIs ANTES)
  3. Injeta a falha (kubectl apply do CR do Chaos Mesh)
  4. Mede o impacto (SLIs DURANTE) enquanto gera tráfego
  5. Remove a falha e espera a recuperação, mede (SLIs DEPOIS)
  6. Veredito: hipótese confirmada ou REFUTADA
  7. Grava JSON em postmortems/data/ -> tools/postmortem.py gera o relatório

Hipótese refutada não é fracasso: é a descoberta que justifica o experimento.

Uso (com `make prom-ui` e a stack no ar):
  python tools/chaos_director.py --list
  python tools/chaos_director.py --id kill-bid-pod
  python tools/chaos_director.py --random --seed 7
"""
import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Medição
# --------------------------------------------------------------------------
class Prometheus:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def instant(self, query: str) -> float | None:
        url = f"{self.url}/api/v1/query?" + urllib.parse.urlencode({"query": query})
        with urllib.request.urlopen(url, timeout=10) as resp:
            result = json.load(resp)["data"]["result"]
        if not result:
            return None
        v = float(result[0]["value"][1])
        return None if v != v else v

    def sli(self, window: str) -> dict:
        """Taxa de erro e p99 da API de lances na janela dada (ex.: '60s')."""
        err = (
            f'sum(rate(aegis_http_requests_total{{service="bid-svc",route="/api/bids",code=~"5.."}}[{window}]))'
            f' / sum(rate(aegis_http_requests_total{{service="bid-svc",route="/api/bids"}}[{window}]))'
        )
        p99 = (
            "histogram_quantile(0.99, sum by (le) (rate("
            f'aegis_http_request_duration_seconds_bucket{{service="bid-svc",route="/api/bids"}}[{window}])))'
        )
        return {"error_ratio": self.instant(err), "p99_seconds": self.instant(p99)}


class Kube:
    def apply(self, manifest: str) -> None:
        subprocess.run(["kubectl", "apply", "-f", manifest], check=True)

    def delete(self, manifest: str) -> None:
        subprocess.run(["kubectl", "delete", "-f", manifest, "--ignore-not-found"], check=True)


class LightLoad:
    """Tráfego leve e constante: sem requisições, os SLIs seriam vazios (NaN).
    Também mede o erro do ponto de vista do CLIENTE, que serve de contraprova
    para o que o Prometheus enxerga."""

    def __init__(self, base_url: str, rps: int = 20):
        self.base, self.rps = base_url.rstrip("/"), rps
        self.ok = self.fail = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _post(self, path: str, body: dict, headers: dict | None = None):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return urllib.request.urlopen(req, timeout=2)

    def _run(self):
        try:
            auction = json.load(
                self._post("/api/auctions", {"title": "chaos", "starting_price": 1, "duration_seconds": 3600})
            )["id"]
        except Exception as e:  # noqa: BLE001
            print(f"[load] não consegui criar leilão: {e}", file=sys.stderr)
            return
        n = 0
        while not self._stop.is_set():
            n += 1
            try:
                self._post(
                    "/api/bids",
                    {"auction_id": auction, "bidder": f"chaos-{n % 50}", "amount": n + 1},
                    {"Idempotency-Key": uuid.uuid4().hex},
                )
                self.ok += 1
            except Exception:  # noqa: BLE001
                self.fail += 1
            self._stop.wait(1 / self.rps)

    def start(self):
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=5)
        total = self.ok + self.fail
        return {"requests": total, "failures": self.fail,
                "client_error_ratio": (self.fail / total) if total else None}


# --------------------------------------------------------------------------
# Lógica de decisão (pura: fácil de testar)
# --------------------------------------------------------------------------
def verdict(during: dict, steady_state: dict) -> tuple[bool, list[str]]:
    """Retorna (hipótese_confirmada, motivos_da_violacao)."""
    reasons = []
    err, p99 = during.get("error_ratio"), during.get("p99_seconds")
    if err is not None and err > steady_state["max_error_ratio"]:
        reasons.append(f"taxa de erro {err:.4f} > limite {steady_state['max_error_ratio']}")
    if p99 is not None and p99 > steady_state["max_p99_seconds"]:
        reasons.append(f"p99 {p99:.3f}s > limite {steady_state['max_p99_seconds']}s")
    return (not reasons), reasons


def run_experiment(exp: dict, prom, kube, load=None, sleep=time.sleep, now=None, recovery_seconds=60) -> dict:
    now = now or (lambda: datetime.now(timezone.utc))
    events = []

    def log(msg: str):
        events.append({"ts": now().isoformat(timespec="seconds"), "event": msg})
        print(f"[{events[-1]['ts']}] {msg}")

    manifest = os.path.join(ROOT, exp["manifest"])
    window = f"{max(30, exp['duration_seconds'])}s"

    if load:
        load.start()
        log("tráfego de carga leve iniciado")
        sleep(30)
    before = prom.sli("2m")
    log(f"estado estável medido: {before}")

    log(f"INJETANDO falha: {exp['id']} ({exp['manifest']})")
    kube.apply(manifest)
    sleep(exp["duration_seconds"])
    during = prom.sli(window)
    log(f"impacto medido durante a falha: {during}")

    kube.delete(manifest)
    log("falha REMOVIDA; aguardando recuperação")
    sleep(recovery_seconds)
    after = prom.sli("1m")
    log(f"estado após recuperação: {after}")

    client = load.stop() if load else None
    confirmed, reasons = verdict(during, exp["steady_state"])
    log("hipótese CONFIRMADA" if confirmed else f"hipótese REFUTADA: {'; '.join(reasons)}")

    return {
        "id": exp["id"],
        "hypothesis": exp["hypothesis"],
        "steady_state": exp["steady_state"],
        "duration_seconds": exp["duration_seconds"],
        "before": before, "during": during, "after": after,
        "client": client,
        "confirmed": confirmed, "violations": reasons,
        "events": events,
        "finished_at": now().isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=os.path.join(ROOT, "chaos", "catalog.yaml"))
    ap.add_argument("--prom-url", default="http://localhost:9090")
    ap.add_argument("--base-url", default="http://aegis.localhost:8080")
    ap.add_argument("--id")
    ap.add_argument("--random", action="store_true")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "postmortems", "data"))
    args = ap.parse_args()

    with open(args.catalog) as f:
        experiments = yaml.safe_load(f)["experiments"]

    if args.list:
        for e in experiments:
            print(f"{e['id']:<24} {e['duration_seconds']:>4}s  {e['hypothesis']}")
        return 0

    if args.id:
        exp = next((e for e in experiments if e["id"] == args.id), None)
        if not exp:
            print(f"experimento '{args.id}' não existe (use --list)", file=sys.stderr)
            return 2
    elif args.random:
        exp = random.Random(args.seed).choice(experiments)
    else:
        print("informe --id <experimento> ou --random", file=sys.stderr)
        return 2

    result = run_experiment(
        exp, Prometheus(args.prom_url), Kube(),
        load=None if args.no_load else LightLoad(args.base_url),
    )
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(args.out_dir, f"{stamp}-{exp['id']}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nresultado salvo em {path}")
    print(f"próximo passo: python tools/postmortem.py {path}")
    return 0 if result["confirmed"] else 1


if __name__ == "__main__":
    sys.exit(main())
