# Fase 7: Segurança e políticas (Kyverno + NetworkPolicies)

## Objetivo
O cluster **recusa** (ou, primeiro, *denuncia*) o que viola as regras: imagem não assinada pelo seu CI, container rodando como root, workload sem limites ou sem dono, tráfego que ninguém autorizou. Entrega: a prova do bloqueio de uma imagem não assinada.

## Por quê
Segurança que depende de memória humana falha. Política como código é revisada em PR, versionada e aplicada igualmente a todos. A assinatura da Fase 1 só vale se alguém **verifica** na entrada.

## O que está implementado
| Política | Arquivo | O que exige |
|---|---|---|
| `aegis-verify-signed-images` | `platform/policies/verify-signed-images.yaml` | imagens `ghcr.io/<owner>/aegis-bid/*` assinadas (keyless) pelo workflow `ci.yml` da branch `main` |
| `aegis-disallow-root` | `platform/policies/disallow-root.yaml` | `runAsNonRoot`, sem escalonamento de privilégio, rootfs somente leitura |
| limites e labels | `platform/policies/require-limits-labels.yaml` | `resources.limits` e labels de dono |
| NetworkPolicies | `deploy/components/network-policies/` | `default-deny` + permissões explícitas (9 políticas) |

Os manifests da própria aplicação já satisfazem as políticas (há um teste: `test_workloads_satisfy_our_own_kyverno_policies`).

## Por que `Audit` primeiro, `Enforce` depois
Ligar `Enforce` direto num cluster em uso derruba deploys legítimos. `Audit` registra violações **sem bloquear**, mostrando o impacto *antes*. A troca para `Enforce` é um commit: revisável e auditável.

## Passos

### 1. Ligue a fase
```bash
make enable PHASE=7
git diff && git add -A && git commit -m "feat: ativa fase 7 (kyverno + netpol)" && git push
```
Instala o Kyverno nos **dois** clusters, as políticas e as NetworkPolicies. A verificação de assinatura precisa de saída para a internet (Fulcio/Rekor) a partir do cluster.

### 2. Leia os relatórios de política (modo Audit)
```bash
kubectl --context k3d-aegis-prod get policyreport -n aegis
kubectl --context k3d-aegis-prod get policyreport -n aegis -o wide
```
O esperado para as imagens vindas do seu CI é `pass`. Se aparecer `fail` na verificação de assinatura, confira: owner correto (`make configure`), nome do repo `aegis-bid`, nome do arquivo `ci.yml` e branch `main` (tudo isso faz parte da *identidade* da assinatura).

### 3. Mude para `Enforce` (por GitOps)
Em `platform/policies/*.yaml`, troque `failureAction: Audit` por `Enforce`; commit, push e espere o Argo CD aplicar.

### 4. A prova do bloqueio
```bash
# imagem pública qualquer, NÃO assinada por você, tentando rodar no namespace aegis:
kubectl --context k3d-aegis-prod -n aegis run intruso --image=ghcr.io/SEU-USUARIO/aegis-bid/bid:dev-nao-assinada
kubectl --context k3d-aegis-prod -n aegis run root-test --image=nginx --restart=Never
```
**Esperado**: o webhook do Kyverno **rejeita** com a mensagem da política. Guarde a saída para o README/entrevista.

### 5. NetworkPolicies: prove o isolamento
```bash
# um pod de teste SEM permissão não deve alcançar o Redis:
kubectl --context k3d-aegis-prod -n aegis run np-test --rm -it --image=busybox:1.36 --restart=Never -- \
  sh -c 'nc -zv -w 2 redis 6379; echo exit=$?'
```
Observação: o k3s embute um controlador de NetworkPolicy; se o teste *conectar*, confirme que as políticas foram aplicadas (`kubectl get netpol -n aegis`) e que o pod de teste carrega labels diferentes dos permitidos.

## Verificação
- `policyreport` limpo para a aplicação.
- Imagem não assinada e container root **rejeitados** em `Enforce`.
- Tráfego não autorizado ao Redis **bloqueado**.

## Atenção ao Enforce e ao Argo CD
Com `Enforce`, se o CI ainda não assinou a tag que está nos overlays (ou o PR de tags ainda não foi mesclado), o Pod é rejeitado e o app fica `Degraded`. Isso é a política funcionando; corrija a causa, não a política.

## Se der errado
| Sintoma | Causa provável |
|---|---|
| Tudo rejeitado após `Enforce` | identidade da assinatura diferente da política (owner/arquivo/branch) |
| Kyverno trava o cluster | `failurePolicy: Ignore` já protege o lab; em produção, avalie o risco do webhook |
| Verificação falha sem motivo claro | cluster sem saída para `rekor.sigstore.dev`/`fulcio` |

## Pergunta de entrevista
*"Como você garante que só código confiável roda no cluster?"* Pipeline único que assina por *digest* (keyless, via OIDC do GitHub) + SBOM como atestado; o cluster *verifica a identidade do assinante* na admissão; nenhuma chave de longo prazo para vazar.
