# Um único Dockerfile para os 3 serviços. Contexto de build: a pasta apps/
#   docker build -f apps/Dockerfile --build-arg SERVICE=bid -t aegis-bid/bid:dev apps/
#
# POR QUE multi-stage: as dependências são instaladas num estágio e copiadas
# para a imagem final, que não carrega pip nem cache. Imagem menor = menos
# superfície de ataque e scan do Trivy mais limpo.
FROM python:3.12-slim AS deps
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
ARG SERVICE
ENV SERVICE=${SERVICE} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/common
WORKDIR /app
COPY --from=deps /install /usr/local
COPY common ./common
COPY ${SERVICE} ./${SERVICE}

# POR QUE UID numérico: a política do Kyverno (fase 7) exige runAsNonRoot, e o
# Kubernetes só consegue verificar isso quando o USER é numérico.
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn ${SERVICE}.main:app --host 0.0.0.0 --port 8000"]
