from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
import base64
from typing import Iterable, Optional

import pandas as pd
import mplfinance as mpf

from models import Candle


class InvestError(RuntimeError):
    pass


# --- Импорты SDK (поддерживаем и новый t-tech-investments, и старый tinkoff-investments) ---
try:
    # Новый SDK (как в документации T-Bank)
    from t_tech.invest import Client, CandleInterval  # type: ignore
    from t_tech.invest.utils import now  # type: ignore
    _SDK_NAME = "t-tech-investments"
except Exception:  # pragma: no cover
    try:
        # Старый SDK (PyPI)
        from tinkoff.invest import Client, CandleInterval  # type: ignore
        from tinkoff.invest.utils import now  # type: ignore
        _SDK_NAME = "tinkoff-investments"
    except Exception as e:  # pragma: no cover
        Client = None  # type: ignore
        CandleInterval = None  # type: ignore
        now = None  # type: ignore
        _SDK_NAME = "not-installed"


@dataclass(frozen=True, slots=True)
class CandleRequest:
    instrument_id: str
    days_back: int = 10
    interval: str = "4h"  # '1m', '5m', '15m', '1h', '4h', '1d'


def _quotation_to_float(q) -> float:
    """В SDK цены обычно приходят как Quotation(units, nano)."""
    # Часто у объекта есть units/nano (protobuf)
    units = getattr(q, "units", 0)
    nano = getattr(q, "nano", 0)
    try:
        return float(units) + float(nano) / 1_000_000_000.0
    except Exception:
        # На всякий случай
        return float(units)


def _interval_from_str(interval: str):
    if CandleInterval is None:
        raise InvestError("SDK не установлен")

    m = {
        "1m": CandleInterval.CANDLE_INTERVAL_1_MIN,
        "5m": CandleInterval.CANDLE_INTERVAL_5_MIN,
        "15m": CandleInterval.CANDLE_INTERVAL_15_MIN,
        "1h": CandleInterval.CANDLE_INTERVAL_HOUR,
        "4h": CandleInterval.CANDLE_INTERVAL_4_HOUR,
        "1d": CandleInterval.CANDLE_INTERVAL_DAY,
    }
    if interval not in m:
        raise InvestError(f"Неизвестный интервал: {interval}. Пример: 4h, 1h, 15m")
    return m[interval]


def fetch_candles(token: str, req: CandleRequest) -> list[Candle]:
    """Получаем свечи и возвращаем список dataclass Candle (Model)."""
    if Client is None or now is None:
        raise InvestError(
            "SDK T-Invest не установлен.\n"
            "Установите: pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple"
        )

    interval_enum = _interval_from_str(req.interval)
    from_dt = now() - timedelta(days=req.days_back)

    candles: list[Candle] = []

    try:
        with Client(token) as client:
            # В разных версиях параметр может называться instrument_id или figi
            try:
                it = client.get_all_candles(
                    instrument_id=req.instrument_id,
                    interval=interval_enum,
                    from_=from_dt,
                )
            except TypeError:
                it = client.get_all_candles(
                    figi=req.instrument_id,
                    interval=interval_enum,
                    from_=from_dt,
                )

            for c in it:
                candles.append(
                    Candle(
                        time=c.time,
                        open=_quotation_to_float(c.open),
                        high=_quotation_to_float(c.high),
                        low=_quotation_to_float(c.low),
                        close=_quotation_to_float(c.close),
                        volume=int(getattr(c, "volume", 0)),
                    )
                )
    except Exception as e:
        raise InvestError(str(e)) from e

    if not candles:
        raise InvestError("Свечи не найдены (проверьте instrument_id/FIGI и период)")

    return candles


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.as_dict() for c in candles])
    df.set_index("time", inplace=True)
    return df


def plot_candles_base64(df: pd.DataFrame) -> str:
    """Строим свечной график и возвращаем PNG как base64 строку."""
    buf = BytesIO()
    mpf.plot(df, type="candle", volume=True, style="charles", savefig=buf)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def sdk_name() -> str:
    return _SDK_NAME
