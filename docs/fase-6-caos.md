# Fase 6: Caos com método científico (Chaos Mesh + Chaos Director)

## Objetivo
Executar experimentos de falha **com hipótese escrita antes**, medir o impacto nos SLIs e registrar o veredito. Entrega: pelo menos um experimento completo (hipótese, números antes/durante/depois, veredito e postmortem).

## Por quê
Resiliência *provada* vale mais que resiliência *prometida*. Caos sem hipótese é só vandalismo; o valor está em **prever** o comportamento e ser surpreendido quando a previsão falha. **Hipótese refutada não é fracasso: é a descoberta que justifica o experimento.**

## O ciclo do Chaos Director (`tools/chaos_director.py`)
1. Hipótese e estado estável vêm de `chaos/catalog.yaml` (escritos antes).
2. Mede os SLIs **antes** (taxa de erro e p99 do `bid-svc` via Prometheus).
3. Gera tráfego leve (sem requisições, os SLIs seriam `NaN`) e aplica o CR do Chaos Mesh.
4. Mede **durante**; remove a falha; espera a recuperação; mede **depois**.
5. Veredito (confirmada/refutada) e JSON em `postmortems/data/`.

## Os 5 experimentos
| ID | Falha | Hipótese resumida |
|---|---|---|
| `kill-bid-pod` | mata 1 pod do bid | réplicas + readiness mantêm erro < 1% e p99 < 300 ms |
| `kill-settlement-pod` | mata 1 pod do settlement | a API não é afetada; nenhum lance perdido nem aplicado 2x (`XAUTOCLAIM` + idempotência) |
| `auction-svc-outage` | auction-svc fora 60 s | o bid continua aceitando em leilões conhecidos (cache de `ends_at_ms`) |
| `bid-latency-500ms` | +500 ms de latência de rede no bid | **controle**: o p99 *deve* estourar, provando que o SLO detecta |
| `bid-redis-partition` | isola bid ↔ Redis 45 s | falha **rápido** (timeouts curtos), sem pendurar conexões, e recupera sozinho |

## Passos

### 1. Ligue a fase
```bash
make enable PHASE=6
git diff && git add -A && git commit -m "feat: ativa fase 6 (chaos mesh)" && git push
```

### 2. Prepare o terminal
```bash
kubectl config use-context k3d-aegis-prod       # o Chaos Director usa o contexto atual do kubectl
make prom-ui                                    # em outro terminal (o Director lê o Prometheus em :9090)
make chaos-list
```
Se o Director não alcançar `aegis.localhost`, adicione `127.0.0.1 aegis.localhost` ao `/etc/hosts`.

### 3. Escreva a SUA previsão antes de rodar
Para o primeiro experimento, anote no `docs/LICOES.md`: *"Eu espero que ... porque ..."* (inclua números). Só então rode:
```bash
make chaos ID=kill-bid-pod
```

### 4. Leia o veredito e gere o postmortem
```bash
ls postmortems/data/
make postmortem FILE=postmortems/data/<arquivo>.json
```
O postmortem sai em Markdown, **sem culpa** (descreve o sistema, não pessoas). A parte mecânica (linha do tempo, números, veredito) vem pronta; **você preenche** causa raiz e ações, que exigem julgamento. Com `--open-pr` (via CLI `gh`) o relatório vira um PR.

### 5. Rode os demais, em ordem de "surpresa"
1. `kill-settlement-pod`: confirme à mão que não houve perda nem duplicidade (veja abaixo).
2. `auction-svc-outage`
3. `bid-latency-500ms`: **o controle**: o alerta de burn rate deve disparar. Veja `Status → Rules` no Prometheus.
4. `bid-redis-partition`: erros são *esperados*; observe a recuperação.

**Verificação manual de duplicidade (experimento 1 do settlement)**:
```bash
kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli xlen bids
kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli xpending bids settlement
kubectl --context k3d-aegis-prod -n aegis exec deploy/redis -- redis-cli --scan --pattern 'processed:*' | wc -l
```
Cada `bid_id` aceito deve ter **uma** chave `processed:*`; mensagens pendentes são reivindicadas pelos pods vivos após `min_idle` (30 s).

## Verificação
- Um JSON em `postmortems/data/` e um `.md` em `postmortems/`.
- Você sabe dizer, para cada experimento, se a hipótese foi confirmada ou refutada e **por quê**.

## Se der errado
| Sintoma | Causa provável |
|---|---|
| `kubectl apply` falha com "no matches for kind PodChaos" | Chaos Mesh ainda instalando; espere o Application ficar *Healthy* |
| SLIs `sem dados` | Prometheus não alcançado em `localhost:9090` (`make prom-ui`) ou sem tráfego |
| NetworkChaos/PodChaos sem efeito | o `chaos-daemon` precisa falar com o containerd do k3s: o socket está em `platform/catalog/fase6-chaos-mesh.yaml` (`/run/k3s/containerd/containerd.sock`); confira os logs do `chaos-daemon` |

## Pergunta de entrevista
*"Qual experimento refutou sua hipótese e o que você mudou?"* Tenha uma resposta real, com números, vinda do seu `docs/LICOES.md`. É a pergunta onde projetos de portfólio se separam.
