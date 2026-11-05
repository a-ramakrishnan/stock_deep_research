"""Figures computed by code from the shared records, kept as one more source.

A model never does arithmetic that a report then states as fact. Growth, margins, trailing EPS,
P/E, net debt and cash conversion are computed here from the records already kept, and each line
names its formula and the sources it used (`[S12]`), so a finding can cite the line and the
review can trace it. Comparable periods only: a quarter against the same quarter a year earlier,
same basis.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from stock_deep_research.evidence import Evidence
from stock_deep_research.models import Source


def _d(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _date(v) -> date | None:
    return date.fromisoformat(str(v)[:10]) if v else None


def _pct(new: Decimal, old: Decimal) -> str:
    return f"{(new - old) / abs(old) * 100:.1f}%"


def _r(v: Decimal, places: int = 2) -> str:
    return f"{v:.{places}f}"


@dataclass
class _Period:
    source: Source
    end: date
    months: int
    basis: str
    label: str
    revenue: Decimal | None
    pat: Decimal | None
    eps: Decimal | None


def _periods(found: list[Source]) -> list[_Period]:
    out = []
    for s in found:
        d = s.data or {}
        if not d.get("primary") or not d.get("period_end"):
            continue
        out.append(
            _Period(
                s,
                _date(d["period_end"]),
                int(d["months"]),
                d["basis"],
                d.get("period") or d["period_end"],
                _d(d.get("revenue_cr")),
                _d(d.get("pat_cr")),
                _d(d.get("eps")),
            )
        )
    return sorted(out, key=lambda p: p.end, reverse=True)


def _year_before(p: _Period, rows: list[_Period]) -> _Period | None:
    for q in rows:
        if q.months == p.months and q.basis == p.basis and 360 <= (p.end - q.end).days <= 370:
            return q
    return None


def lines(evidence: Evidence) -> list[str]:
    results = [s for s in evidence.read("results") if s.data]
    out: list[str] = []
    periods = _periods(results)
    quarters = [p for p in periods if p.months == 3]
    years = [p for p in periods if p.months == 12]

    for p in quarters[:8]:
        bits = []
        if p.revenue and p.pat:
            bits.append(f"PAT margin {_r(p.pat / p.revenue * 100, 1)}%")
        prior = _year_before(p, quarters)
        if prior and p.revenue and prior.revenue:
            bits.append(f"revenue {_pct(p.revenue, prior.revenue)} on {prior.label}")
        if prior and p.pat and prior.pat:
            bits.append(f"PAT {_pct(p.pat, prior.pat)} on {prior.label}")
        if bits:
            cited = f"[S{p.source.id}" + (f",S{prior.source.id}]" if prior else "]")
            out.append(f"{p.label} ({p.basis}): " + "; ".join(bits) + f" {cited}")

    # trailing EPS: the latest four quarters, consecutive, one basis
    latest = quarters[:1]
    if latest:
        basis = latest[0].basis
        run = [q for q in quarters if q.basis == basis][:4]
        consecutive = len(run) == 4 and all(
            80 <= (a.end - b.end).days <= 100 for a, b in zip(run, run[1:], strict=False)
        )
        if consecutive and all(q.eps is not None for q in run):
            eps_ttm = sum(q.eps for q in run)
            ids = ",".join(f"S{q.source.id}" for q in run)
            out.append(
                f"EPS, trailing four quarters ({basis}, {run[-1].label} to {run[0].label}): "
                f"Rs {_r(eps_ttm)} [{ids}]"
            )
            price = evidence.read("price_summary")
            close = _d((price[0].data or {}).get("last_close")) if price else None
            if close and eps_ttm > 0:
                out.append(
                    f"P/E, trailing: {_r(close / eps_ttm, 1)} = close Rs {close} on "
                    f"{price[0].data['as_of']} [S{price[0].id}] / trailing EPS Rs {_r(eps_ttm)} "
                    "(EPS as reported: a split or bonus after these quarters is not adjusted for)"
                )

    if len(years) >= 2:
        same = [y for y in years if y.basis == years[0].basis][:6]
        first, last = same[-1], same[0]
        n = round((last.end - first.end).days / 365)
        if n >= 1 and first.revenue and last.revenue and first.revenue > 0:
            cagr = ((last.revenue / first.revenue) ** (Decimal(1) / n) - 1) * 100
            out.append(
                f"Revenue growth a year, {first.label} to {last.label} ({n} years, "
                f"{last.basis}): {_r(cagr, 1)}% [S{first.source.id},S{last.source.id}]"
            )
        if n >= 1 and first.pat and last.pat and first.pat > 0 and last.pat > 0:
            cagr = ((last.pat / first.pat) ** (Decimal(1) / n) - 1) * 100
            out.append(
                f"PAT growth a year, {first.label} to {last.label} ({n} years, {last.basis}): "
                f"{_r(cagr, 1)}% [S{first.source.id},S{last.source.id}]"
            )

    sheets = sorted(
        (s for s in evidence.read("balance_sheets") if s.data),
        key=lambda s: s.data["period_end"],
        reverse=True,
    )
    for s in sheets[:4]:
        d = s.data
        debt, cash = _d(d.get("borrowings_cr")), _d(d.get("cash_cr"))
        bits = []
        if debt is not None and cash is not None:
            bits.append(f"net debt Rs {_r(debt - cash, 0)} crore (borrowings less cash)")
        equity = _d(d.get("equity_cr"))
        if debt is not None and equity:
            bits.append(f"debt to equity {_r(debt / equity)}")
        cfo, capex = _d(d.get("cfo_cr")), _d(d.get("capex_cr"))
        if cfo is not None and capex is not None:
            bits.append(f"free cash flow Rs {_r(cfo - capex, 0)} crore over {d['months']} months")
        match = next(
            (
                p
                for p in periods
                if p.end == _date(d["period_end"])
                and p.months == int(d["months"] or 0)
                and p.basis == d["basis"]
            ),
            None,
        )
        cited = f"S{s.id}"
        if cfo is not None and match and match.pat and match.pat > 0:
            bits.append(f"cash from operations {_r(cfo / match.pat * 100, 0)}% of PAT")
            cited += f",S{match.source.id}"
        if bits:
            out.append(
                f"Balance sheet {d['period_end']} ({d['basis']}): "
                + "; ".join(bits)
                + f" [{cited}]"
            )
    return out


def derive(evidence: Evidence) -> Source | None:
    """Compute the figures and keep them as the run's `derived` source."""
    found = lines(evidence)
    if not found:
        return None
    sym = evidence.company.nse_symbol or evidence.company.isin
    return evidence.keep(
        kind="derived",
        dataset="derived",
        ref=f"derived:{sym}:figures",
        title="Figures computed from the filings",
        body="\n".join(found),
        data={"lines": found},
    )
