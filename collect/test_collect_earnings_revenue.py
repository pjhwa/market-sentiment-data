"""P0-6: revenue_raw_to_billions_usd unit/currency tests.

Run:
  cd /Users/jerry/dev/market-sentiment-data && python3 -m pytest collect/test_collect_earnings_revenue.py -q
"""

from __future__ import annotations

import collect.collect_earnings as ce


def test_tsm_twd_absolute_converts_to_plausible_usd_billions(monkeypatch):
    """Live yfinance TSM Revenue Average ~1.26e12 is TWD, not USD.

    Blind /1e9 → 1263.57 (BUG). With TWD≈32 → ~$39.5B (OK for TSMC quarterly).
    """
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 32.0 if c == "TWD" else 1.0)
    raw = 1_263_569_250_410  # sample from yfinance calendar 2026-07-13
    out = ce.revenue_raw_to_billions_usd(raw, "TWD")
    assert out is not None
    assert 30.0 <= out <= 50.0, f"expected ~39.5B, got {out}"
    # Must NOT be the old bug value
    assert out < 100


def test_tsla_usd_absolute_billions(monkeypatch):
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 1.0)
    raw = 25_829_856_380  # ~$25.83B
    out = ce.revenue_raw_to_billions_usd(raw, "USD")
    assert out == 25.83


def test_googl_usd_absolute(monkeypatch):
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 1.0)
    raw = 116_841_971_700
    out = ce.revenue_raw_to_billions_usd(raw, "USD")
    assert out == 116.84


def test_already_billions_usd_passthrough(monkeypatch):
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 1.0)
    assert ce.revenue_raw_to_billions_usd(25.83, "USD") == 25.83
    assert ce.revenue_raw_to_billions_usd(1.81, "USD") == 1.81


def test_absurd_after_convert_dropped(monkeypatch):
    """If conversion still yields > max, drop rather than pollute AI/UI."""
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 1.0)
    # 1e15 USD absolute → 1e6 B — invalid
    assert ce.revenue_raw_to_billions_usd(1e15, "USD") is None


def test_none_and_nan():
    assert ce.revenue_raw_to_billions_usd(None, "USD") is None
    assert ce.revenue_raw_to_billions_usd(float("nan"), "USD") is None


def test_old_bug_value_as_local_billions_twd_converts(monkeypatch):
    """If a pipeline already stored 1263.57 meaning 'B TWD', recover via FX."""
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 32.0 if c == "TWD" else 1.0)
    out = ce.revenue_raw_to_billions_usd(1263.57, "TWD")
    assert out is not None
    assert 30.0 <= out <= 50.0


def test_blind_divide_bug_regression(monkeypatch):
    """Document the pre-fix behavior numerically — must not return 1263.57 for TSM."""
    monkeypatch.setattr(ce, "local_currency_units_per_usd", lambda c: 32.0)
    raw = 1_263_569_250_410
    buggy = round(raw / 1e9, 2)
    assert buggy == 1263.57
    fixed = ce.revenue_raw_to_billions_usd(raw, "TWD")
    assert fixed != buggy
    assert fixed is not None and fixed < 100
