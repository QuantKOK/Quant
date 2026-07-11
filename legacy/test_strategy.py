import pandas as pd
import numpy as np
import pytest

from strategy import vwap, simulate_option_proxy, make_trend_following, make_stat_arb


def test_vwap_simple():
    idx = pd.date_range("2025-01-01", periods=2, freq="D")
    df = pd.DataFrame({'Close': [100.0, 110.0], 'Volume': [10.0, 10.0]}, index=idx)
    v = vwap(df)
    # cumulative pv = [1000, 2100] ; cumulative vol = [10,20] -> vwap[1]=105.0
    assert pytest.approx(v.iloc[1], rel=1e-12) == 105.0


def test_simulate_option_proxy_basic():
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    prices = pd.Series([100.0, 110.0, 120.0, 130.0], index=idx)
    # make a single long signal at first bar -> entry at second bar
    long_sig = pd.Series([True, False, False, False], index=idx)
    short_sig = pd.Series([False] * 4, index=idx)

    pnls, entries = simulate_option_proxy(prices, long_sig, short_sig, tp=1000.0, sl=-1000.0, k=1.0)
    # With huge TP/SL we should at least see an entry recorded
    assert not entries.empty
    assert entries.iloc[0]['action'] == 'CALL_IN'


def test_make_trend_following_output():
    idx = pd.date_range("2025-01-01 09:30", periods=5, freq="15min")
    spy = pd.DataFrame({
        'Open': np.linspace(100, 104, 5),
        'High': np.linspace(101, 105, 5),
        'Low': np.linspace(99, 103, 5),
        'Close': np.linspace(100, 104, 5),
        'Volume': np.arange(1, 6)
    }, index=idx)

    df = make_trend_following(spy)
    # Columns should exist and be boolean
    assert 'VWAP' in df.columns
    assert 'EMA9' in df.columns
    assert df['long_sig'].dtype == bool
    assert df['short_sig'].dtype == bool


def test_simulate_option_proxy_immediate_flip():
    # If a long signal occurs at t-1 (enter at t) and an opposite short signal
    # is present at t (same bar), we expect an immediate FLIP_IMMEDIATE recorded.
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.Series([100.0, 100.0, 100.0], index=idx)
    long_sig = pd.Series([True, False, False], index=idx)
    # Opposite signal present at entry bar (index 1)
    short_sig = pd.Series([False, True, False], index=idx)

    pnls, entries = simulate_option_proxy(prices, long_sig, short_sig, tp=1000.0, sl=-1000.0, k=1.0)
    # With immediate flip semantics, the *entry at that bar* should NOT be
    # recorded; a later valid entry (next bar) may occur. Ensure there's no
    # CALL_IN at the immediate-flip timestamp, and FLIP_IMMEDIATE was recorded.
    assert not ((entries['time'] == idx[1]) & (entries['action'] == 'CALL_IN')).any()
    assert any(r == 'FLIP_IMMEDIATE' for r in pnls['reason'])


def test_simulate_option_proxy_eod_close():
    # Ensure an open position is closed at EOD
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    prices = pd.Series([100.0, 101.0, 102.0], index=idx)
    long_sig = pd.Series([True, False, False], index=idx)
    short_sig = pd.Series([False, False, False], index=idx)

    pnls, entries = simulate_option_proxy(prices, long_sig, short_sig, tp=1000.0, sl=-1000.0, k=1.0)
    # Since TP/SL are wide, we expect EOD close recorded
    assert any(r == 'EOD' for r in pnls['reason'])


def test_vwap_zero_volume():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    df = pd.DataFrame({'Close': [100.0, 105.0, 110.0], 'Volume': [0.0, 0.0, 0.0]}, index=idx)
    v = vwap(df)
    # All volumes zero -> vwap is NaN
    assert v.isna().all()


def test_make_stat_arb_empty_and_nans():
    # empty input
    empty_idx = pd.DatetimeIndex([])
    spy_empty = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'], index=empty_idx)
    qqq_empty = spy_empty.copy()
    df_empty = make_stat_arb(spy_empty, qqq_empty)
    assert isinstance(df_empty, pd.DataFrame)
    assert df_empty.empty

    # inputs with NaNs
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    spy = pd.DataFrame({'Close': [100.0, np.nan, 102.0, 103.0], 'Volume': [10.0, 0.0, np.nan, 5.0]}, index=idx)
    qqq = pd.DataFrame({'Close': [99.0, 100.0, np.nan, 101.0], 'Volume': [5.0, 5.0, 5.0, 5.0]}, index=idx)
    df = make_stat_arb(spy, qqq)
    # Should return a DataFrame with the same index (or subset) and boolean columns
    assert isinstance(df, pd.DataFrame)
    assert 'long_sig' in df.columns and 'short_sig' in df.columns
