# Lições aprendidas (seu diário de erros)

> Em entrevista, *"me conte de um problema que você enfrentou"* vale mais do que qualquer diagrama. Registre **todo** tropeço aqui, no momento em que acontece: sintoma, causa, correção e o que você mudou para não repetir. Projeto sem erros registrados parece projeto que ninguém executou.

## Como registrar
Copie o modelo abaixo para cada ocorrência (mais recente no topo).

```markdown
### AAAA-MM-DD · Fase N · título curto
- **Sintoma**: o que você viu (cole a mensagem de erro/print)
- **Hipótese inicial**: o que você achou que era
- **Causa real**: o que era de fato
- **Correção**: o que mudou (commit/PR)
- **Prevenção**: teste, política, alerta ou documentação que impede de repetir
- **Tempo perdido**: ~X min
```

## Armadilhas já conhecidas (leia ANTES de executar a fase)
São decisões tomadas no código por causa de problemas reais deste tipo de plataforma. Quando você as encontrar na prática, registre acima com as **suas** palavras.

| Fase | Armadilha | Onde está tratada |
|---|---|---|
| 0 | CRDs do Argo CD estouram o `apply` comum (annotation > 256 KB) | `--server-side` no `make argocd` |
| 0 | Certificado da API do DR não casa com o nome DNS usado pelo Argo CD | `insecure: true` (LAB ONLY) em `bootstrap/register-clusters.sh` |
| 0 | HAProxy resolve DNS só no start e perde o cluster que reiniciou | `resolvers docker` em `bootstrap/haproxy.cfg` |
| 1 | Pacotes do GHCR nascem **privados**: `ImagePullBackOff` no cluster | torná-los públicos (Fase 1, passo 5) |
| 1 | Merge do PR de tags dispara outro build (loop) | `chore(deploy)` ignorado no job `build` |
| 2 | Tag `dev` não existe no registry antes do primeiro PR do CI | mesclar o PR **antes** de ligar a Fase 2 |
| 2 | Self-heal do Argo CD brigando com o KEDA/failover por `replicas` | `ignoreDifferences` + `RespectIgnoreDifferences` |
| 3 | Prometheus ignora ServiceMonitors sem o label do release Helm | `*SelectorNilUsesHelmValues: false` |
| 3 | Label `service` da métrica vira `exported_service` | `honorLabels: true` no ServiceMonitor |
| 3 | `route` com o caminho real = cardinalidade infinita | `route` usa o *template* da rota (`metrics.py`) |
| 4 | Gate que trava por falta de métrica vira motivo para ser desligado | *fail-open* (`or vector(1)`) |
| 4 | GitHub não alcança o Prometheus do notebook | gate local (`make gate`) ou runner self-hosted |
| 5 | O gerador de carga vira o gargalo e o teste mente | começar com `PEAK` baixo |
| 5 | Pool padrão do redis-py estoura (`MaxConnectionsError` → 503) em rajada e derruba lances válidos | `BlockingConnectionPool` em `redis_client.py` + `apps/tests/test_concurrency.py` |
| 6 | Hipótese escrita *depois* do experimento vira racionalização | hipótese no `chaos/catalog.yaml` |
| 7 | `Enforce` direto derruba deploys legítimos | `Audit` primeiro |
| 8 | Failback sem sincronizar DR → prod perde escritas | documentado na Fase 8 (não automatizado) |

## Ocorrências reais (preencha)

<!-- Mais recente no topo. -->
