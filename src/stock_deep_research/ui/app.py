"""The report pages (Streamlit). R1 adds the report itself; for now, the companies the shared
data follows and whether both databases are reachable."""

import streamlit as st

from stock_deep_research import db, shared

DISCLAIMER = (
    "Research inputs built from public filings and news, for information only. Not investment "
    "advice and not a recommendation to buy, sell or hold any security."
)


def main() -> None:
    st.set_page_config(page_title="Deep research", page_icon=":material/travel_explore:")
    st.title("Deep research")
    st.caption(DISCLAIMER)
    try:
        version = shared.check_contract(db.shared_engine())
    except shared.ContractError as exc:
        st.error(str(exc))
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


if __name__ == "__main__":
    main()
