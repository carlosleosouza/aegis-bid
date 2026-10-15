# Fase 8: Disaster Recovery, RTO/RPO medidos e postmortem real

## Objetivo
Derrubar o cluster primário **inteiro** e mostrar o serviço voltando pelo DR, com **RTO e RPO medidos**, não estimados. Entrega: números reais e um postmortem do exercício.

## Por quê
Um plano de DR que nunca foi executado é uma hipótese. Medir RTO/RPO transforma "temos DR" em "perdemos X segundos de serviço e Y de dados".

- **RTO** (Recovery Time Objective): quanto tempo o serviço fica fora do ar.
- **RPO** (Recovery Point Objective): quanto *dado* se perde (aqui: o atraso da replicação do Redis, em bytes do log de replicação, no instante da falha).

## Estratégia: "pilot light"
O DR mantém só o **Redis** ligado e replicando do primário; auction, bid e settlement ficam com **0 réplicas** (economia de recursos). No failover, o script promove o Redis e liga os serviços. É a estratégia mais barata; o preço é um RTO maior que o de um standby "quente" (veja o trade-off abaixo).

## Como funciona
1. `redis-expose` (prod): um `Service` NodePort **30379** expõe o Redis do primário na rede Docker `aegis-net`.
2. `dr-replication` (dr): o Redis do DR sobe com `--replicaof k3d-aegis-prod-server-0 30379`.
3. HAProxy (`bootstrap/haproxy.cfg`): health check **na API real** (`GET /api/auctions`, esperando 200). O DR sem aplicação responde erro e fica `DOWN`; o `prod` é o servidor normal e o `dr` é `backup`. Se o primário falhar, o tráfego vai para o DR **sem o cliente mudar nada**.
4. `scripts/failover.sh` orquestra o desastre e mede.

## Passos

### 1. Ligue a fase
```bash
make enable PHASE=8
git diff && git add -A && git commit -m "feat: ativa fase 8 (replicação + failover)" && git push
```
Espere o Argo CD aplicar nos dois clusters (a mudança do Redis do DR recria o pod).

### 2. Confirme a replicação ANTES do desastre
```bash
kubectl --context k3d-aegis-dr -n aegis exec deploy/redis -- redis-cli info replication | head -12
# esperado: role:slave, master_link_status:up
```
Crie um leilão pela borda e confirme que o dado aparece no Redis do DR:
```bash
curl -s -H 'Host: aegis.localhost' -X POST http://localhost:8090/api/auctions -H 'content-type: application/json' \
  -d '{"title":"antes-do-desastre","starting_price":100,"duration_seconds":3600}'
kubectl --context k3d-aegis-dr -n aegis exec deploy/redis -- redis-cli dbsize
```

### 3. (Opcional, mas é o que torna o RPO interessante) carga durante a falha
Em outro terminal: `make lightning PEAK=200`. Com escrita em andamento, o atraso de replicação costuma ser diferente de zero.

### 4. O desastre
```bash
make failover
```
O script:
1. registra o estado antes (borda responde, offsets de replicação, RPO aproximado em bytes);
2. **para o cluster primário inteiro** (`k3d cluster stop aegis-prod`, inclusive o Argo CD);
3. promove o Redis do DR (`replicaof no one`);
4. liga os serviços do DR (escala deploys/rollout);
5. espera a borda voltar a responder **200** (agora via DR) e imprime o **RTO**.

Anote os números. Eles são a entrega.

### 5. Verifique que o DR serve de verdade
```bash
curl -s -H 'Host: aegis.localhost' http://localhost:8090/api/auctions          # lista o que foi replicado
```
Quais leilões/lances **não** aparecem? Isso é o seu RPO real (os que ainda não tinham sido replicados).

### 6. Failback
```bash
make restore-prod        # k3d cluster start aegis-prod
```
**Atenção**: o primário volta com os dados *antigos*, e o DR agora tem os dados mais novos. Um failback correto exige sincronizar na direção **DR → prod** (reconfigurar o Redis do prod como réplica do DR, esperar convergir e só então inverter). O script não faz isso: é uma limitação deliberada e um excelente assunto de entrevista (risco de *split-brain* e perda de escrita se voltar sem sincronizar).

### 7. Postmortem do exercício
Escreva (no formato sem culpa de `postmortems/`) o que aconteceu, os números de RTO/RPO, o que surpreendeu e as ações. Se quiser usar o gerador, rode antes um experimento com `make chaos` e `make postmortem` para ter o modelo preenchido.

## Verificação
- RTO medido (falha → primeiro 200 pela borda) e RPO aproximado (atraso em bytes) registrados.
- Após o failover, a API do DR aceita lances.
- Você explica por que o Argo CD ficou fora e o que isso significa (o DR continua rodando o que já tinha, mas não recebe mudanças).

## Se der errado
| Sintoma | Causa provável |
|---|---|
| `master_link_status:down` no DR | DR não alcança `k3d-aegis-prod-server-0:30379`: confirme a fase 8 ligada no prod (Service NodePort) e a rede `aegis-net` |
| RTO muito alto | serviços do DR demoram a ficar *Ready* (imagens a baixar): faça um *pull* prévio ou mantenha 1 réplica quente |
| Borda nunca volta a 200 | confira as imagens no DR (tags/pacote público) e o Ingress/Traefik no DR |
| `curl` sem resolver `aegis.localhost` | `127.0.0.1 aegis.localhost` no `/etc/hosts` |

## Trade-offs para discutir
- **Pilot light vs warm standby**: custo menor, RTO maior. Standby quente (réplicas ligadas, consumidores pausados) corta o RTO, e custa mais.
- **Replicação assíncrona**: nunca RPO = 0. Replicação síncrona (ex.: `WAIT`) reduz a perda e aumenta a latência de escrita.
- **Hub único**: o Argo CD no `prod` é ponto único de falha da *entrega*, não da execução.

## Extensões (não implementadas)
- **FinOps** com OpenCost (custo por serviço e por leilão processado).
- Automatizar o failback com sincronização reversa.

## Pergunta de entrevista
*"Como você mediu RTO e RPO?"* Com um exercício real: derrubei o primário, cronometrei até o primeiro 200 pela borda (RTO) e comparei os offsets de replicação no instante da falha (RPO). Tenha os seus números prontos e diga o que **não** está coberto (failback, split-brain).
