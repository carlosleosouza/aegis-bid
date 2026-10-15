# Fase 0: Fundação (um comando, dois clusters, Argo CD)

## Objetivo
Subir do zero, com `make up`, dois clusters k3d (`prod` e `dr`), o Argo CD no `prod` e um HAProxy de borda. Ao final, **o Git passa a ser a fonte da verdade**: nada mais é aplicado com `kubectl apply` à mão.

## Por quê
- **Reprodutibilidade** é o que separa um projeto de portfólio de um lab bagunçado. Se você (ou um entrevistador) consegue destruir e recriar tudo com um comando, a plataforma é um *produto*, não uma coleção de comandos soltos.
- **Dois clusters** desde o início, porque DR, ApplicationSet e failover só fazem sentido com mais de um destino.
- **Rede Docker compartilhada (`aegis-net`)**: é ela que permite ao Argo CD do `prod` alcançar a API do `dr`, ao Redis do `dr` replicar do `prod` e ao HAProxy falar com os dois.

## Pré-requisitos
| Ferramenta | Para quê | Instalação |
|---|---|---|
| Docker | roda os nós do k3d | https://docs.docker.com/get-docker/ |
| k3d (>= 5.5) | clusters Kubernetes em containers | https://k3d.io |
| kubectl | falar com os clusters | https://kubernetes.io/docs/tasks/tools/ |
| git + make | fluxo GitOps e atalhos | gerenciador de pacotes do seu SO |
| k6 (fase 5) | gerador de carga | https://k6.io/docs/get-started/installation/ |
| python3 + pip | testes e `tools/` | `pip install -r apps/requirements-dev.txt pyyaml yamllint` |

**Memória**: o ideal é 16 GB livres para o Docker. O `make preflight` mede e avisa.

### Plano B de recursos (8 GB)
1. Em `bootstrap/k3d-prod.yaml`, use `agents: 1`.
2. Suba só o `prod` (`make network` e `k3d cluster create --config bootstrap/k3d-prod.yaml`), instale o Argo CD (`make argocd`) e comente a linha do DR em `bootstrap/register-clusters.sh`.
3. Faça as fases 1 a 7 só no `prod`. Deixe DR (fase 8) e a stack completa de observabilidade por último.

## Passos

### 1. Crie o repositório e configure o owner
1. No GitHub, crie um repositório **público** chamado exatamente `aegis-bid` (o nome entra na identidade da assinatura das imagens e nas URLs do Argo CD).
2. Na pasta do projeto:
   ```bash
   make configure OWNER=seu-usuario-github
   git init -b main && git add -A && git commit -m "feat: aegis-bid inicial"
   git remote add origin https://github.com/SEU-USUARIO/aegis-bid.git
   git push -u origin main
   ```
**Por quê agora**: o Argo CD lê *do GitHub*, não da sua pasta. Sem o push, o `make up` termina com um Application vazio.

### 2. Rode os testes (sem cluster)
```bash
make test
```
**Verificação**: tudo verde. Se falhar aqui, falharia mais tarde de forma muito mais confusa.

### 3. Suba tudo
```bash
make up
```
O alvo executa, na ordem: `preflight` → `clusters` → `argocd` → `register` → `root` → `edge`.

| Etapa | O que faz | Por quê |
|---|---|---|
| `preflight` | confere ferramentas, memória e placeholders | falhar cedo e com mensagem clara |
| `clusters` | cria `aegis-prod` (1 server + 2 agents) e `aegis-dr` (1+1) | dois destinos para o GitOps |
| `argocd` | instala o Argo CD com `--server-side` | os CRDs são grandes demais para o `apply` comum (limite de 256 KB da annotation) |
| `register` | cria Secrets rotulados `aegis.io/env` (`prod` ou `dr`) | o ApplicationSet escolhe clusters por esse label |
| `root` | aplica a Application raiz (app-of-apps) | **única coisa aplicada à mão**; ela observa `platform/apps/` |
| `edge` | HAProxy em `:8080` (prod, com `dr` como backup) | ponto de entrada único; base do failover |

## Verificação
```bash
kubectl --context k3d-aegis-prod get nodes          # 3 nós Ready
kubectl --context k3d-aegis-dr get nodes            # 2 nós Ready
make status                                         # Application aegis-root (vazia por enquanto)
docker ps --format '{{.Names}}' | grep aegis        # nós dos dois clusters + aegis-edge
make argocd-password                                # senha do usuário admin
make argocd-ui                                      # https://localhost:8443 (aceite o certificado autoassinado)
```
Na UI, o Application `aegis-root` deve estar **Synced/Healthy**. Em `Settings → Clusters` devem aparecer `aegis-prod` e `aegis-dr` com status **Successful**.

**Prova de reprodutibilidade**: `make down && make up`. Anote quanto tempo levou (use no README e na entrevista).

## Se der errado
| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `port is already allocated` (8080/8081/8082) | outro serviço na porta | pare-o ou troque as portas em `bootstrap/*.yaml` e no Makefile |
| `unknown apiVersion k3d.io/v1alpha5` | k3d antigo | atualize para >= 5.5 |
| Argo CD pods em `Pending` | pouca memória no Docker | aumente a memória nas preferências do Docker ou use o plano B |
| Cluster `aegis-dr` aparece com erro de conexão no Argo CD | rede/certificado | confira `docker network inspect aegis-net`; o registro usa `insecure: true` (LAB ONLY) |
| `make up` falha no `argocd` por timeout | download lento do manifest | rode `make argocd` de novo (é idempotente) |

## Pergunta de entrevista
*"Por que o Argo CD roda no cluster de produção e gerencia o DR? Qual o risco?"* O risco é o hub ser ponto único de falha: se o `prod` cair, o Argo CD cai junto. O DR continua rodando o que já tinha (a Fase 8 demonstra isso), mas deixa de receber mudanças. Mitigações reais: Argo CD em um terceiro cluster de gestão, ou uma instância no DR em modo standby.
