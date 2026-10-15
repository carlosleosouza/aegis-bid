"""Verificações ESTRUTURAIS dos manifests (rodam sem cluster e sem kubectl).

Não substituem `kubectl kustomize` / `kubeconform` (o CI roda o primeiro), mas pegam
os erros mais comuns de quem edita YAML à mão: referência para arquivo inexistente,
selector que não casa com nenhum pod, patch apontando para recurso que não existe,
e violação das nossas próprias políticas do Kyverno.
"""
import glob
import os
import re
import shutil
import subprocess

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_all(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def base_resources():
    docs = []
    for p in glob.glob(os.path.join(ROOT, "deploy", "base", "*.yaml")):
        if not p.endswith("kustomization.yaml"):
            docs += load_all(p)
    return docs


def workloads():
    return [d for d in base_resources() if d["kind"] in ("Deployment", "Rollout")]


def test_every_yaml_file_parses():
    for p in glob.glob(os.path.join(ROOT, "**", "*.yaml"), recursive=True):
        if "/.git/" in p:
            continue
        load_all(p)  # levanta exceção se inválido


def test_kustomization_references_exist():
    for k in glob.glob(os.path.join(ROOT, "deploy", "**", "kustomization.yaml"), recursive=True):
        base = os.path.dirname(k)
        doc = load_all(k)[0]
        refs = list(doc.get("resources", []) or []) + list(doc.get("components", []) or [])
        refs += [p["path"] for p in doc.get("patches", []) or [] if "path" in p]
        for r in refs:
            target = os.path.normpath(os.path.join(base, r))
            assert os.path.exists(target), f"{k}: referência quebrada -> {r}"


def test_overlay_patch_targets_exist_in_base():
    names = {(d["kind"], d["metadata"]["name"]) for d in base_resources()}
    files = glob.glob(os.path.join(ROOT, "deploy", "overlays", "*", "kustomization.yaml")) + \
        glob.glob(os.path.join(ROOT, "deploy", "components", "*", "kustomization.yaml"))
    for k in files:
        for p in load_all(k)[0].get("patches", []) or []:
            t = p.get("target")
            if t:
                assert (t["kind"], t["name"]) in names, f"{k}: patch para recurso inexistente {t}"


def test_services_select_existing_pods_and_ports():
    docs = base_resources()
    pods = {d["metadata"]["name"]: d["spec"]["template"] for d in workloads() + [x for x in docs if x["kind"] == "Deployment"]}
    for svc in (d for d in docs if d["kind"] == "Service"):
        name = svc["metadata"]["name"]
        assert name in pods, f"Service {name} sem workload de mesmo nome"
        labels = pods[name]["metadata"]["labels"]
        for k, v in svc["spec"]["selector"].items():
            assert labels.get(k) == v, f"selector de {name} não casa com os pods"
        port_names = {p["name"] for c in pods[name]["spec"]["containers"] for p in c["ports"]}
        for p in svc["spec"]["ports"]:
            assert p["targetPort"] in port_names


def test_ingress_backends_exist():
    docs = base_resources()
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    ing = next(d for d in docs if d["kind"] == "Ingress")
    for rule in ing["spec"]["rules"]:
        for path in rule["http"]["paths"]:
            be = path["backend"]["service"]
            assert be["name"] in services
            assert be["port"]["name"] in [p["name"] for p in services[be["name"]]["spec"]["ports"]]


def test_workloads_satisfy_our_own_kyverno_policies():
    """Os pods do base PRECISAM passar nas políticas da fase 7, senão `Enforce`
    quebraria o deploy da própria aplicação."""
    for w in workloads():
        tpl = w["spec"]["template"]
        labels = tpl["metadata"]["labels"]
        assert labels.get("app.kubernetes.io/part-of") and labels.get("aegis/owner"), w["metadata"]["name"]
        assert tpl["spec"]["securityContext"]["runAsNonRoot"] is True
        for c in tpl["spec"]["containers"]:
            r = c["resources"]
            assert {"cpu", "memory"} <= set(r["requests"]) and {"cpu", "memory"} <= set(r["limits"])
            sc = c["securityContext"]
            assert sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True


def test_servicemonitor_selector_matches_services():
    sm = load_all(os.path.join(ROOT, "deploy/components/monitoring/servicemonitor.yaml"))[0]
    want = sm["spec"]["selector"]["matchLabels"]
    services = [d for d in base_resources() if d["kind"] == "Service"]
    matching = [s for s in services if all(s["metadata"]["labels"].get(k) == v for k, v in want.items())]
    assert {s["metadata"]["name"] for s in matching} >= {"bid-svc", "auction-svc", "settlement-svc"}


def test_chaos_selectors_match_real_app_labels():
    apps = {w["spec"]["template"]["metadata"]["labels"]["app"] for w in workloads()}
    for p in glob.glob(os.path.join(ROOT, "chaos", "experiments", "*.yaml")):
        doc = load_all(p)[0]
        assert doc["spec"]["selector"]["labelSelectors"]["app"] in apps, p
        if "target" in doc["spec"]:
            assert doc["spec"]["target"]["selector"]["labelSelectors"]["app"] in apps, p


def test_promql_metrics_and_labels_exist_in_app_code():
    """Se alguém renomear uma métrica no código, os SLOs e as análises quebram em
    silêncio (NaN). Este teste transforma o silêncio em falha de CI."""
    code = ""
    for p in glob.glob(os.path.join(ROOT, "apps", "**", "*.py"), recursive=True):
        if "/tests/" not in p:
            code += open(p).read()
    queries = open(os.path.join(ROOT, "platform/observability/slos.yaml")).read()
    queries += open(os.path.join(ROOT, "deploy/components/canary-analysis/analysis-templates.yaml")).read()
    metrics = set(re.findall(r"\b(aegis_[a-z_]+?)(?:_bucket|_count|_sum)?\{", queries))
    metrics |= set(re.findall(r"\b(aegis_[a-z_]+?)(?:_bucket|_count|_sum)?\[", queries))
    assert metrics
    for m in metrics:
        base_name = re.sub(r"_(total)$", "", m)  # Counter registra sem o sufixo _total
        assert m in code or base_name in code, f"métrica {m} não existe no código"
    assert '"/api/bids"' in code and 'service="bid-svc"' in queries and '"bid-svc"' in code


def test_enable_each_phase_in_a_scratch_copy(tmp_path):
    """Liga as fases 2..8 numa cópia e confere que nada fica pela metade."""
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    for n in range(2, 9):
        subprocess.run(["bash", "scripts/enable-phase.sh", str(n)], cwd=dst, check=True, capture_output=True)
    for k in ("prod", "dr"):
        text = (dst / "deploy" / "overlays" / k / "kustomization.yaml").read_text()
        assert not re.search(r"^#fase\d# ", text, re.M), f"marcador sobrando em {k}"
        doc = yaml.safe_load(text)
        for c in doc["components"]:
            assert os.path.exists(os.path.normpath(dst / "deploy" / "overlays" / k / c)), c
    activated = sorted(os.listdir(dst / "platform" / "apps"))
    assert len([a for a in activated if a.endswith(".yaml")]) == 8
    for f in activated:
        if f.endswith(".yaml"):
            load_all(dst / "platform" / "apps" / f)
