from __future__ import annotations

from flask import Flask, render_template, request

from services import (
    CandleRequest,
    ForecastRequest,
    InvestError,
    build_market_summary,
    build_cross_market_forecast,
    candles_to_dataframe,
    fetch_candles,
    plot_candles_base64,
    sdk_name,
)


app = Flask(__name__)


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_instrument_id="SBER",
        default_days=30,
        default_interval="1d",
        sdk=sdk_name(),
    )


@app.post("/run")
def run():
    instrument_id = (request.form.get("instrument_id") or "").strip().upper()
    days_back = int(request.form.get("days_back") or "10")
    interval = (request.form.get("interval") or "1h").strip()

    try:
        candles = fetch_candles(
            CandleRequest(
                instrument_id=instrument_id,
                days_back=days_back,
                interval=interval,
            )
        )
        df = candles_to_dataframe(candles)
        chart_uri = plot_candles_base64(df)
        summary = build_market_summary(df)
        table_html = df.tail(20).to_html(classes="table", border=0)

        return render_template(
            "result.html",
            instrument_id=instrument_id,
            days_back=days_back,
            interval=interval,
            chart_uri=chart_uri,
            summary=summary,
            table_html=table_html,
            sdk=sdk_name(),
            n=len(df),
        )
    except InvestError as e:
        return render_template("error.html", message=str(e)), 400


@app.get("/forecast")
def forecast():
    return render_template(
        "forecast.html",
        default_target="SBER",
        default_related="GAZP,LKOH,ROSN,VTBR,YDEX,AFLT",
        default_days=180,
        default_interval="1d",
        sdk=sdk_name(),
    )


@app.post("/forecast/run")
def forecast_run():
    target_ticker = (request.form.get("target_ticker") or "").strip().upper()
    related_raw = request.form.get("related_tickers") or ""
    related_tickers = tuple(
        ticker.strip().upper()
        for ticker in related_raw.replace(";", ",").split(",")
        if ticker.strip()
    )
    days_back = int(request.form.get("days_back") or "180")
    interval = (request.form.get("interval") or "1d").strip()

    try:
        forecast_data = build_cross_market_forecast(
            ForecastRequest(
                target_ticker=target_ticker,
                related_tickers=related_tickers,
                days_back=days_back,
                interval=interval,
            )
        )

        return render_template(
            "forecast_result.html",
            forecast=forecast_data,
            days_back=days_back,
            interval=interval,
            sdk=sdk_name(),
        )
    except InvestError as e:
        return render_template("error.html", message=str(e)), 400


if __name__ == "__main__":
    app.run(debug=True)
