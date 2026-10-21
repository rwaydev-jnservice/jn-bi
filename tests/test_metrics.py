"""Salesperson conversion metrics."""

from datetime import date

import pandas as pd

from services import metrics as MX
from services.periods import period

OCT = period("Custom", custom=(date(2026, 10, 1), date(2026, 10, 31)))


def q(sp, total, created, sent=None, converted=None, status="awaiting_response"):
    return {"Salesperson": sp, "total": float(total), "status": "converted" if converted else status,
            "is_converted": bool(converted), "created_at": created, "sent_at": sent, "converted_at": converted}


def frame(rows):
    df = pd.DataFrame(rows)
    for c in ("created_at", "sent_at", "converted_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


DF = frame([
    q("Cauet", 1000, "2026-10-02", "2026-10-02", "2026-10-20"),      # sent+converted in Oct
    q("Cauet", 3000, "2026-10-03", "2026-10-03"),                    # sent, open
    q("Cauet", 500, "2026-09-25", "2026-09-26", "2026-10-05"),       # sent in Sep, converted in Oct
    q("Jeff", 2000, "2026-10-04", "2026-10-05", "2026-11-02"),       # sent Oct, converted Nov (cohort counts it)
    q("Jeff", 700, "2026-10-10", None, None, "draft"),               # unsent
    q("Jeff", 900, "2026-10-11", None, None, "archived"),            # archived: not "unsent"
])


def test_summary_cohort_and_activity():
    m = MX.quote_summary(DF, OCT)
    assert m["created"] == 5 and m["created_value"] == 7600
    assert m["sent"] == 3 and m["sent_value"] == 6000
    assert m["cohort_converted"] == 2 and m["rate"] == 2 / 3
    assert m["value_rate"] == 3000 / 6000
    assert m["converted"] == 2 and m["converted_value"] == 1500  # converted in Oct (one sent in Sep)
    assert m["avg_ticket"] == 750
    assert m["unsent"] == 1 and m["unsent_value"] == 700


def test_by_salesperson():
    t = MX.by_salesperson(DF, OCT).set_index("Salesperson")
    assert t.loc["Cauet", "Presented"] == 2 and t.loc["Cauet", "Conversion"] == 0.5
    assert t.loc["Cauet", "Value conversion"] == 1000 / 4000
    assert t.loc["Jeff", "Conversion"] == 1.0 and t.loc["Jeff", "Unsent"] == 1


def test_on_the_spot_conversion_counts_as_presented():
    df = frame([q("A", 400, "2026-10-02", None, "2026-10-02"), q("A", 600, "2026-10-03", "2026-10-03")])
    m = MX.quote_summary(df, OCT)
    assert m["sent"] == 2 and m["sent_only"] == 1
    assert m["rate"] == 0.5 and m["value_rate"] == 0.4
    assert m["unsent"] == 0  # converted quotes are never "unsent"


def test_period_boundary_uses_boston_time():
    # 2026-11-01 02:00 UTC is still Oct 31 in Boston
    df = frame([q("A", 100, "2026-11-01T02:00:00Z", "2026-11-01T02:00:00Z")])
    assert MX.quote_summary(df, OCT)["sent"] == 1


def test_empty_is_safe():
    m = MX.quote_summary(DF.iloc[0:0], OCT)
    assert m["rate"] is None and m["sent"] == 0
