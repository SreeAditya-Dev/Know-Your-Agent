"""Merchant store catalog for Apex Kicks Agentic Commerce Store."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Product:
    sku: str
    name: str
    brand: str
    category: str
    price_paise: int
    price_inr: float
    original_price_inr: float
    image_url: str
    rating: float
    reviews_count: int
    in_stock: bool
    stock_count: int
    sizes: list[int]
    colors: list[str]
    specs: list[str]
    description: str
    merchant_id: str = "merchant_apex_kicks"
    mcc: str = "5651"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STORE_PRODUCTS: list[Product] = [
    Product(
        sku="PUMA-NITRO-3",
        name="Puma Velocity Nitro 3 Running Shoes",
        brand="Puma",
        category="Daily Running",
        price_paise=749900,
        price_inr=7499.00,
        original_price_inr=9999.00,
        image_url="https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=800&q=80",
        rating=4.8,
        reviews_count=1240,
        in_stock=True,
        stock_count=14,
        sizes=[7, 8, 9, 10, 11],
        colors=["Fireglow Orange / White", "Puma Black / White"],
        specs=["NITRO foam cushioning", "PUMAGRIP rubber traction", "260g weight", "10mm drop"],
        description="Engineered for daily mileage, combining responsive nitrogen-infused NITRO foam with high-traction PUMAGRIP rubber for smooth transitions.",
    ),
    Product(
        sku="PUMA-DEVIATE-NITRO-2",
        name="Puma Deviate Nitro 2 Carbon Plated Shoes",
        brand="Puma",
        category="Marathon / Racing",
        price_paise=1299900,
        price_inr=12999.00,
        original_price_inr=15999.00,
        image_url="https://images.unsplash.com/photo-1608231387042-66d1773070a5?auto=format&fit=crop&w=800&q=80",
        rating=4.9,
        reviews_count=840,
        in_stock=True,
        stock_count=8,
        sizes=[8, 9, 10, 11],
        colors=["Royal Sapphire / Nitro Green", "Triple Black"],
        specs=["Full-length INNOPLATE carbon composite", "NITRO Elite superfoam", "214g ultralight", "6mm drop"],
        description="Max-cushion, carbon-plated marathon racing shoe engineered for explosive propulsion and high-speed energy return.",
    ),
    Product(
        sku="PUMA-RED-BULL-RACING",
        name="Puma Red Bull Racing Drift Cat Decima",
        brand="Puma",
        category="Motorsport",
        price_paise=599900,
        price_inr=5999.00,
        original_price_inr=7499.00,
        image_url="https://images.unsplash.com/photo-1551107696-a4b0c5a0d9a2?auto=format&fit=crop&w=800&q=80",
        rating=4.7,
        reviews_count=520,
        in_stock=True,
        stock_count=22,
        sizes=[7, 8, 9, 10],
        colors=["Night Sky Blue / Red Bull", "Stealth Black"],
        specs=["Low profile motorsport silhouette", "Oracle Red Bull Racing crest", "Perforated synthetic upper"],
        description="Official Oracle Red Bull Racing paddock footwear with iconic aerodynamic lines and low-profile pedal grip.",
    ),
    Product(
        sku="PUMA-FLYER-RUNNER",
        name="Puma Flyer Runner Mesh Shoes",
        brand="Puma",
        category="Casual / Training",
        price_paise=319900,
        price_inr=3199.00,
        original_price_inr=3999.00,
        image_url="https://images.unsplash.com/photo-1525966222134-fcfa99b8ae77?auto=format&fit=crop&w=800&q=80",
        rating=4.6,
        reviews_count=2100,
        in_stock=True,
        stock_count=45,
        sizes=[6, 7, 8, 9, 10, 11, 12],
        colors=["Castlerock Grey", "Navy / Sunburst"],
        specs=["SoftFoam+ comfort sockliner", "Breathable mesh upper", "EVA cushioned midsole"],
        description="Lightweight everyday sneaker with SoftFoam+ comfort insert for all-day cushioning and flexibility.",
    ),
    Product(
        sku="PUMA-MAGMAX-NITRO",
        name="Puma MagMax Nitro Super-Max Running Shoes",
        brand="Puma",
        category="Super-Max Cushion",
        price_paise=1499900,
        price_inr=14999.00,
        original_price_inr=17999.00,
        image_url="https://images.unsplash.com/photo-1595950653106-6c9ebd614d3a?auto=format&fit=crop&w=800&q=80",
        rating=4.9,
        reviews_count=310,
        in_stock=True,
        stock_count=5,
        sizes=[8, 9, 10, 11],
        colors=["Lapis Lazuli / Hyper Violet", "Matte Black"],
        specs=["46mm maximal stack height", "Dual-density NITRO foam", "Engineered knit upper"],
        description="Extreme cushion trainer delivering cloud-like soft landings and effortless rolling momentum for ultra recovery runs.",
    ),
    Product(
        sku="PUMA-ALL-PRO-NITRO",
        name="Puma All-Pro Nitro Basketball Shoes",
        brand="Puma",
        category="Basketball",
        price_paise=999900,
        price_inr=9999.00,
        original_price_inr=11999.00,
        image_url="https://images.unsplash.com/photo-1579338559194-a162d19bf842?auto=format&fit=crop&w=800&q=80",
        rating=4.8,
        reviews_count=640,
        in_stock=True,
        stock_count=11,
        sizes=[8, 9, 10, 11, 12],
        colors=["Scoot Henderson PE / Sunset Orange", "Core White / Black"],
        specs=["Dual-density NITRO foam core", "Multi-zone cord lockdown", "High-abrasion rubber tread"],
        description="High-performance on-court shoe with targeted lateral containment and explosive responsive lift for quick cuts.",
    ),
]


def get_catalog() -> list[dict[str, Any]]:
    return [p.to_dict() for p in STORE_PRODUCTS]


def find_product_by_sku(sku: str) -> Product | None:
    sku_clean = (sku or "").strip().upper()
    for p in STORE_PRODUCTS:
        if p.sku.upper() == sku_clean:
            return p
    return None


def search_products(query: str = "", max_price_inr: float = 50000.0) -> list[Product]:
    max_price_paise = int(max_price_inr * 100)
    q = (query or "").lower().strip()
    results: list[Product] = []
    for item in STORE_PRODUCTS:
        match = (
            not q
            or q in item.name.lower()
            or q in item.brand.lower()
            or q in item.category.lower()
            or q in item.sku.lower()
            or any(q in spec.lower() for spec in item.specs)
        )
        if match and item.price_paise <= max_price_paise:
            results.append(item)
    return results
