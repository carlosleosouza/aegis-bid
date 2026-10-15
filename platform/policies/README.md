# Políticas Kyverno

Todas começam em `failureAction: Audit` (só REGISTRAM violações, não bloqueiam).
Na fase 7 você:
1. consulta os relatórios: `kubectl get policyreport -n aegis`
2. corrige o que aparecer
3. troca `Audit` por `Enforce` num commit (GitOps: a mudança de política é revisada e auditável)

Por que Audit primeiro? Ligar `Enforce` direto em um cluster em uso derruba
deploys legítimos. Audit mostra o impacto ANTES de bloquear.
