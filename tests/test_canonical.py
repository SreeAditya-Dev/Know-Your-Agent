"""Canonical serialization.

Cart binding only works if both parties hash the same bytes for the same
logical cart. Key ordering, float formatting and timezone representation are
the three usual ways independent implementations disagree, so all three are
pinned here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kya.canonical import CanonicalizationError, canonicalize, digest
from kya.simulation import make_cart


class TestDeterminism:
    def test_key_order_does_not_affect_output(self):
        assert canonicalize({"b": 1, "a": 2}) == canonicalize({"a": 2, "b": 1})

    def test_nested_key_order_does_not_affect_output(self):
        assert canonicalize({"x": {"b": 1, "a": 2}}) == canonicalize(
            {"x": {"a": 2, "b": 1}}
        )

    def test_list_order_does_affect_output(self):
        """Sequence is meaningful; only mapping order is not."""
        assert canonicalize([1, 2]) != canonicalize([2, 1])

    def test_no_insignificant_whitespace(self):
        assert canonicalize({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'

    def test_equal_carts_hash_equal(self):
        assert make_cart().content_hash() == make_cart().content_hash()

    def test_differing_carts_hash_differently(self):
        a = make_cart(items=[("SKU-A", "A", 1, 100_00)])
        b = make_cart(items=[("SKU-A", "A", 1, 100_01)])
        assert a.content_hash() != b.content_hash()


class TestTimestamps:
    def test_timezones_normalize_to_utc(self):
        utc = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
        ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert canonicalize({"t": utc}) == canonicalize({"t": ist})

    def test_microseconds_are_dropped(self):
        a = datetime(2026, 8, 30, 12, 0, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 8, 30, 12, 0, 0, 999_999, tzinfo=timezone.utc)
        assert canonicalize({"t": a}) == canonicalize({"t": b})


class TestFloatRejection:
    def test_floats_are_refused(self):
        """Money is integer paise precisely so canonicalization never has to
        reason about float formatting."""
        with pytest.raises(CanonicalizationError, match="integer paise"):
            canonicalize({"amount": 10.5})

    def test_nested_floats_are_refused(self):
        with pytest.raises(CanonicalizationError):
            canonicalize({"cart": {"total": 1.0}})


class TestDigest:
    def test_is_stable_hex_sha256(self):
        d = digest({"a": 1})
        assert len(d) == 64
        assert d == digest({"a": 1})
        assert int(d, 16) >= 0  # valid hex
