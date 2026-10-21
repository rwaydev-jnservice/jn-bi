"""Print the headline dashboard numbers (Sept 1 -> today) as JSON, for before/after checks.

    python scripts/dashboard_snapshot.py > before.json
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from config import get_settings  # noqa: E402
from database.connection import session_scope  # noqa: E402
from database.models import ProcessAudit  # noqa: E402
from services import metrics as MX  # noqa: E402
from services.periods import period  # noqa: E402


def main() -> None:
    start = date.fromisoformat(get_settings().jobber_history_start)
    p = period("Custom", custom=(start, datetime.now(timezone.utc).date()))
    out = {}
    with session_scope() as s:
        q = MX.quote_facts(s)
        for flow in sorted(q.Flow.unique()):
            sub = q[(q.Flow == flow) & (q.segment != "GC_PROJECT")]
            m = MX.quote_summary(sub, p)
            out[flow] = {"created": m["created"], "presented": m["sent"], "presented_value": round(float(m["sent_value"])),
                         "converted_of_presented": m["cohort_converted"], "rate": round(m["rate"], 3) if m["rate"] is not None else None,
                         "value_rate": round(float(m["value_rate"]), 3) if m["value_rate"] is not None else None}
        out["audit"] = dict(s.execute(select(ProcessAudit.gap_type, func.count()).where(ProcessAudit.resolved_at.is_(None))
                                      .group_by(ProcessAudit.gap_type)).all())
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
