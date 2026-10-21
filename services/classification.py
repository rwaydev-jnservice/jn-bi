"""Quote classification: department / category / system.

V1 is rules-based (keywords tuned on JN's real line items, e.g.
"HVAC Install - ...", "HVAC Repair - ...", "Plumbing - ...", "MECHANICAL SCOPE").
Each line item is classified, then the quote takes the label carrying the most
dollar value. Add-ons like the "GENN PRO PLAN" membership are ignored unless
they are the only thing on the quote.

Results are stored in quote_classifications with a hash of the source text
(title + line items + rules version); unchanged quotes are never reclassified.
An LLM classifier can plug in later behind the same interface (method="LLM").
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import JobberQuote, JobberQuoteLineItem, QuoteClassification, utcnow

RULES_VERSION = "rules-v3"

ADDON = re.compile(r"genn pro plan|membership|service plan|warranty|permit|trip charge|discount|alternate", re.I)

SYSTEMS = [  # first match wins; order matters (tankless before water heater, boiler before water)
    ("TANKLESS", r"tankless|rinnai|navien"),
    ("BOILER", r"boiler|steam|radiator|baseboard|burnham|hydronic|weil.?mclain"),
    ("FURNACE", r"furnace"),
    ("MINI_SPLIT", r"mini.?split|ductless"),  # names say "Mini Split Heat Pump": the more specific wins
    ("HEAT_PUMP", r"heat pump|hybrid heat|\bzones?\b.*\bbtu|\bbtu.*\bzones?\b"),
    ("CENTRAL_AC", r"central air|condenser|\ba/?c\b|air condition|rooftop|\brtu\b|\b\d+(\.\d+)?.?ton\b|evaporator coil"),
    ("WATER_HEATER", r"water heater|indirect tank|hot water tank"),
    ("WATER_SOFTENER", r"softener"),
    ("DRAIN", r"drain|sewer|clog|jetting|snak"),
]
WATER_TREATMENT = r"water purity|purif|filtration|softener|soft water|water test|osmosis|\buv\b|well water|carbon filter"
HVAC = r"\bhvac\b|mechanical|heat maintenance|duct|thermostat|ventilat|\berv\b|\bhrv\b|exhaust|bathroom fan|thermistor|igniter|inducer|refrigerant"
PLUMBING = r"plumbing|\bpipe|repipe|faucet|toilet|shower|valve|leak|sump|gas line|fixture"
SYSTEM_DEPT = {"TANKLESS": "PLUMBING", "WATER_HEATER": "PLUMBING", "DRAIN": "PLUMBING", "WATER_SOFTENER": "WATER_TREATMENT",
               "BOILER": "HVAC", "FURNACE": "HVAC", "HEAT_PUMP": "HVAC", "MINI_SPLIT": "HVAC", "CENTRAL_AC": "HVAC"}

PART = r"motor|board|switch|valve|thermistor|igniter|ignitor|capacitor|sensor|relay|transformer|blower|gasket|(?<!heat )pump\b|flame|pressure"
CATEGORIES = [  # first match wins
    ("MAINTENANCE", r"maintenance|tune.?up|cleaning|\bclean\b|preventative|inspection and clean"),
    ("DIAGNOSTIC", r"diagnos|troubleshoot|camera inspection|water test|\binspection\b"),
    ("REPAIR", rf"\brepair|\bfix|leak|{PART}"),
    ("REPLACEMENT", r"replace|replacement|change.?out|swap"),
    ("INSTALLATION", r"install|new construction|\bscope\b|new unit|addition"),
]


@dataclass
class Classification:
    department: str
    category: str
    system: str
    confidence: float
    rationale: str


def classify_line(name: str | None, description: str | None) -> tuple[str, str, str]:
    """The line item NAME decides ("HVAC Install - Gas Furnace"); the description
    (often boilerplate mentioning maintenance, valves, cleaning...) only fills gaps."""
    n = classify_text(name or "", allow_default_category=False)
    if not description:
        return n[0], n[1] or classify_text(name or "")[1], n[2]
    d = classify_text(description or "", allow_default_category=False)
    is_scope = bool(re.search(r"\bscope\b", name or "", re.I))  # commercial bid: many systems
    dept = n[0] if n[0] != "OTHER" else d[0]
    system = n[2] if (n[2] != "OTHER" or is_scope) else d[2]
    if n[1]:
        category = n[1]
    elif n[2] not in ("OTHER", "DRAIN"):
        category = "REPLACEMENT"  # name names a whole unit with no verb: "Plumbing - State Gas Water Heater 50 Gallons"
    else:
        category = d[1]
    if category is None:
        category = "REPLACEMENT" if system not in ("OTHER", "DRAIN") else "OTHER"
    if dept == "WATER_TREATMENT":  # descriptions mention heaters/tanks; the product is treatment equipment
        system = "WATER_SOFTENER" if re.search(r"\bsoft", f"{name} {description}", re.I) else "OTHER"
    return dept, category, system


def classify_text(text: str, allow_default_category: bool = True) -> tuple[str, str | None, str]:
    t = (text or "").lower()
    system = next((name for name, rx in SYSTEMS if re.search(rx, t)), "OTHER")
    if re.search(WATER_TREATMENT, t):
        dept = "WATER_TREATMENT"
    elif re.match(r"\s*plumbing", t):
        dept = "PLUMBING"
    elif re.match(r"\s*(hvac|mechanical|heat)", t):
        dept = "HVAC"
    elif system in SYSTEM_DEPT:
        dept = SYSTEM_DEPT[system]
    elif re.search(HVAC, t):
        dept = "HVAC"
    elif re.search(PLUMBING, t):
        dept = "PLUMBING"
    else:
        dept = "OTHER"
    category = next((name for name, rx in CATEGORIES if re.search(rx, t)), None)
    if category is None and allow_default_category:
        # a whole unit with no verb ("Plumbing - Rheem Water Heater") is normally a replacement
        category = "REPLACEMENT" if system not in ("OTHER", "DRAIN") else "OTHER"
    return dept, category, system


def classify_quote(title: str | None, line_items: list[dict]) -> Classification:
    items = [li for li in line_items if (li.get("name") or li.get("description"))]
    core = [li for li in items if not ADDON.search(li.get("name") or "")] or items
    votes: dict[str, dict[str, float]] = {"department": {}, "category": {}, "system": {}}
    total_weight = 0.0
    for li in core:
        w = max(float(li.get("total_price") or 0), 1.0)
        total_weight += w
        for dim, label in zip(("department", "category", "system"), classify_line(li.get("name"), li.get("description"))):
            votes[dim][label] = votes[dim].get(label, 0) + w
    if not core:
        d, c, s = classify_text(title or "")
        conf = 0.4 if (d, c, s) != ("OTHER", "OTHER", "OTHER") else 0.1
        return Classification(d, c, s, conf, "No line items; classified from title")

    def pick(dim):
        options = votes[dim]
        # Prefer a real label over OTHER when OTHER only wins by add-on-like noise
        best = max(options.items(), key=lambda kv: (kv[1] * (0.5 if kv[0] == "OTHER" else 1.0)))
        return best[0], best[1] / total_weight

    (d, sd), (c, sc), (s, ss) = pick("department"), pick("category"), pick("system")
    conf = round(0.9 * (sd + sc + ss) / 3, 2)
    for label in (d, c, s):
        if label == "OTHER":
            conf = round(conf * 0.8, 2)
    return Classification(d, c, s, conf, f"{len(core)} line item(s) weighted by price")


def source_hash(title: str | None, line_items: list[dict]) -> str:
    parts = [RULES_VERSION, title or ""] + [f"{li.get('name') or ''}|{li.get('description') or ''}|{li.get('total_price')}" for li in line_items]
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def rebuild(session: Session) -> dict:
    items: dict[str, list[dict]] = {}
    for li in session.scalars(select(JobberQuoteLineItem).order_by(JobberQuoteLineItem.sort_order)):
        items.setdefault(li.quote_id, []).append({"name": li.name, "description": li.description, "total_price": li.total_price})
    existing = {c.quote_id: c for c in session.scalars(select(QuoteClassification))}
    counts = {"classified": 0, "unchanged": 0}
    for q in session.scalars(select(JobberQuote)):
        lis = items.get(q.quote_id, [])
        h = source_hash(q.title, lis)
        row = existing.get(q.quote_id)
        if row and (row.source_hash == h or row.method == "MANUAL"):
            counts["unchanged"] += 1
            continue
        c = classify_quote(q.title, lis)
        if row is None:
            row = QuoteClassification(quote_id=q.quote_id)
            session.add(row)
        row.department, row.category, row.system, row.confidence = c.department, c.category, c.system, c.confidence
        row.method, row.model, row.source_hash, row.rationale, row.classified_at = "RULES", RULES_VERSION, h, c.rationale, utcnow()
        counts["classified"] += 1
    session.flush()
    return counts
