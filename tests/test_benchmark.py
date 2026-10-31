"""The benchmark runs end to end over a generated sample.

This is a smoke test, not a timing gate: it checks the benchmark exercises every
phase over the sample generator and returns sane numbers, so the development aid
cannot rot. Wall-clock values are not asserted, which would be flaky on shared
CI.
"""

from __future__ import annotations

import benchmark


def test_benchmark_measures_every_phase() -> None:
    """The benchmark times each phase over a small sample ledger."""
    result = benchmark.measure(months=2, per_month=2, repeat=1)
    labels = [label for label, _ in result.rows]
    assert "parse (no booking/plugins)" in labels
    assert "load (with plugin)" in labels
    assert "compute (validation only)" in labels
    assert "compute (activity + hashes)" in labels
    assert all(milliseconds >= 0 for _, milliseconds in result.rows)
    assert result.months == 2
    assert result.transactions > 0
