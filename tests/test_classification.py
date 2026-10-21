"""Rules classifier on real JN line-item naming patterns."""

from services.classification import classify_line, classify_quote, source_hash


def li(name, price=1000, description=""):
    return {"name": name, "description": description, "total_price": price}


def test_line_names():
    assert classify_line("Plumbing - State Gas Water Heater 50 Gallons", "includes annual maintenance flush") == ("PLUMBING", "REPLACEMENT", "WATER_HEATER")
    assert classify_line("HVAC Install - 3 Zones 24-36k BTU/h TCL Mini Split Heat Pump Installation", "") == ("HVAC", "INSTALLATION", "MINI_SPLIT")
    assert classify_line("HVAC Install - Furnace Main Board", "") == ("HVAC", "REPAIR", "FURNACE")
    assert classify_line("HVAC Repair - Taco Zone Valve Replacement", "")[1] == "REPAIR"
    assert classify_line("Maintenance - Deep Heat Maintenance Boiler", "")[1:] == ("MAINTENANCE", "BOILER")
    assert classify_line("Plumbing - Rinnai Tankless Combi Boiler", "")[2] == "TANKLESS"
    assert classify_line("HVAC Install - Gas Furnace and Coil", "replace pump, clean, maintenance")[1:] == ("INSTALLATION", "FURNACE")


def test_commercial_scope_keeps_system_other():
    assert classify_line("MECHANICAL SCOPE", "New rooftop units, boilers and water heaters") == ("HVAC", "INSTALLATION", "OTHER")
    assert classify_line("PLUMBING SCOPE", "water heater, drains")[0] == "PLUMBING"


def test_water_treatment():
    assert classify_line("2-STAR WHOLE-HOME CLEAN & SOFT WATER", "works with your water heater")[0::2] == ("WATER_TREATMENT", "WATER_SOFTENER")
    assert classify_line("AQUA FLO UNDER-SINK REVERSE OSMOSIS", "tankless design")[0::2] == ("WATER_TREATMENT", "OTHER")


def test_quote_uses_most_valuable_line_and_ignores_addons():
    c = classify_quote(None, [li("Service - GENN PRO PLAN", 300), li("Plumbing - Rheem Water Heater 40 Gallons", 2800),
                              li("HVAC Install - GENN Thermostat", 200)])
    assert (c.department, c.category, c.system) == ("PLUMBING", "REPLACEMENT", "WATER_HEATER")
    assert 0.5 < c.confidence <= 0.9


def test_plan_only_quote_still_classified():
    assert classify_quote(None, [li("Service - GENN PRO PLAN", 300)]).department in ("HVAC", "OTHER")


def test_hash_changes_with_content():
    a = source_hash("t", [li("HVAC Install - X")])
    assert a == source_hash("t", [li("HVAC Install - X")]) != source_hash("t", [li("HVAC Install - Y")])
