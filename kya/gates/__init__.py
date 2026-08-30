"""The inline gate pipeline.

Every gate here is deterministic and free of model calls. That is not a
convention — it is the property that lets the gateway promise that no LLM
output ever moves money, and it is asserted by the latency test.
"""

from kya.gates.base import BaseGate
from kya.gates.context import GateContext
from kya.gates.g0_replay import G0Replay
from kya.gates.g1_identity import G1Identity
from kya.gates.g2_mandate import G2Mandate
from kya.gates.g3_cart import G3Cart
from kya.gates.g4_envelope import G4Envelope, resolve_action
from kya.gates.g5_content import G5ContentThreat
from kya.gates.g6_adjudicate import adjudicate
from kya.gates.pipeline import Pipeline, default_pipeline

__all__ = [
    "BaseGate",
    "GateContext",
    "G0Replay",
    "G1Identity",
    "G2Mandate",
    "G3Cart",
    "G4Envelope",
    "G5ContentThreat",
    "resolve_action",
    "adjudicate",
    "Pipeline",
    "default_pipeline",
]
