#!/usr/bin/env python3
"""Error Budget Gate de CI: bloqueia merges de FEATURES quando o budget está baixo.

Política (a "regra" que torna o SLO uma ferramenta de decisão, não um enfeite):
  budget restante >= limite  -> tudo passa
  budget restante <  limite  -> só passa correção (--is-fix): confiabilidade
                                 antes de funcionalidade nova

Uso (com `make prom-ui` rodando em outro terminal):
  python tools/budget_gate.py --prom-url http://localhost:9090 --threshold 0.2
  python tools/budget_gate.py --is-fix         # PR de correção sempre passa

No GitHub Actions o Prometheus do seu notebook não é alcançável; este gate
roda localmente (make gate) ou em um runner self-hosted. O job do workflow só
executa se a variável de repositório PROMETHEUS_URL existir.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request


def evaluate(remaining: float | None, threshold: float, is_fix: bool) -> tuple[bool, str]:
    if remaining is None:
        # Sem dados não bloqueamos: um gate que trava por falta de métrica
        # treina o time a desligá-lo.
        return True, "sem dados de SLO ainda: gate liberado (fail-open)"
    pct = f"{remaining * 100:.1f}%"
    if remaining >= threshold:
        return True, f"budget restante {pct} >= {threshold * 100:.0f}%: liberado"
    if is_fix:
        return True, f"budget restante {pct} < {threshold * 100:.0f}%, mas é correção: liberado"
    return False, (
        f"BLOQUEADO: budget restante {pct} < {threshold * 100:.0f}%. "
        "Apenas correções de confiabilidade (--is-fix) podem ser mescladas."
    )


def query_remaining(prom_url: str, slo: str) -> float | None:
    q = f'aegis:error_budget_remaining:ratio{{slo="{slo}"}}'
    url = f"{prom_url}/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.load(resp)
    result = data["data"]["result"]
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if value != value else value  # NaN -> None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prom-url", default="http://localhost:9090")
    ap.add_argument("--slo", default="bid-availability")
    ap.add_argument("--threshold", type=float, default=0.2)
    ap.add_argument("--is-fix", action="store_true")
    args = ap.parse_args()
    try:
        remaining = query_remaining(args.prom_url, args.slo)
    except Exception as e:  # noqa: BLE001
        print(f"aviso: não consegui consultar o Prometheus ({e}); liberando (fail-open)")
        return 0
    ok, msg = evaluate(remaining, args.threshold, args.is_fix)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
