import os

import redis


def make_redis() -> redis.Redis:
    """Cliente Redis configurado por variável de ambiente.

    Os timeouts curtos são intencionais: sob caos de rede (fase 6) preferimos
    falhar rápido e devolver 503 a deixar threads penduradas.

    POR QUE BlockingConnectionPool: o pool padrão do redis-py tem limite de
    conexões e, ao estourar, LEVANTA `MaxConnectionsError` na hora. Sob rajada
    (o "Leilão Relâmpago") isso vira 503 para lances perfeitamente válidos.
    O BlockingConnectionPool faz a requisição ESPERAR por uma conexão livre
    (backpressure) e só falha se a espera passar de `REDIS_POOL_TIMEOUT`.
    Foi reproduzido: 200 retries simultâneos da mesma Idempotency-Key geravam
    37 respostas 503 com o pool padrão (ver apps/tests/test_concurrency.py).
    """
    pool = redis.BlockingConnectionPool.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
        max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "64")),
        timeout=float(os.getenv("REDIS_POOL_TIMEOUT", "1.0")),  # espera por conexão livre
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        health_check_interval=15,
    )
    return redis.Redis(connection_pool=pool)
