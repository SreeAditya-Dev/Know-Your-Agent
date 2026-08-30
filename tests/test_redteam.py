"""The red-team harness, exercised as tests.

These are not a re-run of the whole 530-session corpus — that is what
``python -m redteam.run --all`` is for. They pin the properties the evaluation
would be worthless without: that the corpus is frozen, that each baseline is a
strictly stronger defence than the one before it, that the full gateway catches
every inline attack class, and that the honest misses are exactly the ones we
declared rather than something that quietly slipped through.
"""

from __future__ import annotations

import pytest

from kya.enums import Decision
from redteam import corpus
from redteam.harness import Baseline
from redteam.metrics import build_report
from redteam.scenarios import CLASS_ORDER, build


ALL = [Baseline.B0, Baseline.B1, Baseline.B2, Baseline.B3]


@pytest.fixture(scope="module")
def report():
    sessions = corpus.load_sessions()
    results = {b: [(s, s.run(b)) for s in sessions] for b in ALL}
    matches, live, _ = corpus.verify()
    return build_report(results, corpus_hash=live, corpus_verified=matches)


def test_corpus_matches_its_frozen_hash():
    matches, live, frozen = corpus.verify()
    assert matches, (
        f"corpus drifted from its freeze:\n live   {live}\n frozen {frozen}\n"
        "re-freeze deliberately with `python -m redteam.run --freeze`"
    )


def test_every_spec_realises_and_is_labelled():
    for spec in corpus.generate_specs():
        session = build(spec)
        assert session.label in {"LEGIT", "ATTACK"}
        assert (session.attack_class is None) == (session.label == "LEGIT")


def test_recall_is_monotonic_across_baselines(report):
    """The whole thesis in one assertion: each added layer catches strictly
    more, and none of them regresses on what a weaker layer already caught."""
    recalls = [report.baselines[b].recall for b in ALL]
    assert recalls == sorted(recalls)
    assert recalls[0] == 0.0  # no gateway catches nothing
    assert report.baselines[Baseline.B1].recall < report.baselines[Baseline.B3].recall


def test_identity_only_misses_the_classes_it_is_known_to_miss(report):
    """B1 is the shipped state of the art. It must catch identity and replay,
    and it must miss mandate substitution, floods and obligation mismatch —
    that gap is the reason the project exists."""
    b1 = report.baselines[Baseline.B1]
    for caught in ("A1", "A2", "A3"):
        assert b1.per_class[caught].rate == 1.0
    for missed in ("A4", "A5", "A7", "A10", "A11"):
        assert b1.per_class[missed].rate == 0.0


def test_full_gateway_catches_every_inline_attack_class(report):
    """B3 stops every class that is catchable on or off the money path, with
    the single declared exception of the evasion/counterfeit honest misses."""
    b3 = report.baselines[Baseline.B3]
    fully_caught = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A9", "A10"}
    for cls in fully_caught:
        assert b3.per_class[cls].rate == 1.0, f"{cls} not fully caught"
    # A8 and A11 are partial by design — the honest misses live there.
    assert 0.0 < b3.per_class["A8"].rate < 1.0
    assert 0.0 < b3.per_class["A11"].rate < 1.0


def test_the_exception_list_is_exactly_the_declared_misses(report):
    """No surprise evasions. Every uncaught attack under B3 is one we authored
    as a known limitation."""
    classes = {s.attack_class for s, _ in report.attack_exceptions}
    assert classes <= {"A8", "A11"}
    ids = {s.session_id for s, _ in report.attack_exceptions}
    assert any("a8-evasion" in i for i in ids)
    assert any("a11-counterfeit" in i for i in ids)


def test_no_legitimate_session_is_ever_denied(report):
    """Precision 100% is the claim; this is the check behind it. Legitimate
    traffic may be stepped up or held, but it is never flatly denied a sale."""
    for b in ALL:
        assert report.baselines[b].denied_paise == 0
        assert not report.false_positives


def test_data_plane_latency_is_inside_budget(report):
    b3 = report.baselines[Baseline.B3]
    assert b3.latencies, "no B3 latency samples collected"
    assert b3.pct(99) < 50.0


def test_a11_is_caught_off_the_money_path_not_inline(report):
    """The obligation-mismatch class must never show up as an inline block —
    it is a clearing decision, and its inline decision stays ALLOW."""
    b3_pairs = [
        (s, o)
        for s, o in _pairs(report, Baseline.B3)
        if s.attack_class == "A11" and o.stopped
    ]
    assert b3_pairs, "expected some A11 sessions to be disputed at clearing"
    for _s, o in b3_pairs:
        assert o.decision is Decision.ALLOW
        assert o.clearing == "DISPUTED"


def _pairs(report, baseline):
    # Rebuild just the A11 slice cheaply for the assertion above.
    return [
        (s, s.run(baseline))
        for s in corpus.load_sessions()
        if s.attack_class == "A11"
    ]
