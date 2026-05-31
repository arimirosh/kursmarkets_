from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
import base64
import math

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import requests

from models import Candle


class InvestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CandleRequest:
    instrument_id: str
    days_back: int = 10
    interval: str = "1h"


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    target_ticker: str
    related_tickers: tuple[str, ...]
    days_back: int = 180
    interval: str = "1d"


_MOEX_INTERVALS = {
    "1m": 1,
    "10m": 10,
    "1h": 60,
    "1d": 24,
    "1w": 7,
}


def _interval_from_str(interval: str) -> int:
    if interval not in _MOEX_INTERVALS:
        raise InvestError(f"Unknown interval: {interval}. Examples: 1h, 1d, 10m")
    return _MOEX_INTERVALS[interval]


def fetch_candles(req: CandleRequest) -> list[Candle]:
    secid = req.instrument_id.upper()
    interval = _interval_from_str(req.interval)

    till = datetime.now().date()
    from_date = till - timedelta(days=req.days_back)

    url = (
        "https://iss.moex.com/iss/engines/stock/markets/shares/"
        f"boards/TQBR/securities/{secid}/candles.json"
    )

    params = {
        "from": from_date.isoformat(),
        "till": till.isoformat(),
        "interval": interval,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as e:
        raise InvestError(f"MOEX ISS API request failed: {e}") from e

    data = payload.get("candles", {})
    columns = data.get("columns", [])
    rows = data.get("data", [])

    if not rows:
        raise InvestError("No candles found. Check the ticker, date range, and TQBR board availability.")

    df = pd.DataFrame(rows, columns=columns)

    required = {"begin", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise InvestError(f"MOEX response is missing required columns: {', '.join(sorted(missing))}")

    candles: list[Candle] = []
    for row in df.itertuples(index=False):
        candles.append(
            Candle(
                time=pd.to_datetime(getattr(row, "begin")).to_pydatetime(),
                open=float(getattr(row, "open")),
                high=float(getattr(row, "high")),
                low=float(getattr(row, "low")),
                close=float(getattr(row, "close")),
                volume=int(getattr(row, "volume")),
            )
        )

    return candles


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.as_dict() for c in candles])
    df.set_index("time", inplace=True)
    return df


def fetch_close_series(ticker: str, days_back: int, interval: str = "1d") -> pd.Series:
    candles = fetch_candles(CandleRequest(ticker, days_back=days_back, interval=interval))
    df = candles_to_dataframe(candles)
    series = df["close"].astype(float).rename(ticker.upper())
    return series[~series.index.duplicated(keep="last")]


def build_cross_market_forecast(req: ForecastRequest) -> dict:
    target = req.target_ticker.upper()
    related = tuple(dict.fromkeys(t.upper() for t in req.related_tickers if t.upper() != target))
    tickers = (target, *related)

    if len(tickers) < 2:
        raise InvestError("Add at least one cross-market ticker for forecasting.")

    close_series = []
    skipped: list[str] = []
    for ticker in tickers:
        try:
            close_series.append(fetch_close_series(ticker, req.days_back, req.interval))
        except InvestError:
            if ticker == target:
                raise
            skipped.append(ticker)

    prices = pd.concat(close_series, axis=1).sort_index().ffill().dropna()
    if len(prices) < 25:
        raise InvestError("Not enough aligned observations for cross-market forecasting.")

    returns = prices.pct_change().dropna()
    y = returns[target].shift(-1).dropna()
    x = returns.loc[y.index].copy()

    feature_names = list(x.columns)
    x_values = x.to_numpy(dtype=float)
    y_values = y.to_numpy(dtype=float)

    x_mean = x_values.mean(axis=0)
    x_std = x_values.std(axis=0)
    x_std[x_std == 0] = 1.0
    y_mean = y_values.mean()
    y_std = y_values.std() or 1.0

    x_scaled = (x_values - x_mean) / x_std
    y_scaled = (y_values - y_mean) / y_std
    design = np.column_stack([np.ones(len(x_scaled)), x_scaled])

    penalty = 0.08
    identity = np.eye(design.shape[1])
    identity[0, 0] = 0
    beta = np.linalg.solve(design.T @ design + penalty * identity, design.T @ y_scaled)

    fitted = design @ beta
    residuals = y_scaled - fitted
    latest_features = returns[feature_names].iloc[-1].to_numpy(dtype=float)
    latest_scaled = (latest_features - x_mean) / x_std
    predicted_scaled = float(np.r_[1.0, latest_scaled] @ beta)
    predicted_return = float(predicted_scaled * y_std + y_mean)

    last_close = float(prices[target].iloc[-1])
    predicted_close = last_close * (1 + predicted_return)
    price_delta = predicted_close - last_close

    target_volatility = float(returns[target].tail(min(20, len(returns))).std())
    model_noise = float(np.std(residuals) * y_std)
    pseudo_volatility = max(target_volatility, 0.0) + max(model_noise, 0.0)
    confidence = max(5.0, min(95.0, 100.0 / (1.0 + pseudo_volatility * 140.0)))

    correlations = returns[feature_names].corrwith(y).fillna(0.0)
    raw_contributions = beta[1:] * latest_scaled
    contribution_rows = []
    for name, contribution, correlation in zip(feature_names, raw_contributions, correlations):
        contribution_rows.append(
            {
                "ticker": name,
                "latest_return": f"{latest_features[feature_names.index(name)] * 100:+.2f}%",
                "correlation": f"{float(correlation):+.2f}",
                "impact": f"{float(contribution * y_std * 100):+.3f}%",
            }
        )

    contribution_rows.sort(key=lambda row: abs(float(row["impact"].replace("%", ""))), reverse=True)

    direction = "Upward movement" if predicted_return >= 0 else "Downward movement"
    direction_class = "positive" if predicted_return >= 0 else "negative"
    forecast_index = prices.index[-1] + (prices.index[-1] - prices.index[-2])
    forecast_chart = plot_forecast_base64(prices[target], forecast_index, predicted_close)

    return {
        "target": target,
        "related": ", ".join(related),
        "skipped": ", ".join(skipped),
        "last_close": _format_number(last_close),
        "predicted_close": _format_number(predicted_close),
        "price_delta": f"{price_delta:+.2f}",
        "predicted_return": f"{predicted_return * 100:+.2f}%",
        "direction": direction,
        "direction_class": direction_class,
        "pseudo_volatility": f"{pseudo_volatility * 100:.2f}%",
        "confidence": f"{confidence:.1f}%",
        "observations": len(prices),
        "features": len(feature_names),
        "chart_uri": forecast_chart,
        "contributions": contribution_rows[:8],
    }


def _format_number(value: float, digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:,.{digits}f}"


def _format_volume(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.0f}"


def build_market_summary(df: pd.DataFrame) -> dict:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    returns = close.pct_change().dropna()

    first_close = float(close.iloc[0])
    last_close = float(close.iloc[-1])
    change_abs = last_close - first_close
    period_return = (last_close / first_close - 1) * 100 if first_close else 0.0
    volatility = float(returns.std() * 100) if not returns.empty else 0.0
    avg_volume = float(volume.mean())
    range_pct = ((float(high.max()) - float(low.min())) / first_close) * 100 if first_close else 0.0

    short_ma = float(close.tail(min(5, len(close))).mean())
    long_ma = float(close.tail(min(10, len(close))).mean())
    signal = "Bullish momentum" if short_ma >= long_ma else "Bearish pressure"
    signal_class = "positive" if short_ma >= long_ma else "negative"

    return {
        "last_close": _format_number(last_close),
        "change_abs": _format_number(change_abs),
        "period_return": f"{period_return:+.2f}%",
        "period_return_class": "positive" if period_return >= 0 else "negative",
        "high": _format_number(float(high.max())),
        "low": _format_number(float(low.min())),
        "range_pct": f"{range_pct:.2f}%",
        "avg_volume": _format_volume(avg_volume),
        "volatility": f"{volatility:.2f}%",
        "signal": signal,
        "signal_class": signal_class,
        "short_ma": _format_number(short_ma),
        "long_ma": _format_number(long_ma),
    }


def plot_candles_base64(df: pd.DataFrame) -> str:
    buf = BytesIO()
    market_colors = mpf.make_marketcolors(
        up="#1f8a70",
        down="#c0392b",
        edge="inherit",
        wick="inherit",
        volume="inherit",
    )
    style = mpf.make_mpf_style(
        base_mpf_style="yahoo",
        marketcolors=market_colors,
        gridstyle=":",
        facecolor="#ffffff",
        figcolor="#ffffff",
    )
    mpf.plot(
        df,
        type="candle",
        volume=True,
        style=style,
        mav=(5, 10) if len(df) >= 10 else None,
        tight_layout=True,
        savefig=dict(fname=buf, dpi=150, bbox_inches="tight"),
    )
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def plot_forecast_base64(close: pd.Series, forecast_index, predicted_close: float) -> str:
    fig, ax = plt.subplots(figsize=(10.5, 4.8), dpi=150)
    history = close.tail(90)
    ax.plot(history.index, history.values, color="#1565c0", linewidth=2.2, label="Historical close")
    ax.scatter([forecast_index], [predicted_close], color="#b42318", s=70, zorder=3, label="Forecast")
    ax.plot(
        [history.index[-1], forecast_index],
        [history.iloc[-1], predicted_close],
        color="#b42318",
        linewidth=1.8,
        linestyle="--",
    )
    ax.set_title("CSPO-inspired next-step close forecast", fontsize=13, fontweight="bold")
    ax.set_ylabel("Close price")
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def sdk_name() -> str:
    return "MOEX ISS API"
