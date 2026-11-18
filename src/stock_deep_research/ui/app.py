"""The report pages (Streamlit). For now: the companies the shared data follows, and the evidence
a run collected (R1). The report itself comes in R3."""

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stock_deep_research import db, shared
from stock_deep_research.config import get_settings
from stock_deep_research.evidence import Evidence, new_run, sources
from stock_deep_research.models import Excerpt, Run, RunEvent

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


SUPPORT = {
    "supported": ":green-badge[supported]",
    "conflicting": ":orange-badge[conflicting]",
    "inferred": ":blue-badge[inferred]",
    "insufficient": ":red-badge[insufficient]",
}
COVER = {"answered": "Answered", "partly": "Partly answered", "open": "Open"}


def _run_research(query: str, extra: list[str], version: str) -> int | None:
    from stock_deep_research.research import research

    shared_eng = db.shared_engine()
    company = shared.find_company(shared_eng, query)
    if company is None:
        st.warning(f"No single company matches '{query}'.")
        return None
    with st.status(f"Researching {company.name}...", expanded=True) as status:
        with Session(db.engine()) as session:
            run = research(
                session,
                shared_eng,
                company,
                request="report" + "".join(f"; {q}" for q in extra),
                questions=extra,
                contract_version=version,
                progress=st.write,
            )
            if run.status in ("succeeded", "partial"):
                from stock_deep_research.report import OUTCOMES, write_report

                made = write_report(session, run, company.name, progress=st.write)
                st.session_state["report-isin"] = run.isin
                st.write(f"Report version {made.version}: {OUTCOMES[made.outcome]}.")
            status.update(label=f"Run {run.id}: {run.status}, ${run.cost_usd}", state="complete")
            return run.id


def findings_page() -> None:
    from stock_deep_research.plan import SECTION, from_json
    from stock_deep_research.research import findings

    st.title("Findings")
    st.caption(
        "What a research run found: atomic claims, each with the passages it rests on and how "
        "well they support it, and which of the report's questions are answered. " + DISCLAIMER
    )
    version = _contract()
    if version is None:
        return
    settings = get_settings()
    with st.expander("Research a company", expanded=False):
        with st.form("research", border=False):
            query = st.text_input("Company", placeholder="LT, an ISIN or a name")
            extra = st.text_area("Your own questions (one a line, optional)")
            st.caption(
                f"Takes a few minutes and up to ${settings.report_budget_usd * 0.7:.2f} of the "
                f"${settings.report_budget_usd:.2f} report budget."
            )
            go = st.form_submit_button("Research", type="primary")
        if go and query.strip():
            if not settings.gemini_api_key:
                st.error("Research needs GEMINI_API_KEY in .env.")
            else:
                made = _run_research(query, [q for q in extra.splitlines() if q.strip()], version)
                if made:
                    st.session_state["findings-run"] = made

    with Session(db.engine()) as session:
        runs = session.scalars(
            select(Run).where(Run.plan.has_key("questions")).order_by(Run.id.desc()).limit(50)
        ).all()
        if not runs:
            st.info("No research runs yet. Start one above, or `sdr research LT`.")
            return
        ids = [r.id for r in runs]
        wanted = st.session_state.get("findings-run")
        run_id = st.selectbox(
            "Run",
            ids,
            index=ids.index(wanted) if wanted in ids else 0,
            format_func=lambda i: next(
                f"{r.id} · {r.symbol or r.isin} · {r.requested_at:%d %b %Y %H:%M} · {r.status}"
                f" · ${r.cost_usd}"
                for r in runs
                if r.id == i
            ),
        )
        run = session.get(Run, run_id)
        plan = from_json(run.plan.get("questions", []))
        cover = run.plan.get("coverage", {})
        found = findings(session, run_id)
        cols = st.columns(4)
        cols[0].metric("Findings", len(found))
        cols[1].metric("Answered", sum(v == "answered" for v in cover.values()))
        cols[2].metric("Open", sum(v == "open" for v in cover.values()))
        cols[3].metric(f"Cost (budget ${run.budget_usd:.2f})", f"${run.cost_usd:.2f}")
        if run.error:
            st.error(run.error)
        if run.status in ("succeeded", "partial") and settings.gemini_api_key:
            if st.button("Write a new version of the report from these findings"):
                from stock_deep_research.report import OUTCOMES, write_report

                company = shared.find_company(db.shared_engine(), run.isin)
                with st.status("Writing and reviewing...", expanded=True):
                    made = write_report(
                        session, run, company.name if company else run.isin, progress=st.write
                    )
                st.session_state["report-isin"] = run.isin
                st.success(
                    f"Report version {made.version}: {OUTCOMES[made.outcome]}. See the Report page."
                )

        for key in dict.fromkeys(q.section for q in plan):
            section = SECTION[key]
            st.subheader(section.title)
            for q in (q for q in plan if q.section == key):
                state = cover.get(q.id, "open")
                st.markdown(
                    f"**{q.text}** · {COVER[state]}" + ("" if q.mandatory else " (optional)")
                )
                mine = [f for f in found if f.question == q.id]
                if not mine:
                    st.caption("Nothing found yet.")
                for f in mine:
                    _finding(session, f)


def _finding(session: Session, f) -> None:
    kind = "" if f.claim_type == "fact" else " :violet-badge[interpretation]"
    when = f" · {f.effective_date:%d %b %Y}" if f.effective_date else ""
    st.markdown(f"- {f.claim} {SUPPORT.get(f.support, f.support)}{kind}{when}")
    quotes = session.scalars(select(Excerpt).where(Excerpt.id.in_(f.excerpt_ids or []))).all()
    for ex in quotes:
        where = f"S{ex.source_id}" if ex.start_char is not None else f"S{ex.source_id}, not found"
        st.caption(f"  > “{ex.quote}” ({where})")
    if f.limitations:
        st.caption(f"  Limits: {f.limitations}")


def report_page() -> None:
    from stock_deep_research.models import Report
    from stock_deep_research.report import OUTCOMES, versions

    st.title("Report")
    if _contract() is None:
        return
    with Session(db.engine()) as session:
        isins = list(
            session.scalars(
                select(Report.isin).group_by(Report.isin).order_by(func.max(Report.id).desc())
            )
        )
        if not isins:
            st.info(
                "No reports yet. Research a company on the Findings page, or `sdr research LT`."
            )
            return
        names = {c.isin: c for c in shared.followed_companies(db.shared_engine())}
        wanted = st.session_state.get("report-isin")
        left, right = st.columns([3, 1])
        isin = left.selectbox(
            "Company",
            isins,
            index=isins.index(wanted) if wanted in isins else 0,
            format_func=lambda i: f"{names[i].nse_symbol} · {names[i].name}" if i in names else i,
        )
        reports = versions(session, isin)
        chosen = right.selectbox(
            "Version",
            reports,
            format_func=lambda r: f"{r.version} · {r.created_at:%d %b %Y} · {OUTCOMES[r.outcome]}",
        )
        badge = {
            "validated": ":green-badge[Validated]",
            "validated_with_limitations": ":orange-badge[Validated, with limitations]",
            "rejected": ":red-badge[Rejected]",
        }[chosen.outcome]
        st.markdown(badge)
        st.download_button(
            "Download Markdown",
            chosen.markdown,
            file_name=f"{(names[isin].nse_symbol if isin in names else isin)}-report-v"
            f"{chosen.version}.md",
            mime="text/markdown",
        )
        st.markdown(chosen.markdown.replace("$", "\\$"))


def evaluation_page() -> None:
    from stock_deep_research.eval import audit_report, run_traps, score_run
    from stock_deep_research.models import Report
    from stock_deep_research.report import OUTCOMES

    st.title("Evaluation & Data Veracity")
    st.caption(
        "Agent evaluation scorecard (R4) and 5-layer veracity auditing: how every claim and "
        "figure in a final report is traced back to its exact source passage and SHA-256 hash. "
        + DISCLAIMER
    )

    with st.expander(
        "How we verify veracity of data in the final report (5 layers)", expanded=False
    ):
        st.markdown(
            "1. **Layer 1 · Cryptographic Source & Quote Offset (`excerpts.verify`):** Every "
            "`Source` stores a `sha256` hash of its text when read. Every `Finding` must quote a "
            "verbatim passage, and code locates its character index (`start_char`) in "
            "`Source.text`. Facts whose quotes are not found are downgraded to `insufficient`.\n"
            "2. **Layer 2 · Figure-to-Quote Decimal Matching (`review.unsupported_figures`):** "
            "Every number in a report paragraph is parsed and matched against the numbers inside "
            "the verbatim quotes of the findings cited by *that* paragraph (allowing standard "
            "rounding).\n"
            "3. **Layer 3 · Deterministic Math Provenance (`derived.py`):** Growth rates, "
            "margins, trailing EPS, P/E, net debt, D/E, FCF and cash conversion are computed in "
            "Python `Decimal` from primary XBRL records (`derived:<SYMBOL>:figures`), never by "
            "model arithmetic.\n"
            "4. **Layer 4 · Basis, Period & Freshness Consistency (`eval.audit_report`):** "
            "Flags paragraphs citing non-primary `standalone` figures without stating "
            "`'standalone'`, checks whether `financials` cites the newest primary quarter, and "
            "blocks future dates outside `catalysts`.\n"
            "5. **Layer 5 · Omission, Contradiction & Gap Honesty (`eval.score_run`):** "
            "Flags collected `red_flags` sources or `conflicting` findings omitted from the "
            "report, and verifies that unanswered questions appear as explicit `*Not found:*` gaps."
        )
        traps = run_traps()
        st.dataframe(
            [
                {
                    "Trap": t.name,
                    "Verification Layer": t.layer,
                    "Status": "Caught" if t.passed else "MISSED",
                    "Detail": t.detail,
                }
                for t in traps
            ],
            hide_index=True,
            width="stretch",
        )

    with Session(db.engine()) as session:
        runs = session.scalars(
            select(Run).where(Run.plan.has_key("questions")).order_by(Run.id.desc()).limit(50)
        ).all()
        if not runs:
            st.info("No research runs yet. Run `sdr research LT` to evaluate a report.")
            return

        cards = [score_run(session, r.id) for r in runs]
        with st.expander("All research runs benchmark", expanded=False):
            st.dataframe(
                [
                    {
                        "Run": c.run_id,
                        "Symbol": c.symbol,
                        "Status": c.status,
                        "Outcome": OUTCOMES.get(c.report_outcome, c.report_outcome or "-"),
                        "Coverage": (
                            f"{c.mandatory_answered}/{c.mandatory_total} "
                            f"({c.mandatory_coverage_pct:.0f}%)"
                        ),
                        "Quote Fidelity": (
                            f"{c.quotes_located}/{c.quotes_total} ({c.quote_fidelity_pct:.0f}%)"
                        ),
                        "Figure Precision": (
                            f"{c.figures_verified}/{c.figures_total} "
                            f"({c.figure_precision_pct:.0f}%)"
                        ),
                        "Dropped Paras": c.paragraphs_dropped,
                        "Warnings": len(c.veracity_warnings),
                        "Cost ($)": float(c.cost_usd),
                        "Time (s)": c.wall_seconds,
                    }
                    for c in cards
                ],
                hide_index=True,
                width="stretch",
            )

        ids = [r.id for r in runs]
        run_id = st.selectbox(
            "Inspect run & report veracity",
            ids,
            format_func=lambda i: next(
                f"Run {r.id} · {r.symbol or r.isin} · {r.requested_at:%d %b %Y %H:%M} · "
                f"{r.status} · ${r.cost_usd}"
                for r in runs
                if r.id == i
            ),
        )
        card = next(c for c in cards if c.run_id == run_id)

        cols = st.columns(5)
        cols[0].metric(
            "Mandatory coverage",
            f"{card.mandatory_answered}/{card.mandatory_total}",
            f"{card.mandatory_coverage_pct:.0f}%",
        )
        cols[1].metric(
            "Quote fidelity",
            f"{card.quotes_located}/{card.quotes_total}",
            f"{card.quote_fidelity_pct:.0f}% located",
        )
        cols[2].metric(
            "Figure precision",
            f"{card.figures_verified}/{card.figures_total}",
            f"{card.figure_precision_pct:.0f}% traced",
        )
        cols[3].metric(
            "Paragraphs verified",
            f"{card.paragraphs_verified}/{card.paragraphs_total}",
            f"{card.paragraphs_dropped} dropped",
        )
        cols[4].metric(
            "Run cost",
            f"${card.cost_usd:.2f}",
            f"${card.cost_per_supported_fact}/fact" if card.cost_per_supported_fact else None,
        )

        if card.stage_costs:
            fu_note = (
                f" · Follow-ups: {card.followup_questions} "
                f"({card.followup_sources_read} sources read)"
                if card.followup_questions
                else ""
            )
            st.caption(
                "Stage spend: "
                + " · ".join(f"**{k}** \\${v:.4f}" for k, v in card.stage_costs.items())
                + fu_note
            )

        rep = session.scalar(
            select(Report)
            .where(Report.run_id == run_id)
            .order_by(Report.version.desc(), Report.id.desc())
            .limit(1)
        )
        if rep is None:
            st.info("No report has been written for this run yet.")
            return

        veracity = audit_report(session, rep)
        outcome_label = OUTCOMES.get(veracity.outcome, veracity.outcome)
        st.subheader(f"Report v{veracity.version} Veracity Audit ({outcome_label})")
        vcols = st.columns(4)
        vcols[0].metric(
            "Excerpt hash & offset check",
            f"{veracity.excerpts_intact}/{veracity.excerpts_checked}",
        )
        vcols[1].metric("Figures from shared records", veracity.figures_from_shared)
        vcols[2].metric("Figures from derived.py math", veracity.figures_from_derived)
        vcols[3].metric("Figures from web pages", veracity.figures_from_web)

        if veracity.warnings:
            for w in veracity.warnings:
                st.warning(f"Veracity warning: {w}")
        else:
            st.success(
                "All 5 veracity layers passed with zero basis, freshness or omission warnings."
            )

        fig_rows = [
            {
                "Paragraph": p.where,
                "Figure Written": ft.written,
                "Matched Value": str(ft.matched_value) if ft.matched_value is not None else "-",
                "Status": "Verified" if ft.verified else "UNSUPPORTED",
                "Finding": f"F{ft.finding_id}" if ft.finding_id else "-",
                "Source": f"S{ft.source_id}" if ft.source_id else "-",
                "Dataset": ft.dataset or "-",
                "Source Ref": ft.source_ref or "-",
                "Verbatim Quote": ft.quote or "-",
            }
            for p in veracity.paragraphs
            for ft in p.figures
        ]
        st.markdown("#### Figure-by-Figure Lineage (Every Number in the Final Report)")
        if fig_rows:
            st.dataframe(fig_rows, hide_index=True, width="stretch")
        else:
            st.caption("No figures in this report.")

        st.markdown("#### Paragraph-by-Paragraph Claim Audit")
        st.dataframe(
            [
                {
                    "Address": p.where,
                    "Section": p.section,
                    "Kind": p.kind,
                    "Status": "Verified" if p.verified else "Issues found",
                    "Findings": ", ".join(f"F{i}" for i in p.finding_ids) or "-",
                    "Sources": ", ".join(f"S{i}" for i in p.source_ids) or "-",
                    "Figures": len(p.figures),
                    "Issues": "; ".join(p.issues) if p.issues else "None",
                    "Text": p.text,
                }
                for p in veracity.paragraphs
            ],
            hide_index=True,
            width="stretch",
        )


def main() -> None:
    st.set_page_config(page_title="Deep research", page_icon=":material/travel_explore:")
    page = st.navigation(
        [
            st.Page(companies_page, title="Companies", icon=":material/apartment:", default=True),
            st.Page(report_page, title="Report", icon=":material/article:"),
            st.Page(findings_page, title="Findings", icon=":material/checklist:"),
            st.Page(evidence_page, title="Evidence", icon=":material/fact_check:"),
            st.Page(evaluation_page, title="Evaluation & Veracity", icon=":material/verified:"),
        ]
    )
    page.run()


if __name__ == "__main__":
    main()
