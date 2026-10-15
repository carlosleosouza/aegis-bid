#!/usr/bin/env python3
"""Gera o dashboard "Saúde do Leilão" como ConfigMap (o sidecar do Grafana o carrega).

  python tools/dashboard_gen.py > platform/observability/dashboard-saude-do-leilao.yaml

Gerar JSON de dashboard por código evita o pesadelo de revisar JSON gigante no
Git, e deixa cada painel com a consulta PromQL visível e comentável.
"""
import json
import sys

import yaml

DS = {"type": "prometheus", "uid": "prometheus"}
_id = 0


def panel(kind, title, targets, x, y, w, h, unit=None, thresholds=None, desc=""):
    global _id
    _id += 1
    ts = []
    for i, (expr, legend) in enumerate(targets):
        ts.append({"datasource": DS, "expr": expr, "legendFormat": legend, "refId": chr(65 + i)})
    defaults = {}
    if unit:
        defaults["unit"] = unit
    if thresholds:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
    return {
        "id": _id, "type": kind, "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": ts,
        "fieldConfig": {"defaults": defaults, "overrides": []},
    }


RED, YEL, GRN = "red", "yellow", "green"
BUDGET_STEPS = [{"color": RED, "value": None}, {"color": YEL, "value": 0.2}, {"color": GRN, "value": 0.5}]

PANELS = [
    panel("stat", "Error budget restante (30 dias)",
          [('aegis:error_budget_remaining:ratio', "{{slo}}")], 0, 0, 8, 5, "percentunit", BUDGET_STEPS,
          "1 = intacto, 0 = esgotado. Abaixo de 20% o Error Budget Gate bloqueia features."),
    panel("timeseries", "Burn rate da disponibilidade (1 = ritmo sustentável)",
          [('aegis:sli_error:ratio_rate1h{slo="bid-availability"} / 0.001', "janela 1h"),
           ('aegis:sli_error:ratio_rate5m{slo="bid-availability"} / 0.001', "janela 5m")], 8, 0, 8, 5, None, None,
          "14,4 = dispara o alerta de page (2% do budget em 1 h)."),
    panel("timeseries", "Lances por segundo (por desfecho)",
          [('sum by (outcome) (rate(aegis_bids_total[1m]))', "{{outcome}}")], 16, 0, 8, 5, "reqps"),
    panel("timeseries", "Latência da API de lances (p50 / p95 / p99)",
          [(f'histogram_quantile({q}, sum by (le) (rate(aegis_http_request_duration_seconds_bucket{{service="bid-svc",route="/api/bids"}}[1m])))', f"p{int(q*100)}")
           for q in (0.5, 0.95, 0.99)], 0, 5, 12, 8, "s", None, "O SLO exige p99 abaixo de 0,3 s."),
    panel("timeseries", "Taxa de erro 5xx da API de lances",
          [('sum(rate(aegis_http_requests_total{service="bid-svc",route="/api/bids",code=~"5.."}[1m])) / sum(rate(aegis_http_requests_total{service="bid-svc",route="/api/bids"}[1m]))', "5xx")],
          12, 5, 12, 8, "percentunit"),
    panel("timeseries", "Fila x réplicas x frescor (a história do KEDA)",
          [('aegis_settlement_pending_entries', "pendentes (sem XACK)"),
           ('kube_deployment_status_replicas{namespace="aegis",deployment="settlement-svc"}', "réplicas do settlement"),
           ('histogram_quantile(0.99, sum by (le) (rate(aegis_settlement_lag_seconds_bucket[1m])))', "p99 do atraso (s)")],
          0, 13, 12, 8),
    panel("timeseries", "Tráfego por versão do bid-svc (canary visível)",
          [('sum by (rollouts_pod_template_hash) (rate(aegis_http_requests_total{service="bid-svc",route="/api/bids"}[1m]))', "{{rollouts_pod_template_hash}}")],
          12, 13, 12, 8, "reqps", None, "Durante um rollout aparecem duas séries: estável e canary."),
    panel("timeseries", "Reinícios de containers (namespace aegis)",
          [('sum by (pod) (increase(kube_pod_container_status_restarts_total{namespace="aegis"}[5m]))', "{{pod}}")],
          0, 21, 12, 6),
    panel("timeseries", "Duplicatas suprimidas pelo settlement (idempotência em ação)",
          [('sum(rate(aegis_settlement_processed_total{result="duplicate"}[1m]))', "duplicate/s"),
           ('sum(rate(aegis_bids_total{outcome="replayed"}[1m]))', "replays barrados na API/s")],
          12, 21, 12, 6, "ops"),
]


def build() -> dict:
    dashboard = {
        "uid": "aegis-saude-leilao", "title": "AEGIS-Bid: Saúde do Leilão", "schemaVersion": 39,
        "version": 1, "refresh": "10s", "time": {"from": "now-30m", "to": "now"},
        "tags": ["aegis-bid", "slo"], "panels": PANELS,
    }
    return {
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": "aegis-dashboard-saude-do-leilao", "namespace": "monitoring",
                     "labels": {"grafana_dashboard": "1", "app.kubernetes.io/part-of": "aegis-bid"}},
        "data": {"saude-do-leilao.json": json.dumps(dashboard, indent=2, ensure_ascii=False)},
    }


def render() -> str:
    return "# GERADO por tools/dashboard_gen.py. NÃO edite à mão.\n" + yaml.safe_dump(
        build(), sort_keys=False, width=10_000, allow_unicode=True)


if __name__ == "__main__":
    sys.stdout.write(render())
