"""Data-plane latency budget.

The claim that no money decision waits on a model is only meaningful if the
deterministic path is actually fast. This test enforces the docs/02 budget.

The threshold is the published p99 budget of 50 ms rather than the observed
figure (~10 ms), so the test guards against regressions without becoming
flaky on a loaded machine.
"""

from __future__ import annotations

import pytest

from kya.enums import Decision
from kya.simulation import (
    build_signed_request,
    make_cart,
    make_mandates,
    standard_sandbox,
)

BUDGET_P99_MS = 50.0
SAMPLES = 300


@pytest.fixture(scope="module")
def latencies() -> list[float]:
    sandbox, agent, principal = standard_sandbox()
    cart = make_cart()

    # Warm the directory cache, as a running gateway would be.
    sandbox.evaluate(
        build_signed_request(agent, make_mandates(agent, principal, cart), cart)
    )

    samples: list[float] = []
    for _ in range(SAMPLES):
        mandates = make_mandates(agent, principal, cart)
        envelope = sandbox.evaluate(build_signed_request(agent, mandates, cart))
        assert envelope.decision is Decision.ALLOW
        samples.append(envelope.latency_ms)

    return sorted(samples)


def _percentile(values: list[float], p: float) -> float:
    return values[max(0, int(len(values) * p) - 1)]


def test_p99_is_within_budget(latencies):
    assert _percentile(latencies, 0.99) < BUDGET_P99_MS


def test_p50_is_comfortably_inside_budget(latencies):
    assert _percentile(latencies, 0.50) < BUDGET_P99_MS / 4


#: Anything that could reach a model or the network. The inline path may not
#: touch these, transitively or otherwise.
FORBIDDEN_ON_MONEY_PATH = {"anthropic", "openai", "httpx", "requests", "urllib"}

INLINE_MODULES = (
    "kya/gates/g0_replay.py",
    "kya/gates/g1_identity.py",
    "kya/gates/g2_mandate.py",
    "kya/gates/g3_cart.py",
    "kya/gates/g6_adjudicate.py",
    "kya/gates/pipeline.py",
    "kya/gates/base.py",
    "kya/gates/context.py",
)


@pytest.mark.parametrize("module_path", INLINE_MODULES)
def test_no_model_or_network_import_on_the_money_path(module_path):
    """Structural guarantee, not a timing one.

    The latency budget is a *symptom* of the property that no money decision
    waits on a model. This asserts the property itself, by parsing the inline
    modules' imports rather than trusting a runtime name lookup — which would
    miss ``from anthropic import ...``.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    tree = ast.parse((root / module_path).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    offending = imported & FORBIDDEN_ON_MONEY_PATH
    assert not offending, f"{module_path} imports {offending} on the money path"
