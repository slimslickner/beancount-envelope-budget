"""Tests for the shared report view models, especially the pool ledger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

from beancount import loader

from beancount_envelope_budget.core import BudgetResult, compute
from beancount_envelope_budget.report import (
    POOL_ACTIVITY,
    POOL_ALLOCATION,
    ReportEntry,
    SuggestionSummary,
    _top_entries,
    build_reports,
    build_suggestions,
    build_view,
    funding_explanation,
    funding_explanation_lines,
    month_statuses,
    plan_top_up,
    pool_ledger,
    pool_ledger_json,
    prepare_view,
    suggestion_cells,
    suggestion_summary_line,
    to_jsonable,
)

D = Decimal

_LEDGERS = Path(__file__).parent / "ledgers"


def _compute(name: str, *, collect_activity: bool = True) -> BudgetResult:
    """Compute the budget for a fixture ledger.

    Args:
        name: The fixture file name.
        collect_activity: Whether to collect per-posting activity detail.

    Returns:
        The computed :class:`BudgetResult`.
    """
    entries, _errors, options = loader.load_file(_LEDGERS / name)
    return compute(entries, options, collect_activity=collect_activity)


def test_pool_ledger_reports_lines_and_low() -> None:
    """The ledger's lines, order, low and opening are reported."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result)
    assert ledger.opening == D("0")
    assert [(e.date, e.kind, e.amount, e.balance) for e in ledger.entries] == [
        (date(2024, 1, 1), POOL_ALLOCATION, D("-500"), D("-500")),
        (date(2024, 1, 15), POOL_ACTIVITY, D("3000"), D("2500")),
    ]
    assert ledger.low == D("-500")
    assert ledger.low_date == date(2024, 1, 1)


def test_pool_warning_uses_the_month_end() -> None:
    """W001 reports a month that closes negative, not an intra-month dip."""
    result = _compute("pool_shortfall.bean")
    warning = next(d for d in result.diagnostics if d.code == "W001")
    assert "2024-01" in warning.message
    assert "-450.00" in warning.message
    assert "opened the month at 0.00 USD" in warning.message


def test_pool_ledger_window_carries_opening() -> None:
    """A window starts at the pool balance carried in, and filters the lines."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result, start=date(2024, 1, 15), end=date(2024, 1, 31))
    assert ledger.opening == D("-500")
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.kind == POOL_ACTIVITY
    assert entry.balance == D("2500")


def test_pool_ledger_by_day() -> None:
    """A single-day window opens at the balance before that day."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result, start=date(2024, 1, 1), end=date(2024, 1, 1))
    assert ledger.opening == D("0")
    assert [e.kind for e in ledger.entries] == [POOL_ALLOCATION]
    assert ledger.low == D("-500")


def test_pool_ledger_nets_each_transaction() -> None:
    """A transaction's pool postings net into one line, so payroll is not a dip."""
    result = _compute("payroll.bean")
    ledger = pool_ledger(result)
    pool_lines = [e for e in ledger.entries if e.kind == POOL_ACTIVITY]
    assert [e.amount for e in pool_lines] == [D("3500")]
    assert ledger.low == D("3500")
    assert "W001" not in [d.code for d in result.diagnostics]


def test_pool_ledger_includes_rounding() -> None:
    """A folded rounding residual appears as a pool activity line."""
    result = _compute("rounding_residual.bean")
    ledger = pool_ledger(result)
    pool_lines = [e for e in ledger.entries if e.kind == POOL_ACTIVITY]
    assert any(e.amount == D("0.01") for e in pool_lines)


def test_pool_ledger_without_start() -> None:
    """A ledger with no start has an empty pool ledger."""
    result = _compute("no_start.bean")
    ledger = pool_ledger(result)
    assert ledger.entries == ()
    assert ledger.low is None
    assert ledger.low_date is None


def test_pool_ledger_json_is_stable() -> None:
    """The JSON serialisation uses fixed money strings and ISO dates."""
    result = _compute("mid_month_dip.bean")
    payload = pool_ledger_json(pool_ledger(result))
    assert payload["opening"] == "0.00"
    assert payload["low"] == "-500.00"
    assert payload["low_date"] == "2024-01-01"
    entries = cast("list[dict[str, object]]", payload["entries"])
    assert entries[0]["kind"] == POOL_ALLOCATION
    assert entries[0]["envelope"] == "Groceries"
    assert entries[0]["amount"] == "-500.00"


def test_build_view_pool_cell_carries_ledger() -> None:
    """The pool cell for a month carries that month's ledger lines."""
    result = _compute("mid_month_dip.bean")
    view = build_view(result, date(2024, 1, 1))
    assert [e.kind for e in view.pool[0].pool] == [POOL_ALLOCATION, POOL_ACTIVITY]
    assert view.pool[0].pool[0].balance == D("-500")


def test_reports_balances_row_spans_every_month() -> None:
    """The first report row is the cash and pool balance, per month."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert reports.has_start is True
    assert reports.currency == "USD"
    assert [row.months[0].month for row in reports.rows] == [date(2024, 1, 1)] * 4
    balance = reports.rows[0]
    assert balance.title == "Cash and pool"
    assert [(e.name, e.amount) for e in balance.months[0].entries] == [
        ("Cash", D("3030")),
        ("Pool", D("2300")),
    ]


def test_reports_row_titles() -> None:
    """The rows are the balance, then the three top-ten rankings."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert [row.title for row in reports.rows] == [
        "Cash and pool",
        "Top spending",
        "Top allocated",
        "Top ending balance",
    ]


def test_reports_column_widths() -> None:
    """The report exposes the longest name and amount, for uniform columns."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert reports.name_chars == 9
    assert reports.amount_chars == len("3030.00")
    empty = build_reports(_compute("no_start.bean"))
    assert empty.name_chars == 0
    assert empty.amount_chars == 0


def test_reports_rankings_order_and_filter() -> None:
    """Each ranking is envelope-level, ordered high to low, positive only."""
    reports = build_reports(_compute("parent_groups.bean"))
    _balance, spending, allocated, closing = reports.rows
    assert [e.name for e in spending.months[0].entries] == ["Rent", "Groceries", "Car"]
    assert [e.name for e in allocated.months[0].entries] == [
        "Rent",
        "Groceries",
        "Car",
        "Misc",
    ]
    # Rent closes at zero, so it is not a "top ending balance".
    assert [e.name for e in closing.months[0].entries] == [
        "Groceries",
        "Car",
        "Misc",
    ]
    assert closing.months[0].entries[0].amount == D("380")


def test_reports_without_start() -> None:
    """A ledger with no start has an empty reports view."""
    reports = build_reports(_compute("no_start.bean"))
    assert reports.has_start is False
    assert reports.rows == ()


def test_reports_filter_to_a_month_range() -> None:
    """A start/end range narrows the months shown."""
    reports = build_reports(
        _compute("fund_backfill.bean"),
        start=date(2024, 2, 1),
        end=date(2024, 2, 28),
    )
    assert [month.month for month in reports.rows[0].months] == [date(2024, 2, 1)]


def test_reports_balances_stay_cumulative_when_filtered() -> None:
    """A narrowed range still shows balances from the whole history.

    The pool holds money that earlier months moved through, so a balance
    recomputed from only the visible month would be wrong; it must be drawn
    from the whole budget.
    """
    reports = build_reports(
        _compute("fund_backfill.bean"),
        start=date(2024, 2, 1),
        end=date(2024, 2, 28),
    )
    balance = reports.rows[0].months[0]
    assert [(entry.name, entry.amount) for entry in balance.entries] == [
        ("Cash", D("4750")),
        ("Pool", D("5000")),
    ]


def test_reports_empty_range_has_no_rows() -> None:
    """A range with no computed months leaves the rows empty but started."""
    reports = build_reports(_compute("fund_backfill.bean"), start=date(2030, 1, 1))
    assert reports.has_start is True
    assert reports.rows == ()


def test_top_entries_caps_and_breaks_ties() -> None:
    """Ranked entries cap at TOP_N and break ties by name."""
    entries = [ReportEntry(f"env{index:02d}", D(1)) for index in range(12)]
    top = _top_entries(entries)
    assert len(top) == 10
    assert [entry.name for entry in top] == [f"env{index:02d}" for index in range(10)]


def test_up_to_cell_caps_the_target_and_reads_under() -> None:
    """An ``up-to`` cell caps its target and reports the room left (#22)."""
    view = build_view(_compute("up_to.bean"), date(2024, 3, 1))
    car = next(e for e in view.envelopes if e.name == "Car")
    cell = car.cells[0]
    assert cell.opening == D("500")
    assert cell.target == D("600")
    assert cell.up_to == D("600")
    assert cell.goal is None
    assert cell.funding == "under"
    assert cell.fill == D("500") / D("600")


def test_up_to_cell_reads_on_at_the_ceiling() -> None:
    """An ``up-to`` cell at its ceiling has no room left and reads on plan."""
    view = build_view(_compute("up_to_full.bean"), date(2024, 4, 1))
    car = next(e for e in view.envelopes if e.name == "Car")
    cell = car.cells[0]
    assert cell.opening == D("600")
    assert cell.closing == D("600")
    assert cell.funding == "on"
    assert cell.fill == D("1")


def test_monthly_partial_allocation_reads_under() -> None:
    """A ``monthly`` envelope rows only what the month allocated."""
    view = build_view(_compute("monthly_flow.bean"), date(2024, 1, 1), 1)
    partial = next(e for e in view.envelopes if e.name == "Partial")
    cell = partial.cells[0]
    assert cell.target == D("100")
    assert cell.funding == "under"
    assert cell.fill == D("0.5")


def test_monthly_over_allocation_reads_over() -> None:
    """A ``monthly`` envelope allocated above its plan reads over, ring full."""
    view = build_view(_compute("monthly_flow.bean"), date(2024, 1, 1), 1)
    over = next(e for e in view.envelopes if e.name == "Over")
    cell = over.cells[0]
    assert cell.funding == "over"
    assert cell.fill == D("1")


def test_monthly_carried_balance_does_not_fund_the_next_month() -> None:
    """A carried balance is not this month's funding: the plan is a flow."""
    result = _compute("monthly_flow.bean")
    report = next(r for r in result.envelopes if r.name == "Carried")
    # January is funded to plan by its own allocation.
    assert report.funded_at(date(2024, 1, 31)) == D("100")
    # February opens with the January balance but allocates nothing, so the
    # flow is zero and the month still needs the full monthly amount.
    assert report.funded_at(date(2024, 2, 29)) == D("0")
    assert plan_top_up(report, date(2024, 2, 29)) == D("100.00")

    view = build_view(result, date(2024, 2, 1), 1)
    carried = next(e for e in view.envelopes if e.name == "Carried")
    cell = carried.cells[0]
    assert cell.opening == D("100")
    assert cell.allocated == D("0")
    assert cell.funding == "under"
    assert cell.fill == D("0")


def test_build_view_with_a_prepared_index_matches() -> None:
    """A view built from a prepared index is identical to one built afresh."""
    result = _compute("parent_groups.bean")
    index = prepare_view(result)
    for count in (1, 2, 3):
        view = build_view(result, result.months[0], count)
        cached = build_view(result, result.months[0], count, index=index)
        assert to_jsonable(view) == to_jsonable(cached)


def test_month_status_counts_and_worst() -> None:
    """A month's summary tallies each status and reports the worst one."""
    view = build_view(_compute("funding_colors.bean"), date(2024, 1, 1), 1)
    status = view.month_status[0]
    assert status.under == 1
    assert status.on == 1
    assert status.over == 1
    assert status.negative == 0
    assert status.worst == "under"


def test_month_status_negative_wins() -> None:
    """A negative closing balance outranks every funding status."""
    view = build_view(_compute("overspend_carry.bean"), date(2024, 1, 1), 1)
    status = view.month_status[0]
    assert status.negative == 1
    assert status.worst == "negative"


def test_month_status_without_a_plan_is_none() -> None:
    """A month with no planned envelopes and no negative balance has no status."""
    view = build_view(_compute("basic.bean"), date(2024, 1, 1), 1)
    assert view.month_status[0].worst is None


def test_month_status_is_serialised() -> None:
    """``to_jsonable`` carries the per-month status counts and worst reading."""
    view = build_view(_compute("funding_colors.bean"), date(2024, 1, 1), 1)
    as_json = cast("list[dict[str, object]]", to_jsonable(view)["month_status"])
    assert as_json == [
        {"negative": 0, "under": 1, "over": 1, "on": 1, "worst": "under"}
    ]


def test_month_statuses_match_a_view() -> None:
    """Statuses for a view's own months agree with the view's month_status."""
    result = _compute("funding_colors.bean")
    view = build_view(result, date(2024, 1, 1), 3)
    assert month_statuses(result, view.months) == view.month_status


def test_month_statuses_reach_outside_the_window() -> None:
    """A month's status is the same whether or not it is in the window."""
    result = _compute("disney_2028.bean")
    months = (result.months[0], result.months[3], result.months[6])
    for month, status in zip(months, month_statuses(result, months), strict=True):
        assert status == build_view(result, month, 1).month_status[0]


def test_month_statuses_with_a_prepared_index_matches() -> None:
    """A prepared index gives the same statuses as building one each time."""
    result = _compute("funding_colors.bean")
    months = result.months
    index = prepare_view(result)
    assert month_statuses(result, months, index=index) == month_statuses(result, months)


def test_month_statuses_without_months_is_empty() -> None:
    """No months requested yields no statuses, without touching the budget."""
    result = _compute("basic.bean")
    assert month_statuses(result, ()) == ()


def test_build_reports_carries_month_status() -> None:
    """The reports carry a status per month, aligned with the month list."""
    result = _compute("funding_colors.bean")
    reports = build_reports(result)
    assert reports.months == result.months
    assert reports.month_status == month_statuses(result, result.months)


def test_build_reports_month_status_respects_the_range() -> None:
    """A narrowed range narrows the overview months and their statuses."""
    result = _compute("fund_backfill.bean")
    reports = build_reports(result, start=date(2024, 2, 1), end=date(2024, 2, 29))
    assert reports.months == (date(2024, 2, 1),)
    assert reports.month_status == month_statuses(result, reports.months)


def test_build_reports_without_a_start_has_no_months() -> None:
    """A ledger without a start has no overview months or statuses."""
    empty = BudgetResult(None, "USD", (), (), (), (), ())
    reports = build_reports(empty)
    assert reports.months == ()
    assert reports.month_status == ()


def test_build_reports_with_a_prepared_index_matches() -> None:
    """A prepared index gives the same reports as building one each time."""
    result = _compute("funding_colors.bean")
    index = prepare_view(result)
    assert build_reports(result, index=index) == build_reports(result)


def test_funding_explanation_for_monthly() -> None:
    """A monthly plan's explanation shows its level, funded amount and top-up."""
    result = _compute("funding_colors.bean")
    report = next(r for r in result.envelopes if r.name == "Under")
    explanation = funding_explanation(report, date(2024, 1, 31))
    assert explanation is not None
    assert explanation.reference == "monthly"
    assert explanation.monthly == D("100")
    assert explanation.target == D("100.00")
    assert explanation.funded == D("50")
    assert explanation.top_up == D("50.00")
    assert explanation.funding == "under"
    assert funding_explanation_lines(explanation) == (
        "monthly 100.00 = plan",
        "target 100.00 = monthly 100.00",
        "funded 50.00 = allocated 50.00",
        "under plan by 50.00",
        "top up 50.00",
    )


def test_funding_explanation_for_avg() -> None:
    """An avgN plan lists the completed months it averaged, oldest first."""
    result = _compute("topup_avg3.bean")
    report = next(r for r in result.envelopes if r.name == "Groceries")
    explanation = funding_explanation(report, date(2024, 4, 30))
    assert explanation is not None
    assert explanation.monthly == D("520.00")
    assert explanation.average_months == (
        (date(2024, 1, 1), D("500")),
        (date(2024, 2, 1), D("540")),
        (date(2024, 3, 1), D("520")),
    )
    assert funding_explanation_lines(explanation)[0] == (
        "monthly 520.00 = average of 2024-01 500.00, 2024-02 540.00, 2024-03 520.00"
    )


def test_funding_explanation_for_up_to() -> None:
    """An up-to plan shows the ceiling formula, the closing balance and the room."""
    result = _compute("up_to.bean")
    report = next(r for r in result.envelopes if r.name == "Car")
    explanation = funding_explanation(report, date(2024, 3, 31))
    assert explanation is not None
    assert explanation.reference == "up-to"
    assert explanation.target == D("600.00")
    assert explanation.funded == D("500")
    assert explanation.top_up == D("100.00")
    assert explanation.funding == "under"
    assert funding_explanation_lines(explanation) == (
        "target 600.00 = min(ceiling 600.00, opening 500.00 + monthly 250.00)",
        "funded 500.00 = closing balance",
        "under plan by 100.00",
        "top up 100.00",
    )


def test_funding_explanation_for_goal() -> None:
    """A goal plan shows its schedule, cumulative contributions and surplus."""
    result = _compute("savings_goal.bean")
    report = next(r for r in result.envelopes if r.name == "Car")
    explanation = funding_explanation(report, date(2024, 3, 31))
    assert explanation is not None
    assert explanation.reference == "goal"
    assert explanation.goal == D("15000")
    assert explanation.by == date(2028, 6, 1)
    assert (explanation.months_elapsed, explanation.months_total) == (3, 54)
    assert explanation.target == D("833.33")
    assert explanation.funded == D("1000")
    assert explanation.funding == "over"
    assert funding_explanation_lines(explanation) == (
        "target 833.33 = goal 15000.00 (3/54 months) to 2028-06-01",
        "funded 1000.00 = cumulative contributions",
        "over plan by 166.67",
    )


def test_funding_explanation_is_absent_without_a_plan() -> None:
    """An unplanned envelope carries no funding explanation."""
    view = build_view(_compute("basic.bean"), date(2024, 1, 1))
    assert all(
        cell.explanation is None
        for envelope in view.envelopes
        for cell in envelope.cells
    )


def test_explanation_is_serialised() -> None:
    """``to_jsonable`` carries each cell's funding explanation."""
    view = build_view(_compute("up_to.bean"), date(2024, 3, 1))
    car = next(e for e in view.envelopes if e.name == "Car")
    explanation = car.cells[0].explanation
    assert explanation is not None
    assert funding_explanation_lines(explanation)[0].startswith("target 600.00")
    as_json = cast("list[dict[str, object]]", to_jsonable(view)["envelopes"])
    car_json = next(entry for entry in as_json if entry["name"] == "Car")
    cells = cast("list[dict[str, object]]", car_json["cells"])
    cell_json = cast("dict[str, object]", cells[0]["explanation"])
    assert cell_json["reference"] == "up-to"
    assert cell_json["top_up"] == "100.00"


# --- Plan review ------------------------------------------------------------


def test_build_suggestions_reviews_spending_against_plan() -> None:
    """Every open, non-goal envelope is reviewed over the completed months."""
    suggestions = build_suggestions(_compute("plan_review.bean"))
    assert suggestions.months == (
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    )
    rows = {row.name: row for row in suggestions.rows}
    # The goal envelope is left to the goal `suggest` table.
    assert set(rows) == {"Car", "Fun", "Gifts", "Groceries", "Insurance", "Repairs"}
    groceries = rows["Groceries"]
    assert groceries.average == D("600.00")
    assert groceries.planned == D("500.00")
    assert groceries.plan_label == "500.00"
    assert (groceries.under, groceries.on, groceries.over) == (0, 0, 3)
    assert groceries.reading == "under-saving"


def test_build_suggestions_reads_up_to_envelopes_by_their_ceiling() -> None:
    """An up-to envelope is a sinking fund until it fills or overspends."""
    rows = {
        row.name: row for row in build_suggestions(_compute("plan_review.bean")).rows
    }
    assert rows["Gifts"].plan_label == "100.00 up to 1200.00"
    assert rows["Gifts"].reading == "sinking fund"
    assert rows["Insurance"].reading == "over-saving"
    assert rows["Repairs"].reading == "over allowance"
    assert rows["Car"].reading == "over-saving"
    assert rows["Fun"].planned is None
    assert rows["Fun"].plan_label == "-"
    assert rows["Fun"].reading == "no plan"


def test_build_suggestions_groups_by_parent_like_the_envelopes() -> None:
    """Rows group by parent, headings alphabetical, no-parent group last."""
    suggestions = build_suggestions(_compute("plan_review.bean"))
    assert [
        (group.parent, [row.name for row in group.rows]) for group in suggestions.groups
    ] == [
        ("Home", ["Groceries", "Insurance"]),
        ("Transport", ["Car", "Repairs"]),
        (None, ["Fun", "Gifts"]),
    ]


def test_build_suggestions_window_excludes_the_current_month() -> None:
    """The window ends the month before the review date, capped there."""
    result = _compute("plan_review.bean")
    # Reviewing in March leaves January and February completed.
    suggestions = build_suggestions(result, on=date(2024, 3, 1))
    assert suggestions.months == (date(2024, 1, 1), date(2024, 2, 1))
    # A future-dated directive cannot pull the window past the review date.
    capped = build_suggestions(result, on=date(2024, 2, 1))
    assert capped.months == (date(2024, 1, 1),)


def test_build_suggestions_summary_includes_goals_and_the_pool() -> None:
    """The affordability total adds goal contributions and measures the pool."""
    summary = build_suggestions(_compute("plan_review.bean")).summary
    assert summary is not None
    assert summary.months == 3
    # Groceries 500 + Insurance 100 + Car 200 + Repairs 100 + Gifts 100
    # + Vacation's goal spread (333.33).
    assert summary.planned_total == D("1333.33")
    assert summary.pool_average == D("2000.00")
    assert summary.surplus == D("666.67")


def test_build_suggestions_counts_overspent_months() -> None:
    """A month whose balance closed negative is counted as overspent."""
    suggestions = build_suggestions(_compute("overspend_carry.bean"))
    groceries = next(row for row in suggestions.rows if row.name == "Groceries")
    assert groceries.months == 2
    assert groceries.overspent == 1
    assert groceries.planned is None


def test_build_suggestions_without_start() -> None:
    """A ledger without a start has nothing to review."""
    empty = BudgetResult(None, "USD", (), (), (), (), ())
    assert build_suggestions(empty).has_start is False


def test_suggestion_cells_and_summary_line() -> None:
    """The shared formatters render the row and the affordability line."""
    line = suggestion_summary_line(SuggestionSummary(D(1000), D(800), 12))
    assert "Plans allocate 1000.00/month" in line
    assert "shortfall 200.00" in line
    suggestions = build_suggestions(_compute("plan_review.bean"))
    cells = {
        suggestion_cells(row)[0]: suggestion_cells(row) for row in suggestions.rows
    }
    assert cells["Groceries"] == (
        "Groceries",
        "600.00",
        "3",
        "500.00",
        "0/0/3",
        "0",
        "under-saving",
    )
