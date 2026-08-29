"""The evidence lattice must be a genuine partial order.

The project's central claim — that a model's opinion can never by itself clear
a settlement — is a consequence of these algebraic properties, not a policy.
If the lattice degrades into a total order, the claim silently becomes false,
so it is tested directly.
"""

from __future__ import annotations

import itertools

import pytest

from kya.evidence import (
    EvidenceClass as E,
    comparable,
    join,
    join_all,
    leq,
    meet,
    meet_all,
    meets_floor,
)

ALL = list(E)


class TestPartialOrderLaws:
    def test_reflexive(self):
        assert all(leq(c, c) for c in ALL)

    def test_antisymmetric(self):
        for a, b in itertools.product(ALL, repeat=2):
            if leq(a, b) and leq(b, a):
                assert a is b

    def test_transitive(self):
        for a, b, c in itertools.product(ALL, repeat=3):
            if leq(a, b) and leq(b, c):
                assert leq(a, c), f"{a} ⪯ {b} ⪯ {c} but not {a} ⪯ {c}"

    def test_self_is_bottom_and_proof_is_top(self):
        assert all(leq(E.SELF, c) for c in ALL)
        assert all(leq(c, E.PROOF) for c in ALL)


class TestIncomparability:
    def test_wit_and_rec_are_incomparable(self):
        """A witness observed an event; a receipt records that a system
        processed one. Neither dominates, which is why this is a poset."""
        assert not leq(E.WIT, E.REC)
        assert not leq(E.REC, E.WIT)
        assert not comparable(E.WIT, E.REC)

    def test_every_other_pair_is_comparable(self):
        incomparable = [
            (a, b)
            for a, b in itertools.combinations(ALL, 2)
            if not comparable(a, b)
        ]
        assert incomparable == [(E.WIT, E.REC)]


class TestMeetAndJoin:
    def test_meet_of_incomparable_pair(self):
        assert meet(E.WIT, E.REC) is E.SIGN

    def test_join_of_incomparable_pair(self):
        assert join(E.WIT, E.REC) is E.ATT

    def test_meet_is_a_lower_bound(self):
        for a, b in itertools.product(ALL, repeat=2):
            m = meet(a, b)
            assert leq(m, a) and leq(m, b)

    def test_join_is_an_upper_bound(self):
        for a, b in itertools.product(ALL, repeat=2):
            j = join(a, b)
            assert leq(a, j) and leq(b, j)

    def test_meet_and_join_commute(self):
        for a, b in itertools.product(ALL, repeat=2):
            assert meet(a, b) is meet(b, a)
            assert join(a, b) is join(b, a)

    def test_meet_all_takes_the_weakest_link(self):
        """Multi-hop provenance is only as admissible as its weakest step."""
        assert meet_all([E.PROOF, E.REC, E.SELF]) is E.SELF
        assert meet_all([E.REC, E.ATT]) is E.REC

    def test_join_all_takes_the_strongest_survivor(self):
        assert join_all([E.SELF, E.REC]) is E.REC

    def test_empty_folds_are_bottom(self):
        assert meet_all([]) is E.SELF
        assert join_all([]) is E.SELF


class TestAdmissibilityFloor:
    @pytest.mark.parametrize("llm_basis", [E.SELF, E.SIGN])
    def test_llm_cannot_clear_a_receipt_floor_obligation(self, llm_basis):
        """The load-bearing assertion of the whole design.

        The semantic verifier declares SELF or SIGN. An obligation whose floor
        is REC therefore cannot be cleared by it, at any confidence.
        """
        assert not meets_floor(llm_basis, E.REC)

    def test_receipt_evidence_clears_a_receipt_floor(self):
        assert meets_floor(E.REC, E.REC)

    def test_witness_does_not_substitute_for_receipt(self):
        """Incomparability has teeth: a witness statement does not satisfy a
        floor that specifically demands an external system's receipt."""
        assert not meets_floor(E.WIT, E.REC)

    def test_attestation_clears_both_incomparable_floors(self):
        assert meets_floor(E.ATT, E.REC)
        assert meets_floor(E.ATT, E.WIT)

    def test_floor_of_self_admits_everything(self):
        assert all(meets_floor(c, E.SELF) for c in ALL)
