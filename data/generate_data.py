"""
Generates a synthetic vehicle inventory that mirrors the fields Copart
surfaces on a real lot listing (see SCHEMA.md), and loads it into a SQLite
database at data/vehicles.db.

This is intentionally NOT scraped from copart.com: scraping risks ToS issues
and flaky/rate-limited data during a time-boxed take-home. Instead we encode
the *real schema and realistic value distributions* (common salvage-auction
makes, damage types, title types, etc.) and generate data that looks and
behaves like the real thing, at a scale (~800 rows) that's enough to
demonstrate real filtering/refinement behavior.

Run: python data/generate_data.py
"""

import random
import sqlite3
import string
from datetime import date, timedelta
from pathlib import Path

random.seed(42)  # reproducible dataset across runs

DB_PATH = Path(__file__).parent / "vehicles.db"

# ---------------------------------------------------------------------------
# Reference lists — these double as the "known good values" the guardrail /
# validation layer checks LLM-extracted filters against, so import from here
# rather than re-declaring them elsewhere.
# ---------------------------------------------------------------------------

MAKES_MODELS = {
    "Toyota": ["Camry", "Corolla", "RAV4", "Tacoma", "Highlander", "Tundra"],
    "Honda": ["Civic", "Accord", "CR-V", "Pilot", "Odyssey"],
    "Ford": ["F-150", "Explorer", "Escape", "Mustang", "Focus", "Fusion"],
    "Chevrolet": ["Silverado", "Malibu", "Equinox", "Tahoe", "Camaro"],
    "Nissan": ["Altima", "Sentra", "Rogue", "Frontier", "Pathfinder"],
    "Jeep": ["Grand Cherokee", "Wrangler", "Cherokee", "Compass"],
    "Hyundai": ["Elantra", "Sonata", "Tucson", "Santa Fe"],
    "Kia": ["Optima", "Soul", "Sorento", "Sportage"],
    "BMW": ["3 Series", "5 Series", "X3", "X5"],
    "Mercedes-Benz": ["C-Class", "E-Class", "GLC", "GLE"],
    "Dodge": ["Charger", "Challenger", "Durango", "Grand Caravan"],
    "GMC": ["Sierra", "Yukon", "Acadia", "Terrain"],
    "Subaru": ["Outback", "Forester", "Impreza", "Legacy"],
    "Volkswagen": ["Jetta", "Passat", "Tiguan", "Atlas"],
    "Tesla": ["Model 3", "Model Y", "Model S"],
}

BODY_STYLES = {
    "Camry": "Sedan", "Corolla": "Sedan", "RAV4": "SUV", "Tacoma": "Pickup",
    "Highlander": "SUV", "Tundra": "Pickup", "Civic": "Sedan", "Accord": "Sedan",
    "CR-V": "SUV", "Pilot": "SUV", "Odyssey": "Van", "F-150": "Pickup",
    "Explorer": "SUV", "Escape": "SUV", "Mustang": "Coupe", "Focus": "Sedan",
    "Fusion": "Sedan", "Silverado": "Pickup", "Malibu": "Sedan", "Equinox": "SUV",
    "Tahoe": "SUV", "Camaro": "Coupe", "Altima": "Sedan", "Sentra": "Sedan",
    "Rogue": "SUV", "Frontier": "Pickup", "Pathfinder": "SUV",
    "Grand Cherokee": "SUV", "Wrangler": "SUV", "Cherokee": "SUV",
    "Compass": "SUV", "Elantra": "Sedan", "Sonata": "Sedan", "Tucson": "SUV",
    "Santa Fe": "SUV", "Optima": "Sedan", "Soul": "SUV", "Sorento": "SUV",
    "Sportage": "SUV", "3 Series": "Sedan", "5 Series": "Sedan", "X3": "SUV",
    "X5": "SUV", "C-Class": "Sedan", "E-Class": "Sedan", "GLC": "SUV",
    "GLE": "SUV", "Charger": "Sedan", "Challenger": "Coupe", "Durango": "SUV",
    "Grand Caravan": "Van", "Sierra": "Pickup", "Yukon": "SUV", "Acadia": "SUV",
    "Terrain": "SUV", "Outback": "SUV", "Forester": "SUV", "Impreza": "Sedan",
    "Legacy": "Sedan", "Jetta": "Sedan", "Passat": "Sedan", "Tiguan": "SUV",
    "Atlas": "SUV", "Model 3": "Sedan", "Model Y": "SUV", "Model S": "Sedan",
}

COLORS = ["White", "Black", "Silver", "Gray", "Blue", "Red", "Green", "Brown", "Beige"]

# weighted: front/rear end collisions are the most common salvage cause
PRIMARY_DAMAGE = (
    ["FRONT END"] * 6 + ["REAR END"] * 5 + ["SIDE"] * 4 + ["ALL OVER"] * 2
    + ["WATER/FLOOD"] * 3 + ["HAIL"] * 3 + ["VANDALISM"] * 2 + ["MECHANICAL"] * 3
    + ["BURN"] * 1 + ["UNDERCARRIAGE"] * 2 + ["NORMAL WEAR"] * 2
)
SECONDARY_DAMAGE_POOL = [None] * 6 + ["SIDE", "REAR END", "FRONT END", "UNDERCARRIAGE", "ALL OVER"]

TITLE_TYPES = ["SALVAGE"] * 5 + ["REBUILT"] * 2 + ["CLEAN"] * 2 + ["PARTS ONLY"] * 1 + ["JUNK"] * 1
LOSS_TYPES = ["COLLISION"] * 6 + ["COMPREHENSIVE"] * 3 + ["THEFT"] * 2 + ["VANDALISM"] * 1
RUN_AND_DRIVE = ["RUNS_DRIVES"] * 4 + ["START_ONLY"] * 3 + ["UNKNOWN"] * 3
DRIVETRAIN = ["FWD"] * 5 + ["RWD"] * 2 + ["AWD"] * 2 + ["4WD"] * 3
FUEL_TYPE = ["GAS"] * 8 + ["HYBRID"] * 2 + ["DIESEL"] * 1 + ["ELECTRIC"] * 1
TRANSMISSION = ["AUTOMATIC"] * 8 + ["MANUAL"] * 2
TITLE_STATES = ["TX", "CA", "FL", "GA", "OH", "PA", "IL", "NC", "AZ", "CO"]

YARDS = [
    ("Dallas", "Dallas", "TX"), ("Houston", "Houston", "TX"),
    ("Los Angeles", "Los Angeles", "CA"), ("Sacramento", "Sacramento", "CA"),
    ("Atlanta North", "Atlanta", "GA"), ("Orlando South", "Orlando", "FL"),
    ("Tampa South", "Tampa", "FL"), ("Columbus", "Columbus", "OH"),
    ("Phoenix", "Phoenix", "AZ"), ("Denver Central", "Denver", "CO"),
    ("Chicago North", "Chicago", "IL"), ("Charlotte", "Charlotte", "NC"),
    ("Philadelphia", "Philadelphia", "PA"),
]

HIGHLIGHT_POOL = ["Enhanced Vehicle", "Buy It Now", "Clean Title", "Low Mileage"]


def _vin() -> str:
    chars = string.ascii_uppercase.replace("I", "").replace("O", "").replace("Q", "") + string.digits
    return "".join(random.choice(chars) for _ in range(17))


def _price_for(year: int, odometer: int, primary_damage: str, run_and_drive: str) -> float:
    base = 4000 + (year - 1995) * 350
    base -= odometer / 50
    if primary_damage in ("WATER/FLOOD", "BURN"):
        base *= 0.45
    elif primary_damage in ("ALL OVER", "UNDERCARRIAGE"):
        base *= 0.6
    if run_and_drive == "RUNS_DRIVES":
        base *= 1.25
    elif run_and_drive == "UNKNOWN":
        base *= 0.85
    base *= random.uniform(0.8, 1.2)
    return max(round(base, -1), 300.0)


def generate_rows(n: int = 800):
    rows = []
    start = date(2026, 8, 1)
    for i in range(n):
        make = random.choice(list(MAKES_MODELS.keys()))
        model = random.choice(MAKES_MODELS[make])
        year = random.randint(2005, 2025)
        odometer = random.randint(8_000, 180_000)
        primary_damage = random.choice(PRIMARY_DAMAGE)
        secondary_damage = random.choice(SECONDARY_DAMAGE_POOL)
        run_and_drive = random.choice(RUN_AND_DRIVE)
        has_keys = random.random() > 0.25
        airbags_deployed = primary_damage in ("FRONT END", "SIDE", "ALL OVER") and random.random() > 0.6
        current_bid = _price_for(year, odometer, primary_damage, run_and_drive)
        buy_it_now = round(current_bid * random.uniform(1.3, 1.8), -1) if random.random() > 0.6 else None
        yard_name, yard_city, yard_state = random.choice(YARDS)
        sale_date = start + timedelta(days=random.randint(0, 45))
        highlights = ",".join(random.sample(HIGHLIGHT_POOL, k=random.randint(0, 2))) or None

        rows.append({
            "lot_id": str(40_000_000 + i),
            "vin": _vin(),
            "year": year,
            "make": make,
            "model": model,
            "trim": None,
            "body_style": BODY_STYLES.get(model, "Sedan"),
            "vehicle_type": "Automobile",
            "color": random.choice(COLORS),
            "odometer": odometer,
            "odometer_status": random.choice(["ACTUAL"] * 8 + ["NOT_ACTUAL"] * 1 + ["EXEMPT"] * 1),
            "primary_damage": primary_damage,
            "secondary_damage": secondary_damage,
            "title_type": random.choice(TITLE_TYPES),
            "title_state": random.choice(TITLE_STATES),
            "loss_type": random.choice(LOSS_TYPES),
            "has_keys": has_keys,
            "airbags_deployed": airbags_deployed,
            "run_and_drive": run_and_drive,
            "drivetrain": random.choice(DRIVETRAIN),
            "fuel_type": random.choice(FUEL_TYPE),
            "transmission": random.choice(TRANSMISSION),
            "cylinders": random.choice([4, 4, 4, 6, 6, 8]),
            "current_bid": current_bid,
            "buy_it_now_price": buy_it_now,
            "sale_date": sale_date.isoformat(),
            "yard_name": yard_name,
            "yard_city": yard_city,
            "yard_state": yard_state,
            "highlights": highlights,
        })
    return rows


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vehicles (
    lot_id TEXT PRIMARY KEY,
    vin TEXT,
    year INTEGER,
    make TEXT,
    model TEXT,
    trim TEXT,
    body_style TEXT,
    vehicle_type TEXT,
    color TEXT,
    odometer INTEGER,
    odometer_status TEXT,
    primary_damage TEXT,
    secondary_damage TEXT,
    title_type TEXT,
    title_state TEXT,
    loss_type TEXT,
    has_keys INTEGER,
    airbags_deployed INTEGER,
    run_and_drive TEXT,
    drivetrain TEXT,
    fuel_type TEXT,
    transmission TEXT,
    cylinders INTEGER,
    current_bid REAL,
    buy_it_now_price REAL,
    sale_date TEXT,
    yard_name TEXT,
    yard_city TEXT,
    yard_state TEXT,
    highlights TEXT
);
"""


def main():
    rows = generate_rows()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS vehicles")
    conn.execute(SCHEMA_SQL)
    conn.executemany(
        f"""INSERT INTO vehicles ({",".join(rows[0].keys())})
            VALUES ({",".join("?" for _ in rows[0])})""",
        [tuple(r.values()) for r in rows],
    )
    conn.commit()
    conn.close()
    print(f"Wrote {len(rows)} rows to {DB_PATH}")


if __name__ == "__main__":
    main()
