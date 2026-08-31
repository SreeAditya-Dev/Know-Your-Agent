# shop_mcp.py
"""Shop and KYA Gateway MCP Server for Claude Code, ChatGPT Desktop, and Antigravity.

Provides real-time product discovery, cart creation, mandate verification,
and simulation scenario execution over Model Context Protocol (MCP).
"""

import json
import httpx
from mcp.server.fastmcp import FastMCP

from kya.store_catalog import STORE_PRODUCTS, get_catalog, find_product_by_sku, search_products
from kya.simulation_runner import SCENARIOS_CATALOG, execute_simulation
from kya.simulation import standard_sandbox, make_cart, make_mandates, build_signed_request

mcp = FastMCP("ShopPayAgent")

API_BASE_URL = "http://127.0.0.1:8331/v1"


@mcp.tool()
def search_catalog(query: str = "", max_price_inr: float = 50000.0):
    """Search the Apex Kicks store catalog for shoes matching a keyword, size, and budget ceiling."""
    products = search_products(query=query, max_price_inr=max_price_inr)
    return [p.to_dict() for p in products]


@mcp.tool()
def create_cart(sku: str, quantity: int = 1, size: int = 9):
    """Create a locked checkout cart with calculated totals, selected size, and line items."""
    item = find_product_by_sku(sku)
    if not item:
        return {"error": f"Product with SKU '{sku}' not found in catalog."}

    total_paise = item.price_paise * quantity
    cart_id = f"cart_{item.sku.lower()}_{int(total_paise)}"

    return {
        "cart_id": cart_id,
        "sku": item.sku,
        "name": item.name,
        "size": size,
        "quantity": quantity,
        "unit_price_inr": item.price_inr,
        "total_paise": total_paise,
        "total_inr": total_paise / 100.0,
        "currency": "INR",
        "locked": True,
    }


@mcp.tool()
def execute_order(sku: str, max_budget_inr: float = 15000.0, quantity: int = 1, size: int = 9):
    """Execute a purchase through the KYA security gateway and Razorpay rails for Claude Code / ChatGPT."""
    item = find_product_by_sku(sku)
    if not item:
        return {"decision": "DENY", "error": f"SKU '{sku}' not found."}

    total_paise = item.price_paise * quantity
    budget_paise = int(max_budget_inr * 100)

    # 1. Try forwarding to live running KYA server so it updates web UI in real-time
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{API_BASE_URL}/store/agent-checkout",
                json={
                    "prompt": f"Claude/ChatGPT MCP Buyer: Order {quantity}x {item.name} (SKU: {item.sku}, Size: {size}) under max budget ₹{max_budget_inr:,.2f}",
                    "custom_params": {
                        "sku": item.sku,
                        "size": size,
                        "quantity": quantity,
                        "max_budget_inr": max_budget_inr,
                    },
                    "buyer_source": "CLAUDE_CODE_MCP",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "decision": data.get("decision"),
                    "allowed": data.get("success"),
                    "reason_codes": data.get("reason_codes"),
                    "explanation": data.get("explanation"),
                    "obligation_id": data.get("obligation_id"),
                    "amount_inr": total_paise / 100.0,
                    "order": data.get("order"),
                    "razorpay_order_id": data.get("razorpay_order_id"),
                    "live_web_synced": True,
                }
    except Exception:
        # Fallback to direct in-process execution if server is offline
        pass

    if total_paise > budget_paise:
        return {
            "decision": "DENY",
            "reason_codes": ["E003"],
            "explanation": f"Cart total ₹{total_paise / 100:.2f} exceeds user budget of ₹{max_budget_inr:.2f}.",
            "gate": "G4_MANDATE_CEILING",
            "allowed": False,
        }

    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[(item.sku, f"{item.name} (Size {size})", quantity, item.price_paise)])
    mandates = make_mandates(agent, principal, cart, max_amount=budget_paise)
    request = build_signed_request(
        agent=agent,
        mandates=mandates,
        cart=cart,
        method="POST",
        path="/v1/agent/orders",
    )

    res = sandbox.gateway().create_order(request)

    return {
        "decision": res.envelope.decision.value,
        "allowed": res.allowed,
        "reason_codes": res.envelope.reason_codes,
        "explanation": res.envelope.explanation,
        "obligation_id": res.envelope.obligation_id,
        "amount_inr": total_paise / 100.0,
        "order": {
            "item": item.name,
            "sku": item.sku,
            "size": size,
            "currency": "INR",
            "receipt": "rcpt_" + item.sku.lower(),
            "kya_verified": res.allowed,
            "razorpay_order_id": res.order.get("id") if res.order else None,
        },
        "live_web_synced": False,
    }


@mcp.tool()
def debit_reserve_pay_block(block_id: str = "blk_sim_reserve_001", amount_inr: float = 10000.0):
    """Debit funds from an NPCI UPI Reserve Pay (Single Block Multi Debit) block through the KYA gateway."""
    result = execute_simulation("a10_reserve_drain", custom_params={"amount_inr": amount_inr})
    return {
        "block_id": block_id,
        "amount_inr": amount_inr,
        "decision": result["decision"],
        "reason_codes": result["reason_codes"],
        "explanation": result["explanation"],
        "total_latency_ms": result["total_latency_ms"],
        "gate_steps": [
            {
                "step_id": step["step_id"],
                "name": step["name"],
                "verdict": step["verdict"],
                "reason_codes": step["reason_codes"],
                "explanation": step.get("explanation", ""),
            }
            for step in result.get("steps", [])
        ],
    }


@mcp.tool()
def list_simulation_scenarios():
    """List all available threat and legitimate simulation scenarios in the test corpus."""
    return [
        {
            "scenario_id": s.scenario_id,
            "title": s.title,
            "category": s.category,
            "threat_class": s.threat_class,
            "target_gate": s.target_gate,
            "expected_decision": s.expected_decision,
            "summary": s.summary,
        }
        for s in SCENARIOS_CATALOG
    ]


@mcp.tool()
def run_simulation_test(scenario_id: str):
    """Run a specific simulation scenario and return gate-by-gate verification trace."""
    result = execute_simulation(scenario_id)
    return {
        "scenario_id": result["scenario_id"],
        "title": result["scenario_title"],
        "threat_class": result["threat_class"],
        "decision": result["decision"],
        "reason_codes": result["reason_codes"],
        "explanation": result["explanation"],
        "total_latency_ms": result["total_latency_ms"],
        "obligation": result.get("obligation"),
        "gate_steps": [
            {
                "step_id": step["step_id"],
                "name": step["name"],
                "verdict": step["verdict"],
                "reason_codes": step["reason_codes"],
                "elapsed_ms": step["elapsed_ms"],
                "explanation": step.get("explanation", ""),
                "assertions": step.get("assertions", []),
            }
            for step in result.get("steps", [])
        ],
    }


if __name__ == "__main__":
    mcp.run()
