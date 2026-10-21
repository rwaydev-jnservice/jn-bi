"""Quote origin validation report (spec: before changing dashboard totals).

    python scripts/quote_origin_report.py

Recomputes origin fields (does NOT change dashboards while USE_QUOTE_ORIGIN_ENGINE
is off) and writes:
  validation_output/quote_origin_report.md
  validation_output/quote_origin_detail.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import get_settings  # noqa: E402
from database.connection import session_scope  # noqa: E402
from services import quote_origin  # noqa: E402
from services import quote_origin_report as R  # noqa: E402


def md_table(df) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if c in ("Value", "Total") and v == v and v is not None:
                v = f"${v:,.0f}"
            cells.append("" if v is None or v != v else str(v).replace("|", "/").replace("\n", " "))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    cfg = get_settings()
    with session_scope() as s:
        counts = quote_origin.rebuild(s)
        df = R.origin_table(s)
    out = ROOT / "validation_output"
    out.mkdir(exist_ok=True)
    detail = df[df["Origin Method"].isin(["INFERRED", "REVIEW_REQUIRED"])]
    detail_cols = ["Quote Number", "Quote ID", "Client", "Property", "Created At", "Flow Type", "Origin Method", "Origin Request",
                   "Origin Assessment", "Origin Service Job", "Origin Visit", "Converted Job(s)", "Confidence",
                   "Candidate Service Jobs", "Candidate Requests", "Reason"]
    df.drop(columns=["key"]).to_csv(out / "quote_origin_detail.csv", index=False)
    legacy = df.groupby(["Legacy Flow", "Flow Type"]).size().rename("Quotes").reset_index()
    text = [
        f"# Quote origin validation — quotes created since {cfg.jobber_history_start}",
        "",
        f"Dashboards still use the previous logic (`USE_QUOTE_ORIGIN_ENGINE={cfg.use_quote_origin_engine}`).",
        "",
        "## Summary", "", md_table(R.summary(df)), "",
        "## Quotes with no Request in Jobber — how they resolved", "", md_table(R.unlinked_resolution(df)), "",
        "## Previous classification → new classification", "", md_table(legacy), "",
        f"## Detail: all INFERRED and REVIEW_REQUIRED quotes ({len(detail)})", "", md_table(detail[detail_cols]), "",
    ]
    (out / "quote_origin_report.md").write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text[:14]))
    print(f"\nWrote {out / 'quote_origin_report.md'} and quote_origin_detail.csv ({len(df)} quotes). Counts: {counts}")


if __name__ == "__main__":
    main()
