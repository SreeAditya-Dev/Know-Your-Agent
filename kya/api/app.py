"""KYA's FastAPI routes and server-rendered operator dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kya.api.state import KYAAppState, StoredDecision
from kya.enums import Decision
from kya.rails.webhooks import WebhookRejected
from kya.schemas import AgentRequest, DisputeClaim, EvidenceEnvelope


_ROOT = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_ROOT / "templates"))


def get_state(request: Request) -> KYAAppState:
    return cast(KYAAppState, request.app.state.kya)


StateDep = Annotated[KYAAppState, Depends(get_state)]


def _json_model(model: Any | None) -> dict[str, Any] | None:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return jsonable_encoder(model)


def _decision_summary(item: StoredDecision) -> dict[str, Any]:
    envelope = item.envelope
    return {
        "decision_id": envelope.decision_id,
        "decision": envelope.decision.value,
        "agent_id": envelope.agent_id,
        "reason_codes": envelope.reason_codes,
        "obligation_id": envelope.obligation_id,
        "latency_ms": envelope.latency_ms,
        "decided_at": envelope.decided_at,
        "explanation": envelope.explanation,
    }


def _clearing_summary(result: Any) -> dict[str, Any]:
    return {
        "obligation_id": result.obligation.obligation_id,
        "agent_id": result.obligation.agent_id,
        "finality": result.finality.finality.value,
        "performance_verdict": result.decision.performance_verdict,
        "explanation": result.explain(),
    }


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "decision": _json_model(result.envelope),
        "obligation": _json_model(result.obligation),
        "order": result.order,
        "refund": result.refund,
        "anchor": _json_model(result.anchor),
        "rail_error": result.rail_error,
        "replayed": result.replayed,
    }


def _dashboard_metrics(state: KYAAppState) -> dict[str, Any]:
    benchmark = state.benchmark()
    b3 = benchmark["baselines"]["B3"]
    decisions = state.ordered_decisions()
    ledger = state.sandbox.ledger.verify()
    clearing_results = state.ordered_clearing_results()
    return {
        "benchmark": {
            "sessions": benchmark["n_sessions"],
            "attacks": benchmark["n_attacks"],
            "recall": b3["recall"],
            "precision": b3["precision"],
            "p99": b3["latency_p99"],
        },
        "operations": {
            "decisions": len(decisions),
            "quarantined": sum(
                item.envelope.decision is Decision.QUARANTINE for item in decisions
            ),
            "obligations": len(state.sandbox.ledger.open_obligations()),
            "ledger_ok": ledger.ok,
            "ledger_entries": ledger.entries,
            "clearing_disputed": sum(
                result.disputed for result in clearing_results
            ),
        },
    }


def _stored_or_404(state: KYAAppState, decision_id: str) -> StoredDecision:
    item = state.decisions.get(decision_id)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown decision")
    return item


def create_app(state: KYAAppState | None = None) -> FastAPI:
    app = FastAPI(
        title="Know-Your-Agent",
        version="0.1.0",
        description="Obligation-clearing gateway for agentic commerce on Razorpay rails.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.kya = state or KYAAppState.demo()
    app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")

    api = APIRouter(prefix="/v1", tags=["KYA API"])

    @api.get("/health")
    def health(state: StateDep) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "sandbox",
            "decisions": len(state.decisions),
            "ledger_entries": state.sandbox.ledger.verify().entries,
        }

    @api.post("/agent/orders")
    def create_order(request: AgentRequest, state: StateDep) -> dict[str, Any]:
        return _result_payload(state.create_order(request))

    @api.post("/agent/inspect")
    def inspect_agent_request(request: AgentRequest, state: StateDep) -> dict[str, Any]:
        envelope = state.inspect(request)
        return {"decision": _json_model(envelope)}

    @api.get("/decisions")
    def list_decisions(state: StateDep) -> list[dict[str, Any]]:
        return [_decision_summary(item) for item in state.ordered_decisions()]

    @api.get("/decisions/{decision_id}")
    def get_decision(decision_id: str, state: StateDep) -> dict[str, Any]:
        item = _stored_or_404(state, decision_id)
        return {
            "request": _json_model(item.request),
            "decision": _json_model(item.envelope),
            "result": _result_payload(item.result) if item.result else None,
        }

    @api.get("/decisions/{decision_id}/replay")
    def replay_record(decision_id: str, state: StateDep) -> dict[str, Any]:
        item = _stored_or_404(state, decision_id)
        return {
            "decision": _json_model(item.envelope),
            "replayable": False,
            "detail": (
                "This sandbox stores the signed input and gate trace for audit. "
                "Re-execution is intentionally disabled because it would mutate "
                "replay and rate-limit state; use the recorded trace as evidence."
            ),
        }

    @api.get("/obligations/{obligation_id}")
    def get_obligation(obligation_id: str, state: StateDep) -> dict[str, Any]:
        current = state.sandbox.ledger.current(obligation_id)
        if current is None:
            raise HTTPException(status_code=404, detail="unknown obligation")
        return {
            "current": _json_model(current),
            "history": [_json_model(item) for item in state.sandbox.ledger.history(obligation_id)],
            "rail_id": state.sandbox.ledger.rail_id_for(obligation_id),
        }

    @api.post("/evidence")
    def submit_evidence(
        envelope: EvidenceEnvelope,
        state: StateDep,
        obligation_id: Annotated[str, Query(min_length=1)],
    ) -> dict[str, Any]:
        try:
            result = state.clearing.submit(obligation_id, envelope)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        state.clearing_results[obligation_id] = result
        return {
            "obligation": _json_model(result.obligation),
            "decision": _json_model(result.decision),
            "finality": result.finality.finality.value,
            "explanation": result.explain(),
            "settlement": _json_model(result.settlement),
        }

    @app.post("/webhooks/razorpay", tags=["Razorpay webhook"])
    async def receive_webhook(request: Request, state: StateDep) -> dict[str, Any]:
        body = await request.body()
        try:
            event = await run_in_threadpool(
                state.webhooks.receive, body, dict(request.headers)
            )
        except WebhookRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "accepted": event is not None,
            "duplicate": event is None,
            "event_id": event.event_id if event is not None else None,
        }

    @api.get("/ledger/verify")
    def verify_ledger(state: StateDep) -> dict[str, Any]:
        verification = state.sandbox.ledger.verify()
        return {
            "ok": verification.ok,
            "entries": verification.entries,
            "tip_hash": verification.tip_hash,
            "failures": [asdict(failure) for failure in verification.failures],
        }

    @api.get("/metrics")
    def metrics(state: StateDep) -> dict[str, Any]:
        return _dashboard_metrics(state)

    @api.get("/benchmark")
    def benchmark(state: StateDep) -> dict[str, Any]:
        return state.benchmark()

    @api.get("/simulation/scenarios")
    def get_simulation_scenarios() -> list[dict[str, Any]]:
        from kya.simulation_runner import list_scenarios
        return list_scenarios()

    @api.post("/simulation/run")
    def run_simulation(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from kya.simulation_runner import execute_simulation
        body = payload or {}
        scenario_id = body.get("scenario_id", "legit_purchase")
        custom_params = body.get("custom_params")
        return execute_simulation(scenario_id, custom_params=custom_params)

    # --- Disputes & Liability Arbiter routes ---

    @api.get("/disputes")
    def list_disputes(state: StateDep) -> list[dict[str, Any]]:
        packages = state.ordered_dispute_packages()
        return [
            {
                "package_id": pkg.package_id,
                "dispute_id": pkg.dispute_id,
                "obligation_id": pkg.obligation_id,
                "created_at": pkg.created_at,
                "executive_summary": pkg.executive_summary,
                "assigned_fault": pkg.liability_verdict.assigned_fault.value,
                "outcome": pkg.liability_verdict.outcome.value,
                "confidence": pkg.liability_verdict.confidence,
                "reason_codes": pkg.liability_verdict.reason_codes,
                "has_certificate": pkg.settlement_certificate is not None,
                "has_consent": pkg.consent_record is not None,
            }
            for pkg in packages
        ]

    @api.get("/disputes/{dispute_id}")
    def get_dispute(dispute_id: str, state: StateDep) -> dict[str, Any]:
        pkg = state.dispute_packages.get(dispute_id)
        if pkg is None:
            raise HTTPException(status_code=404, detail="unknown dispute")
        return _json_model(pkg) or {}

    @api.post("/disputes/evaluate")
    def evaluate_dispute(claim: DisputeClaim, state: StateDep) -> dict[str, Any]:
        try:
            pkg = state.evaluate_dispute(claim)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _json_model(pkg) or {}

    @api.get("/certificates/{obligation_id}")
    def get_certificate(obligation_id: str, state: StateDep) -> dict[str, Any]:
        cert = state.get_settlement_certificate(obligation_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="no settlement certificate for obligation")
        return _json_model(cert) or {}

    @api.get("/consent/{chain_hash}")
    def get_consent_record(chain_hash: str, state: StateDep) -> dict[str, Any]:
        record = state.consent_ledger.get_by_chain_hash(chain_hash)
        if record is None:
            record = state.consent_ledger.get_by_id(chain_hash)
        if record is None:
            raise HTTPException(status_code=404, detail="consent record not found")
        valid, reasons = state.consent_ledger.verify_consent(record)
        return {
            "record": _json_model(record),
            "is_valid": valid,
            "verification_reasons": reasons,
        }

    @api.get("/reputation/{agent_id}")
    def get_reputation(agent_id: str, state: StateDep) -> dict[str, Any]:
        score = state.get_agent_reputation(agent_id)
        return _json_model(score) or {}

    @api.post("/cross-rail/normalize")
    def normalize_cross_rail(payload: dict[str, Any], state: StateDep) -> dict[str, Any]:
        token_type = payload.get("token_type", "stripe_spt")
        token_id = payload.get("token_id", "tok_demo")
        agent_id = payload.get("agent_id", "agent_cross_rail")
        principal_ref = payload.get("principal_ref", "user_cross_rail")
        amount = int(payload.get("amount", 5000_00))
        currency = payload.get("currency", "INR")

        if token_type == "stripe_spt":
            token = state.cross_rail_adapter.parse_stripe_spt(
                token_id=token_id,
                agent_id=agent_id,
                principal_ref=principal_ref,
                amount=amount,
                currency=currency,
            )
        elif token_type == "mc_agentic_token":
            token = state.cross_rail_adapter.parse_mc_agentic_token(
                token_id=token_id,
                agent_id=agent_id,
                principal_ref=principal_ref,
                amount=amount,
                currency=currency,
            )
        elif token_type == "x402_usdc":
            token = state.cross_rail_adapter.parse_x402_header(
                auth_header=payload.get("auth_header", f"x402 {token_id}"),
                agent_id=agent_id,
                principal_ref=principal_ref,
                amount=amount,
                currency=currency,
            )
        else:
            raise HTTPException(status_code=400, detail=f"unsupported token type: {token_type}")

        is_valid = state.cross_rail_adapter.verify_token(token)
        return {
            "token": _json_model(token),
            "is_valid": is_valid,
            "rail_normalized": True,
        }

    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=307)

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(request: Request, state: StateDep) -> HTMLResponse:
        return _templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "metrics": _dashboard_metrics(state),
                "decisions": [_decision_summary(item) for item in state.ordered_decisions()],
                "clearing_results": [
                    _clearing_summary(result) for result in state.ordered_clearing_results()
                ],
            },
        )

    @app.get(
        "/dashboard/decisions/{decision_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def decision_view(decision_id: str, request: Request, state: StateDep) -> HTMLResponse:
        item = _stored_or_404(state, decision_id)
        return _templates.TemplateResponse(
            request=request,
            name="decision.html",
            context={"item": item, "summary": _decision_summary(item)},
        )

    @app.get(
        "/dashboard/metrics", response_class=HTMLResponse, include_in_schema=False
    )
    def metrics_view(request: Request, state: StateDep) -> HTMLResponse:
        benchmark = state.benchmark()
        return _templates.TemplateResponse(
            request=request,
            name="metrics.html",
            context={"benchmark": benchmark, "b3": benchmark["baselines"]["B3"]},
        )

    @app.get(
        "/dashboard/quarantine", response_class=HTMLResponse, include_in_schema=False
    )
    def quarantine_view(request: Request, state: StateDep) -> HTMLResponse:
        quarantined = [
            _decision_summary(item)
            for item in state.ordered_decisions()
            if item.envelope.decision is Decision.QUARANTINE
        ]
        return _templates.TemplateResponse(
            request=request,
            name="quarantine.html",
            context={"decisions": quarantined},
        )

    return app


app = create_app()
