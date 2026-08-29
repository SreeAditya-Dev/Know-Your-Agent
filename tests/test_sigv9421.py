"""RFC 9421 parsing and signature-base construction.

Compatibility with Web Bot Auth and Visa TAP is the reason agents built for
those protocols work here unmodified, so the wire format is pinned by tests
rather than left to whatever the simulator happens to emit.
"""

from __future__ import annotations

import pytest

from kya.sigv9421 import (
    SignatureParseError,
    build_signature_base,
    parse_signature_agent,
    parse_signature_header,
    parse_signature_input,
)
from kya.simulation import (
    make_cart,
    make_mandates,
    build_signed_request,
    standard_sandbox,
)

RAW_INPUT = (
    'sig1=("@method" "@authority" "@path" "content-digest")'
    ';created=1788000000;keyid="agent-key-1";alg="ed25519"'
    ';nonce="abc123";expires=1788000300;tag="web-bot-auth"'
)


class TestParseSignatureInput:
    def test_extracts_components_and_params(self):
        parsed = parse_signature_input(RAW_INPUT)["sig1"]

        assert parsed.params.covered_components == [
            "@method",
            "@authority",
            "@path",
            "content-digest",
        ]
        assert parsed.params.key_id == "agent-key-1"
        assert parsed.params.algorithm == "ed25519"
        assert parsed.params.created == 1788000000
        assert parsed.params.expires == 1788000300
        assert parsed.params.nonce == "abc123"
        assert parsed.params.tag == "web-bot-auth"

    def test_preserves_raw_params_verbatim(self):
        """RFC 9421 requires the signature base to echo Signature-Input
        byte-for-byte, so it must be preserved rather than re-serialized."""
        parsed = parse_signature_input(RAW_INPUT)["sig1"]
        assert parsed.raw_params == RAW_INPUT.split("=", 1)[1]

    def test_missing_keyid_is_rejected(self):
        with pytest.raises(SignatureParseError, match="keyid"):
            parse_signature_input('sig1=("@method");created=1788000000')

    def test_missing_created_is_rejected(self):
        with pytest.raises(SignatureParseError, match="created"):
            parse_signature_input('sig1=("@method");keyid="k"')

    def test_missing_component_list_is_rejected(self):
        with pytest.raises(SignatureParseError, match="covered-component"):
            parse_signature_input('sig1=;created=1;keyid="k"')

    def test_commas_inside_the_component_list_do_not_split_labels(self):
        raw = (
            'a=("@method" "@path");created=1;keyid="k1", '
            'b=("@authority");created=2;keyid="k2"'
        )
        parsed = parse_signature_input(raw)
        assert set(parsed) == {"a", "b"}
        assert parsed["a"].params.key_id == "k1"
        assert parsed["b"].params.key_id == "k2"


class TestParseSignatureHeader:
    def test_decodes_byte_sequence_form(self):
        assert parse_signature_header("sig1=:YWJj:")["sig1"] == b"abc"

    def test_bare_base64_without_colons_is_rejected(self):
        with pytest.raises(SignatureParseError, match="byte sequence"):
            parse_signature_header("sig1=YWJj")

    def test_bad_base64_is_rejected(self):
        with pytest.raises(SignatureParseError, match="base64"):
            parse_signature_header("sig1=:not!valid!:")


class TestSignatureBase:
    def test_lines_follow_the_covered_component_order(self):
        sandbox, agent, principal = standard_sandbox()
        cart = make_cart()
        request = build_signed_request(
            agent, make_mandates(agent, principal, cart), cart
        )

        parsed = parse_signature_input(request.signature_input_raw)["sig1"]
        base = build_signature_base(request, parsed).decode()
        lines = base.split("\n")

        assert lines[0] == '"@method": POST'
        assert lines[1] == f'"@authority": {request.authority}'
        assert lines[2] == f'"@path": {request.path}'
        assert lines[3].startswith('"content-digest": sha-256=:')
        assert lines[-1].startswith('"@signature-params": (')

    def test_no_trailing_newline(self):
        sandbox, agent, principal = standard_sandbox()
        cart = make_cart()
        request = build_signed_request(
            agent, make_mandates(agent, principal, cart), cart
        )
        parsed = parse_signature_input(request.signature_input_raw)["sig1"]

        assert not build_signature_base(request, parsed).endswith(b"\n")

    def test_absent_covered_header_is_an_error(self):
        sandbox, agent, principal = standard_sandbox()
        cart = make_cart()
        request = build_signed_request(
            agent, make_mandates(agent, principal, cart), cart
        )
        del request.headers["content-digest"]

        parsed = parse_signature_input(request.signature_input_raw)["sig1"]
        with pytest.raises(SignatureParseError, match="absent from request"):
            build_signature_base(request, parsed)

    def test_unsupported_derived_component_is_an_error(self):
        sandbox, agent, principal = standard_sandbox()
        cart = make_cart()
        request = build_signed_request(
            agent, make_mandates(agent, principal, cart), cart
        )
        parsed = parse_signature_input(request.signature_input_raw)["sig1"]
        parsed.params.covered_components = ["@query-param"]

        with pytest.raises(SignatureParseError, match="unsupported derived"):
            build_signature_base(request, parsed)


class TestSignatureAgent:
    def test_strips_structured_field_quoting(self):
        assert parse_signature_agent('"https://a.example"') == "https://a.example"

    def test_passes_through_unquoted(self):
        assert parse_signature_agent("https://a.example") == "https://a.example"

    def test_empty_and_none_yield_none(self):
        assert parse_signature_agent(None) is None
        assert parse_signature_agent('""') is None
