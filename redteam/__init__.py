"""The red-team evaluation harness.

Day 5 of the build plan, and the artifact that carries the panel interview:
a measured comparison of what identity-only defence — the shipped state of the
art — fails to catch, against the full KYA gateway.

Nothing here reaches for a network or a model. Every scenario runs against an
in-process sandbox merchant with in-process fixtures, and every attack is a
perturbation of traffic that would otherwise pass, so the gate that fires is
the gate under test. See ``docs/05-evaluation.md``.
"""

from __future__ import annotations

from redteam.harness import Baseline, Outcome, Session
from redteam.metrics import BaselineReport, Report

__all__ = ["Baseline", "Outcome", "Session", "BaselineReport", "Report"]
