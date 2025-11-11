"""
strategy.py

Cleaned and hardened versions of:
- vwap
- make_trend_following
- make_stat_arb
- simulate_option_proxy

Key fixes applied:
- Explicit index alignment (reindex + ffill) where series from different symbols are combined.
- Safe rolling calculations with min_periods and denom checks to avoid divide-by-zero and spurious early values.
- Signals reindexed to the price series before simulation (fill False).
- simulate_option_proxy closes open positions at end-of-data (EOD) and documents entry/exit logic.
- Consistent use of positional access (iloc) while ensuring index alignment beforehand.
"""
from typing import Tuple
import pandas as pd
import numpy as np


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Cumulative VWAP over the DataFrame (expects 'Close' and 'Volume').
    Returns a Series indexed like df.
    """
    pv = (df['Close'] * df['Volume']).cumsum()
    vv = df['Volume'].cumsum()
    # Avoid div by zero; result will be NaN until vv > 0
    return pv / vv.replace({0: np.nan})


def make_trend_following(spy: pd.DataFrame, vix: pd.DataFrame = None) -> pd.DataFrame:
    """
    Trend-following signals for spy DataFrame (15m bars assumed).
    Produces VWAP, EMA9 and boolean long_sig / short_sig columns.
    If vix is provided, uses the VIX close slope as a filter (reindexed to spy.index).
    """
    df = spy.copy()
    df = df.sort_index()
    # Ensure necessary columns exist
    if 'Volume' not in df.columns:
        df['Volume'] = 0.0
    df['VWAP'] = vwap(df)
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()

    if vix is not None:
        vix15 = vix['Close'].reindex(df.index).ffill()
        df['VIX_slope'] = vix15.diff()
    else:
        df['VIX_slope'] = 0.0

    # Signals (use current bar comparison to indicators)
    df['long_sig'] = (df['Close'] > df['VWAP']) & (df['Close'] > df['EMA9']) & (df['VIX_slope'] <= 0)
    df['short_sig'] = (df['Close'] < df['VWAP']) & (df['Close'] < df['EMA9']) & (df['VIX_slope'] >= 0)

    # Ensure bool dtype and no NaNs
    df['long_sig'] = df['long_sig'].fillna(False).astype(bool)
    df['short_sig'] = df['short_sig'].fillna(False).astype(bool)
    return df


def make_stat_arb(spy: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    """
    Simple stat-arb signals based on the recent pct_change spread between spy and qqq.

    - Reindexes qqq to spy.index (ffill) before differencing so the spread aligns.
    - Uses rolling windows with min_periods to avoid misleading early values.
    - vwap30 uses rolling sums with a safe denominator.
    """
    spy = spy.sort_index()
    qqq = qqq.sort_index()

    df = pd.DataFrame(index=spy.index)
    # Avoid deprecated default fill behavior in pct_change
    r_spy = spy['Close'].pct_change(fill_method=None)
    r_qqq = qqq['Close'].pct_change(fill_method=None).reindex(spy.index).ffill()

    # short aggregation window for short-term spread behavior
    spread = (r_spy - r_qqq).rolling(window=8, min_periods=1).sum()

    # z-score computed on a longer window (with min_periods to allow earlier values)
    spread_mean = spread.rolling(window=40, min_periods=5).mean()
    spread_std = spread.rolling(window=40, min_periods=5).std().replace({0: np.nan})
    z = (spread - spread_mean) / (spread_std + 1e-12)

    # Rolling VWAP-like: sum(price * vol) / sum(vol) over 30 bars (safe denom)
    pv30 = (spy['Close'] * spy['Volume']).rolling(window=30, min_periods=1).sum()
    vol30 = spy['Volume'].rolling(window=30, min_periods=1).sum().replace({0: np.nan})
    vwap30 = pv30 / vol30

    df['long_sig'] = (z < -1.0) & (spy['Close'] > vwap30)
    df['short_sig'] = (z > 1.0) & (spy['Close'] < vwap30)

    df['long_sig'] = df['long_sig'].fillna(False).astype(bool)
    df['short_sig'] = df['short_sig'].fillna(False).astype(bool)
    return df


def simulate_option_proxy(
    price_series: pd.Series,
    long_sig: pd.Series,
    short_sig: pd.Series,
    tp: float = 0.20,
    sl: float = -0.10,
    k: float = 3.0
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Simulate a simple option-proxy PnL based on underlying moves.

    Rules:
    - An entry occurs at the next bar after a signal (i.e., if long_sig is True at t, entry is price at t+1).
    - Exit occurs on:
        * option-return >= tp  (take-profit)
        * option-return <= sl  (stop-loss)
        * opposite signal (flip) on the current bar (conservative)
        * end-of-data (EOD) - closes open position at last available price
    - Option return is approximated as:
        opt_ret = k * underlying_pct_move for calls; for puts we invert sign.
    - Inputs are reindexed/aligned to price_series.index inside the function.
    """
    closes = price_series.sort_index()
    # Align signals to closes index and ensure bool type
    long_sig = long_sig.reindex(closes.index).fillna(False).astype(bool)
    short_sig = short_sig.reindex(closes.index).fillna(False).astype(bool)

    entries = []
    pnls = []

    in_pos = 0  # +1 long call, -1 long put, 0 flat
    entry_px = None
    entry_time = None

    n = len(closes)
    if n == 0:
        return pd.DataFrame(columns=['time', 'reason', 'ret']), pd.DataFrame(columns=['time', 'action', 'price'])

    # iterate by integer positions to keep logic consistent
    for i in range(1, n):
        t = closes.index[i]
        prev_t = closes.index[i - 1]

        if in_pos == 0:
            # check for signal at previous bar -> enter at current bar price
            if long_sig.iloc[i - 1]:
                # If opposite signal present on the same bar as entry, treat as no-op
                # (do NOT record the entry). Instead, record an immediate flip pnl
                # so the event is observable.
                if short_sig.iloc[i]:
                    # compute immediate pnl (usually zero since entry==exit price)
                    entry_px = closes.iloc[i]
                    move = (closes.iloc[i] - entry_px) / entry_px if entry_px != 0 else 0.0
                    opt_ret = k * move
                    pnls.append((t, 'FLIP_IMMEDIATE', float(opt_ret)))
                    # do not set in_pos (remain flat), do not append an entry
                else:
                    in_pos = +1
                    entry_px = closes.iloc[i]
                    entry_time = t
                    entries.append((t, 'CALL_IN', float(entry_px)))
            elif short_sig.iloc[i - 1]:
                # If opposite signal present on the same bar as entry, treat as no-op
                if long_sig.iloc[i]:
                    entry_px = closes.iloc[i]
                    move = (closes.iloc[i] - entry_px) / entry_px if entry_px != 0 else 0.0
                    opt_ret = k * (-move)
                    pnls.append((t, 'FLIP_IMMEDIATE', float(opt_ret)))
                else:
                    in_pos = -1
                    entry_px = closes.iloc[i]
                    entry_time = t
                    entries.append((t, 'PUT_IN', float(entry_px)))
        else:
            # position is open; compute underlying pct move since entry
            if entry_px is None:
                # safety: shouldn't happen, but guard
                in_pos = 0
                entry_time = None
                continue

            move = (closes.iloc[i] - entry_px) / entry_px
            opt_ret = k * move if in_pos == +1 else k * (-move)

            exit_reason = None
            # TP / SL checks
            if opt_ret >= tp:
                exit_reason = 'TP'
            elif opt_ret <= sl:
                exit_reason = 'SL'
            # Flip on opposite signal at current bar (conservative)
            if in_pos == +1 and short_sig.iloc[i]:
                exit_reason = exit_reason or 'FLIP'
            if in_pos == -1 and long_sig.iloc[i]:
                exit_reason = exit_reason or 'FLIP'

            if exit_reason:
                pnls.append((t, exit_reason, float(opt_ret)))
                in_pos = 0
                entry_px = None
                entry_time = None

    # Close any remaining open position at EOD
    if in_pos != 0 and entry_px is not None:
        final_px = closes.iloc[-1]
        move = (final_px - entry_px) / entry_px
        opt_ret = k * move if in_pos == +1 else k * (-move)
        pnls.append((closes.index[-1], 'EOD', float(opt_ret)))

    results = pd.DataFrame(pnls, columns=['time', 'reason', 'ret'])
    entries_df = pd.DataFrame(entries, columns=['time', 'action', 'price'])
    return results, entries_df


# Optional: small example usage (comment or remove when importing as module)
if __name__ == "__main__":
    # This minimal runnable example builds dummy data for a few bars.
    times = pd.date_range("2025-01-02 09:30", periods=20, freq="15min")
    spy = pd.DataFrame({
        'Open':  np.linspace(430, 435, 20),
        'High':  np.linspace(431, 436, 20),
        'Low':   np.linspace(429, 434, 20),
        'Close': np.linspace(430, 435, 20),
        'Volume': np.random.randint(100, 1000, size=20)
    }, index=times)

    qqq = spy.copy()
    qqq['Close'] *= 0.98  # small offset

    tf = make_trend_following(spy)
    sa = make_stat_arb(spy, qqq)
    res, ent = simulate_option_proxy(spy['Close'], tf['long_sig'], tf['short_sig'])
    print("Entries:\n", ent)
    print("PnLs:\n", res)
