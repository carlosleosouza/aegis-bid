"""Instrumentação Prometheus compartilhada pelos serviços.

POR QUE existe: os SLOs (fase 3) e o Error Budget Gate (fase 4) são calculados
a partir destas duas métricas. Se o nome ou os labels mudarem, as regras de SLO
em platform/observability quebram. Trate este arquivo como um contrato.

  aegis_http_requests_total{service, route, method, code}
  aegis_http_request_duration_seconds_bucket{service, route, le}

O label `route` usa o TEMPLATE da rota ("/api/auctions/{auction_id}"), nunca o
caminho real. Caminho real = cardinalidade infinita = Prometheus morrendo.
"""
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "aegis_http_requests_total",
    "Total de requisições HTTP",
    ["service", "route", "method", "code"],
)

# Os buckets incluem 0.3 de propósito: o SLO de latência é "p99 < 300 ms" e o
# SLI é calculado como a fração de requisições com duração <= 0.3 s.
# Se 0.3 não fosse um limite de bucket, o SLI ficaria impreciso.
LATENCY = Histogram(
    "aegis_http_request_duration_seconds",
    "Duração das requisições HTTP em segundos",
    ["service", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0),
)

# Endpoints operacionais não entram nos SLIs: probes e scrapes não são tráfego
# de usuário e distorceriam a disponibilidade.
_IGNORED_ROUTES = {"/metrics", "/healthz", "/readyz"}


def instrument(app: FastAPI, service: str) -> None:
    """Registra o middleware de métricas e o endpoint /metrics."""

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        code = "500"  # se call_next levantar exceção, contamos como 5xx
        try:
            response = await call_next(request)
            code = str(response.status_code)
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            if path not in _IGNORED_ROUTES:
                LATENCY.labels(service, path).observe(time.perf_counter() - start)
                REQUESTS.labels(service, path, request.method, code).inc()

    @app.get("/metrics", include_in_schema=False)
    def _metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
