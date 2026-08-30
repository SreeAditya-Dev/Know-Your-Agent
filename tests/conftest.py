from __future__ import annotations

import pytest

from kya.schemas import AgentRequest, Cart, MandateBundle
from kya.simulation import (
    AgentIdentity,
    Principal,
    Sandbox,
    build_signed_request,
    make_cart,
    make_mandates,
    standard_sandbox,
)


@pytest.fixture
def sandbox_bundle() -> tuple[Sandbox, AgentIdentity, Principal]:
    return standard_sandbox()


@pytest.fixture
def sandbox(sandbox_bundle) -> Sandbox:
    return sandbox_bundle[0]


@pytest.fixture
def agent(sandbox_bundle) -> AgentIdentity:
    return sandbox_bundle[1]


@pytest.fixture
def principal(sandbox_bundle) -> Principal:
    return sandbox_bundle[2]


@pytest.fixture
def cart() -> Cart:
    return make_cart()


@pytest.fixture
def mandates(agent, principal, cart) -> MandateBundle:
    return make_mandates(agent, principal, cart)


@pytest.fixture
def good_request(agent, mandates, cart) -> AgentRequest:
    """A request that should pass every gate."""
    return build_signed_request(agent, mandates, cart)


# --- Day 3: obligations, rail, gateway ---------------------------------------
#
# Drawn off the sandbox rather than constructed independently, so a test is
# always exercising the same wiring the gateway ships with. Fixtures that build
# their own ledger would happily pass while the real assembly was broken.


@pytest.fixture
def merchant(sandbox):
    return sandbox.merchant


@pytest.fixture
def ledger(sandbox):
    return sandbox.ledger


@pytest.fixture
def rail(sandbox):
    return sandbox.rail


@pytest.fixture
def gateway(sandbox):
    return sandbox.gateway()
