"""KYA's FastAPI routes and server-rendered operator dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kya.api.state import KYAAppState, StoredDecision
from kya.enums import Decision
from kya.rails.webhooks import WebhookRejected
from kya.schemas import AgentRequest, EvidenceEnvelope


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
