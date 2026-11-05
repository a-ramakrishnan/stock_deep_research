"""The report pages (Streamlit). For now: the companies the shared data follows, and the evidence
a run collected (R1). The report itself comes in R3."""

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_deep_research import db, shared
from stock_deep_research.config import get_settings
from stock_deep_research.evidence import Evidence, new_run, sources
from stock_deep_research.models import Run, RunEvent

DISCLAIMER = (
    "Research inputs built from public filings and news, for information only. Not investment "
    "advice and not a recommendation to buy, sell or hold any security."
)
STATUS = {
    "ok": "Read",
    "listed": "Search result, not read",
    "blocked": "Blocked",
    "failed": "Failed",
    "unreadable": "No text",
}


def _contract() -> str | None:
    try:
        return shared.check_contract(db.shared_engine())
    except shared.ContractError as exc:
        st.error(str(exc))
        return None


def companies_page() -> None:
    st.title("Deep research")
    st.caption(DISCLAIMER)
    version = _contract()
    if version is None:
        return
    companies = shared.followed_companies(db.shared_engine())
    st.write(
        f"Reading stock_news_analyzer's shared data (contract {version}): "
        f"{len(companies)} companies followed."
    )
    st.dataframe(
        [{"Symbol": c.nse_symbol, "Company": c.name, "Owned": c.owned} for c in companies],
        hide_index=True,
    )


def _collect(query: str, version: str) -> int | None:
    settings = get_settings()
    shared_eng = db.shared_engine()
    company = shared.find_company(shared_eng, query)
    if company is None:
        st.warning(f"No single company matches '{query}'.")
        return None
    with Session(db.engine()) as session:
        run = new_run(
            session,
            company,
            "evidence",
            settings.research_model,
            settings.report_budget_usd,
            version,
        )
        ev = Evidence(session, run, shared_eng, company)
        counts = ev.collect()
        ev.event("collect", f"shared data: {sum(counts.values())} sources")
        run.status = "succeeded"
        session.commit()
        return run.id


def evidence_page() -> None:
    st.title("Evidence")
    st.caption(
        "Everything a run read, as it was when read: records of the shared data and web pages. "
        "A report's claims will cite passages of these. " + DISCLAIMER
    )
    version = _contract()
    if version is None:
        return
    with st.form("collect", border=False):
        left, right = st.columns([3, 1], vertical_alignment="bottom")
        query = left.text_input("Collect the shared data for", placeholder="LT, an ISIN or a name")
        if right.form_submit_button("Collect", type="primary") and query.strip():
            with st.spinner("Reading the shared data..."):
                made = _collect(query, version)
            if made:
                st.session_state["evidence-run"] = made

    with Session(db.engine()) as session:
        runs = session.scalars(select(Run).order_by(Run.id.desc()).limit(50)).all()
        if not runs:
            st.info("No runs yet. Collect a company's evidence above, or `sdr evidence LT`.")
            return
        ids = [r.id for r in runs]
        wanted = st.session_state.get("evidence-run")
        run_id = st.selectbox(
            "Run",
            ids,
            index=ids.index(wanted) if wanted in ids else 0,
            format_func=lambda i: next(
                f"{r.id} · {r.symbol or r.isin} · {r.requested_at:%d %b %Y %H:%M} · {r.request}"
                f" · ${r.cost_usd}"
                for r in runs
                if r.id == i
            ),
        )
        found = sources(session, run_id)
        events = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        ).all()
        _show(found, events)


def _show(found: list, events: list) -> None:
    web = [s for s in found if s.kind == "web"]
    pages = sum(1 for s in web if s.retrieval_status == "ok")
    cols = st.columns(3)
    cols[0].metric("Shared records", len(found) - len(web))
    cols[1].metric("Web pages read", pages)
    cols[2].metric(
        "Gaps (blocked, failed, no text)",
        sum(1 for s in found if s.retrieval_status in ("blocked", "failed", "unreadable")),
    )
    if events:
        with st.expander("What the run did"):
            for e in events:
                st.markdown(f"{e.seq}. **{e.stage}**: {e.message} (${e.cost_usd})")

    datasets = sorted({s.dataset or s.kind for s in found})
    chosen = st.selectbox("Dataset", ["All", *datasets])
    rows = [s for s in found if chosen == "All" or (s.dataset or s.kind) == chosen]
    table = pd.DataFrame(
        [
            {
                "Id": s.id,
                "Dataset": s.dataset or s.kind,
                "Title": s.title or s.ref,
                "Published": s.published_at.date() if s.published_at else None,
                "Status": STATUS.get(s.retrieval_status, s.retrieval_status),
                "Characters": len(s.text) if s.text else 0,
            }
            for s in rows
        ]
    )
    picked = st.dataframe(
        table, hide_index=True, on_select="rerun", selection_mode="single-row", width="stretch"
    )
    selected = picked.selection.rows
    if not selected:
        st.caption("Pick a row to read the source.")
        return
    s = rows[selected[0]]
    st.subheader(s.title or s.ref)
    facts = [f"`{s.ref}`", STATUS.get(s.retrieval_status, s.retrieval_status)]
    if s.publisher:
        facts.append(s.publisher)
    if s.published_at:
        facts.append(f"published {s.published_at:%d %b %Y}")
    facts.append(f"retrieved {s.retrieved_at:%d %b %Y %H:%M}")
    st.caption(" · ".join(facts))
    if s.kind == "web":
        st.link_button("Open the page", s.ref)
    if s.text:
        st.code(s.text, language=None, wrap_lines=True)
    else:
        st.info(f"No text kept ({s.retrieval_status}). {s.data or ''}")


def main() -> None:
    st.set_page_config(page_title="Deep research", page_icon=":material/travel_explore:")
    page = st.navigation(
        [
            st.Page(companies_page, title="Companies", icon=":material/apartment:", default=True),
            st.Page(evidence_page, title="Evidence", icon=":material/fact_check:"),
        ]
    )
    page.run()


if __name__ == "__main__":
    main()
