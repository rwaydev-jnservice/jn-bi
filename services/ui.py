"""Small Streamlit helpers shared by pages."""

from __future__ import annotations

import streamlit as st

from services.periods import PRESETS, Period, local_today, period


def require_role(*roles: str) -> dict:
    user = st.session_state.get("user")
    if not user or user["role"] not in roles:
        st.error("You don't have access to this page.")
        st.stop()
    return user


def period_picker(key: str, default: str = "This Week", options: list[str] | None = None) -> Period:
    options = options or [p for p in PRESETS if p not in ("Tomorrow", "Next 7 Days", "Remaining This Week")]
    cols = st.columns([1, 2])
    preset = cols[0].selectbox("Period", options, index=options.index(default), key=f"{key}_preset")
    custom = None
    if preset == "Custom":
        today = local_today()
        rng = cols[1].date_input("Date range", (today.replace(day=1), today), key=f"{key}_range")
        if isinstance(rng, tuple) and len(rng) == 2:
            custom = rng
        else:
            st.stop()
    p = period(preset, custom=custom)
    cols[1].caption(f"{p.start_date:%b %d, %Y} – {p.end_date:%b %d, %Y} (Boston time)")
    return p


def quote_filters(df, key: str, show_flow: bool = True):
    """Common quote filters (spec section 59). GC bids are excluded by default: they are
    tracked separately because a few multi-million bids swamp every value metric."""
    from services import segments as SEG

    if df.empty:
        return df
    seg_labels = {s: SEG.LABELS[s] for s in SEG.ORDER}
    default_segs = [seg_labels[s] for s in SEG.ORDER if s != SEG.GC_PROJECT]
    with st.expander("Filters", expanded=False):
        c = st.columns(4)
        segs = c[0].multiselect("Segment (Pipedrive Category)", list(seg_labels.values()), default=default_segs, key=f"{key}_seg")
        sp = c[1].multiselect("Salesperson", sorted(df.Salesperson.dropna().unique()), key=f"{key}_sp")
        techs = sorted({t for v in df.Technician.dropna() for t in v.split(", ") if t != "—"})
        tech = c[2].multiselect("Technician", techs, key=f"{key}_tech")
        flow = c[3].multiselect("Estimate / Service", sorted(df.Flow.unique()), key=f"{key}_flow") if show_flow else []
        c = st.columns(4)
        dep = c[0].multiselect("Department", sorted(df.Department.unique()), key=f"{key}_dep")
        cat = c[1].multiselect("Category", sorted(df.Category.unique()), key=f"{key}_cat")
        sysm = c[2].multiselect("System", sorted(df.System.unique()), key=f"{key}_sys")
        owner = c[3].multiselect("Pipedrive Owner", sorted(df["Deal Owner"].dropna().unique()), key=f"{key}_own")
    inv = {v: k for k, v in seg_labels.items()}
    out = df[df.segment.isin([inv[s] for s in segs])]
    if sp:
        out = out[out.Salesperson.isin(sp)]
    if tech:
        out = out[out.Technician.apply(lambda v: any(t in (v or "").split(", ") for t in tech))]
    if flow:
        out = out[out.Flow.isin(flow)]
    for col, sel in (("Department", dep), ("Category", cat), ("System", sysm), ("Deal Owner", owner)):
        if sel:
            out = out[out[col].isin(sel)]
    if SEG.LABELS[SEG.GC_PROJECT] not in segs:
        st.caption("GC / Project Work bids are excluded (change in Filters → Segment).")
    return out


def fmt_money(v) -> str:
    return "—" if v is None else f"${v:,.0f}"


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.0%}"


def coming_soon(phase: str, what: str) -> None:
    st.info(f"**Available in {phase}.** {what}")
