from __future__ import annotations

from flask import Flask, render_template, request

from services import CandleRequest, InvestError, fetch_candles, candles_to_dataframe, plot_candles_base64, sdk_name

# token в отдельном файле secrets.py (он в .gitignore)
try:
    from app_secrets import TINKOFF_TOKEN
except Exception:
    TINKOFF_TOKEN = ""


app = Flask(__name__)


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_instrument_id="BBG004730N88",  # SBER FIGI из примера
        default_days=10,
        default_interval="4h",
        sdk=sdk_name(),
    )


@app.post("/run")
def run():
    instrument_id = (request.form.get("instrument_id") or "").strip()
    days_back = int(request.form.get("days_back") or "10")
    interval = (request.form.get("interval") or "4h").strip()

    if not TINKOFF_TOKEN:
        return render_template(
            "error.html",
            message="Не найден токен. Создайте файл app_secrets.py рядом с app.py и задайте TINKOFF_TOKEN.",
        ), 500

    try:
        candles = fetch_candles(
            TINKOFF_TOKEN,
            CandleRequest(instrument_id=instrument_id, days_back=days_back, interval=interval),
        )
        df = candles_to_dataframe(candles)
        chart_uri = plot_candles_base64(df)
        # покажем первые строки таблицы, чтобы не перегружать страницу
        table_html = df.tail(30).to_html(classes="table", border=0)
        return render_template(
            "result.html",
            instrument_id=instrument_id,
            days_back=days_back,
            interval=interval,
            chart_uri=chart_uri,
            table_html=table_html,
            sdk=sdk_name(),
            n=len(df),
        )
    except InvestError as e:
        return render_template("error.html", message=str(e)), 400


if __name__ == "__main__":
    app.run(debug=True)
