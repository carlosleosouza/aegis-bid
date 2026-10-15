import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import budget_gate  # noqa: E402
import chaos_director  # noqa: E402
import postmortem  # noqa: E402
import slo_gen  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def load_spec():
    with open(os.path.join(ROOT, "platform", "observability", "slos.yaml")) as f:
        return yaml.safe_load(f)


def test_slo_gen_burn_thresholds_for_999():
    doc = slo_gen.generate(load_spec())
    group = next(g for g in doc["spec"]["groups"] if g["name"] == "aegis-slo-bid-availability")
    alerts = {r["alert"]: r for r in group["rules"] if "alert" in r}
    # budget = 0.001  =>  14.4x = 0.0144 ; 6x = 0.006 ; 3x = 0.003 ; 1x = 0.001
    assert "> 0.0144" in alerts["AegisErrorBudgetFastBurn"]["expr"]
    assert "ratio_rate1h" in alerts["AegisErrorBudgetFastBurn"]["expr"]
    assert "ratio_rate5m" in alerts["AegisErrorBudgetFastBurn"]["expr"]
    assert "> 0.006" in alerts["AegisErrorBudgetModerateBurn"]["expr"]
    assert "> 0.003" in alerts["AegisErrorBudgetSlowBurn"]["expr"]
    assert "> 0.001" in alerts["AegisErrorBudgetVerySlowBurn"]["expr"]


def test_slo_gen_records_every_window_and_budget_remaining():
    doc = slo_gen.generate(load_spec())
    for group in doc["spec"]["groups"]:
        records = [r["record"] for r in group["rules"] if "record" in r]
        for w in slo_gen.WINDOWS:
            assert f"aegis:sli_error:ratio_rate{w}" in records
        assert "aegis:error_budget_remaining:ratio" in records


def test_slo_gen_output_is_valid_yaml_and_has_no_leftover_placeholder():
    text = slo_gen.render(slo_gen.generate(load_spec()))
    assert "{window}" not in text
    assert yaml.safe_load(text)["kind"] == "PrometheusRule"


def test_committed_generated_rules_are_up_to_date():
    path = os.path.join(ROOT, "platform", "observability", "slo-rules.generated.yaml")
    expected = slo_gen.render(slo_gen.generate(load_spec()))
    with open(path) as f:
        assert f.read() == expected, "rode `make slo-rules` e comite o resultado"


def test_budget_gate_policy():
    assert budget_gate.evaluate(0.9, 0.2, False)[0] is True
    assert budget_gate.evaluate(0.1, 0.2, False)[0] is False
    assert budget_gate.evaluate(0.1, 0.2, True)[0] is True       # correção passa
    assert budget_gate.evaluate(None, 0.2, False)[0] is True     # fail-open sem dados


def test_chaos_verdict():
    ss = {"max_error_ratio": 0.01, "max_p99_seconds": 0.3}
    assert chaos_director.verdict({"error_ratio": 0.0, "p99_seconds": 0.05}, ss)[0] is True
    ok, why = chaos_director.verdict({"error_ratio": 0.05, "p99_seconds": 0.9}, ss)
    assert ok is False and len(why) == 2
    assert chaos_director.verdict({"error_ratio": None, "p99_seconds": None}, ss)[0] is True


class FakeProm:
    def __init__(self, during):
        self.calls = []
        self.during = during

    def sli(self, window):
        self.calls.append(window)
        return self.during if len(self.calls) == 2 else {"error_ratio": 0.0, "p99_seconds": 0.05}


class FakeKube:
    def __init__(self):
        self.applied, self.deleted = [], []

    def apply(self, m):
        self.applied.append(m)

    def delete(self, m):
        self.deleted.append(m)


def test_run_experiment_applies_then_removes_fault_and_flags_refuted():
    exp = {"id": "t", "manifest": "chaos/experiments/kill-bid-pod.yaml", "duration_seconds": 10,
           "hypothesis": "h", "steady_state": {"max_error_ratio": 0.01, "max_p99_seconds": 0.3}}
    kube = FakeKube()
    res = chaos_director.run_experiment(
        exp, FakeProm({"error_ratio": 0.2, "p99_seconds": 0.1}), kube,
        sleep=lambda s: None, recovery_seconds=0)
    assert len(kube.applied) == 1 and kube.applied == kube.deleted
    assert res["confirmed"] is False
    assert any("REFUTADA" in e["event"] for e in res["events"])


def test_catalog_manifests_exist_and_are_valid():
    with open(os.path.join(ROOT, "chaos", "catalog.yaml")) as f:
        catalog = yaml.safe_load(f)
    assert len(catalog["experiments"]) >= 3
    for e in catalog["experiments"]:
        path = os.path.join(ROOT, e["manifest"])
        assert os.path.exists(path), path
        doc = yaml.safe_load(open(path))
        assert doc["apiVersion"] == "chaos-mesh.org/v1alpha1"
        assert e["hypothesis"] and e["steady_state"]


def test_postmortem_render_contains_key_sections():
    r = {"id": "kill-bid-pod", "hypothesis": "h", "duration_seconds": 60,
         "steady_state": {"max_error_ratio": 0.01, "max_p99_seconds": 0.3},
         "before": {"error_ratio": 0.0, "p99_seconds": 0.05},
         "during": {"error_ratio": 0.2, "p99_seconds": 0.5},
         "after": {"error_ratio": None, "p99_seconds": None},
         "client": {"requests": 100, "failures": 20, "client_error_ratio": 0.2},
         "confirmed": False, "violations": ["taxa de erro 0.2000 > limite 0.01"],
         "events": [{"ts": "2026-10-05T12:00:00+00:00", "event": "x"}],
         "finished_at": "2026-10-05T12:05:00+00:00"}
    md = postmortem.render(r)
    for s in ("# Postmortem", "REFUTADA", "## Linha do tempo", "## Causa raiz", "## Ações", "20.00%"):
        assert s in md
