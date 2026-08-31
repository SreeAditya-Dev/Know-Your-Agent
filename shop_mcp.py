# shop_mcp.py
"""Shop and KYA Gateway MCP Server for Claude Code and Antigravity.

Provides real-time product discovery, cart creation, mandate verification,
and simulation scenario execution over Model Context Protocol (MCP).
"""

import json
from mcp.server.fastmcp import FastMCP

from kya.simulation_runner import SCENARIOS_CATALOG, execute_simulation
from kya.simulation import standard_sandbox, make_cart, make_mandates, build_signed_request

mcp = FastMCP("ShopPayAgent")

# Real-time shoe catalog
CATALOG = [
    {
        "sku": "PUMA-NITRO-3",
        "name": "Puma Velocity Nitro 3 Running Shoes",
        "brand": "Puma",
        "category": "Running",
        "price_paise": 749900,  # Rs 7,499.00
        "price_inr": 7499.00,
        "in_stock": True,
    },
    {
        "sku": "PUMA-FLYER-RUNNER",
        "name": "Puma Flyer Runner Mesh Shoes",
        "brand": "Puma",
        "category": "Casual/Running",
        "price_paise": 319900,  # Rs 3,199.00
        "price_inr": 3199.00,
        "in_stock": True,
    },
    {
        "sku": "PUMA-DEVIATE-NITRO-2",
        "name": "Puma Deviate Nitro 2 Carbon Plated Shoes",
        "brand": "Puma",
        "category": "Marathon",
        "price_paise": 1299900,  # Rs 12,999.00
        "price_inr": 12999.00,
        "in_stock": True,
    },
    {
        "sku": "PUMA-RED-BULL-RACING",
        "name": "Puma Red Bull Racing Drift Cat Decima",
        "brand": "Puma",
        "category": "Motorsport",
        "price_paise": 599900,  # Rs 5,999.00
        "price_inr": 5999.00,
        "in_stock": True,
    },
]


@mcp.tool()
def search_catalog(query: str = "", max_price_inr: float = 20000.0):
    """Search the store catalog for items matching a keyword and budget ceiling."""
    max_price_paise = int(max_price_inr * 100)
    q = (query or "").lower().strip()
    matches = []
    for item in CATALOG:
        name_match = (
            not q
            or q in item["name"].lower()
            or q in item["brand"].lower()
            or q in item["category"].lower()
            or q in item["sku"].lower()
        )
        if name_match and item["price_paise"] <= max_price_paise:
            matches.append(item)
    return matches


@mcp.tool()
def create_cart(sku: str, quantity: int = 1):
    """Create a locked checkout cart with calculated totals and line items."""
    item = next((p for p in CATALOG if p["sku"].upper() == sku.upper()), None)
    if not item:
        return {"error": f"Product with SKU '{sku}' not found in catalog."}

    total_paise = item["price_paise"] * quantity
    cart_id = f"cart_{item['sku'].lower()}_{int(total_paise)}"

    return {
        "cart_id": cart_id,
        "sku": item["sku"],
        "name": item["name"],
        "quantity": quantity,
        "unit_price_inr": item["price_inr"],
        "total_paise": total_paise,
        "total_inr": total_paise / 100.0,
        "currency": "INR",
        "locked": True,
    }


@mcp.tool()
def execute_order(sku: str, max_budget_inr: float = 10000.0, quantity: int = 1):
    """Execute a purchase through the KYA security gateway and Razorpay rails."""
    item = next((p for p in CATALOG if p["sku"].upper() == sku.upper()), None)
    if not item:
        return {"decision": "DENY", "error": f"SKU '{sku}' not found."}

    total_paise = item["price_paise"] * quantity
    budget_paise = int(max_budget_inr * 100)

    if total_paise > budget_paise:
        return {
            "decision": "DENY",
            "reason_codes": ["E003"],
            "explanation": f"Cart total Rs {total_paise / 100:.2f} exceeds user budget of Rs {max_budget_inr:.2f}.",
            "gate": "G3_MANDATE_CEILING",
            "allowed": False,
        }

    sandbox, agent, principal = standard_sandbox()
    cart = make_cart(items=[(item["sku"], item["name"], quantity, item["price_paise"])])
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
            "item": item["name"],
            "sku": item["sku"],
            "currency": "INR",
            "receipt": "rcpt_" + item["sku"].lower(),
            "kya_verified": res.allowed,
            "razorpay_order_id": res.order.get("id") if res.order else None,
        },
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
