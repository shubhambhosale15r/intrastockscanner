from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

from fyers_apiv3 import fyersModel

# ------------------------------ Configuration ------------------------------
st.set_page_config(page_title="FYERS Max Pain Dashboard", page_icon="📊", layout="wide")
DEFAULT_SYMBOL = "NSE:NIFTY50-INDEX"
try:
    IST = ZoneInfo("Asia/Kolkata") if ZoneInfo else timezone(timedelta(hours=5, minutes=30))
except Exception:  # Windows may need the tzdata package for ZoneInfo
    IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------- FYERS client -------------------------------
def fyers_client(client_id: str, token: str):
    return fyersModel.FyersModel(
        client_id=client_id, token=token, is_async=False, log_path=""
    )


def read_response(response: Any, label: str) -> Dict[str, Any]:
    if not isinstance(response, dict):
        raise RuntimeError(f"{label}: unexpected FYERS response: {response!r}")
    status = str(response.get("s", "")).lower()
    if status == "error":
        raise RuntimeError(f"{label}: {response.get('message') or response}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{label}: response does not contain a data object: {response}")
    return data


@st.cache_data(ttl=300, show_spinner=False)
def get_expiries(client_id: str, token: str, symbol: str) -> List[Dict[str, str]]:
    """Expiry metadata is cached for five minutes to avoid an extra API call on every rerun."""
    response = fyers_client(client_id, token).optionchain(data={
        "symbol": symbol, "strikecount": 1, "timestamp": "", "greeks": "1"
    })
    data = read_response(response, "Expiry lookup")
    result = []
    for item in data.get("expiryData", []):
        if isinstance(item, dict) and item.get("expiry") is not None and item.get("date"):
            result.append({
                "date": str(item["date"]),
                "expiry": str(item["expiry"]),
                "flag": str(item.get("expiry_flag", "")),
            })
    if not result:
        raise RuntimeError("No expiryData entries were returned for this symbol.")
    return result


def get_chain(client_id: str, token: str, symbol: str,
              strike_count: int, expiry_timestamp: str) -> Dict[str, Any]:
    response = fyers_client(client_id, token).optionchain(data={
        "symbol": symbol,
        "strikecount": int(strike_count),
        "timestamp": str(expiry_timestamp),
        "greeks": "1",
    })
    return read_response(response, "Option-chain request")


# ------------------------------ Data processing ----------------------------
def num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def get_underlying(chain: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in chain:
        if not isinstance(item, dict):
            continue
        if not item.get("option_type") or num(item.get("strike_price"), -999) == -1:
            return item
    return {}


def make_strike_table(chain: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, List[str]]:
    contracts: Dict[Tuple[float, str], Dict[str, Any]] = {}
    for item in chain:
        if not isinstance(item, dict):
            continue
        side = str(item.get("option_type", "")).upper()
        strike = num(item.get("strike_price"), -1)
        if side in ("CE", "PE") and strike >= 0:
            contracts[(strike, side)] = item

    strikes = sorted({strike for strike, _ in contracts})
    if not strikes:
        raise RuntimeError("No CE/PE contracts found in optionsChain.")

    rows, missing = [], 0
    for strike in strikes:
        ce, pe = contracts.get((strike, "CE"), {}), contracts.get((strike, "PE"), {})
        missing += int(not bool(ce)) + int(not bool(pe))
        cg = ce.get("greeks") if isinstance(ce.get("greeks"), dict) else {}
        pg = pe.get("greeks") if isinstance(pe.get("greeks"), dict) else {}
        rows.append({
            "Strike": strike,
            "CE LTP": num(ce.get("ltp")), "CE OI": num(ce.get("oi")),
            "CE OI Chg": num(ce.get("oich")), "CE OI Chg %": num(ce.get("oichp")),
            "CE Volume": num(ce.get("volume")), "CE Delta": num(cg.get("delta")),
            "CE Gamma": num(cg.get("gamma")), "CE IV": num(cg.get("iv")),
            "PE IV": num(pg.get("iv")), "PE Gamma": num(pg.get("gamma")),
            "PE Delta": num(pg.get("delta")), "PE Volume": num(pe.get("volume")),
            "PE OI Chg %": num(pe.get("oichp")), "PE OI Chg": num(pe.get("oich")),
            "PE OI": num(pe.get("oi")), "PE LTP": num(pe.get("ltp")),
        })

    warnings = []
    if missing:
        warnings.append(
            f"{missing} CE/PE leg(s) were missing. Missing legs are treated as zero OI; "
            "this can distort the payout calculation."
        )
    return pd.DataFrame(rows), warnings


def payout_curves(df: pd.DataFrame, oi_multiplier: float) -> pd.DataFrame:
    """Gross intrinsic payout at every returned strike, using all returned CE/PE OI."""
    k = df["Strike"].to_numpy(dtype=float)
    ce_oi = df["CE OI"].to_numpy(dtype=float) * oi_multiplier
    pe_oi = df["PE OI"].to_numpy(dtype=float) * oi_multiplier
    rows = []
    for settlement in k:
        ce = float(np.sum(ce_oi * np.maximum(settlement - k, 0)))
        pe = float(np.sum(pe_oi * np.maximum(k - settlement, 0)))
        rows.append({"Settlement": settlement, "CE Payout": ce,
                     "PE Payout": pe, "Total Payout": ce + pe})
    return pd.DataFrame(rows)


def max_pain_level(curves: pd.DataFrame, spot: float):
    minimum = float(curves["Total Payout"].min())
    tied = curves.loc[
        np.isclose(curves["Total Payout"], minimum, rtol=1e-10, atol=1e-6),
        "Settlement"
    ].astype(float).tolist()
    # In a tie, headline the tied strike closest to spot and disclose the full tie set.
    selected = min(tied, key=lambda x: abs(x - spot)) if spot > 0 else tied[0]
    row = curves.loc[curves["Settlement"] == selected].iloc[0]
    return selected, tied, minimum, row


def secret_value(key: str) -> str:
    value = os.getenv(key, "")
    try:
        if not value:
            value = str(st.secrets.get(key, ""))
    except Exception:
        pass
    return value.strip()


# ----------------------------------- UI --------------------------------------
st.title("NIFTY Options — Max Pain & Payout Dashboard")
st.caption("Read-only FYERS API v3 analytics. This app does not place, modify or cancel orders.")

with st.sidebar:
    st.header("FYERS connection")
    client_id = st.text_input("Client ID", value=secret_value("FYERS_CLIENT_ID"),
                              placeholder="YOUR_CLIENT_ID-100").strip()
    access_token = st.text_input("Access token", value=secret_value("FYERS_ACCESS_TOKEN"),
                                 type="password", placeholder="Current FYERS access token").strip()
    st.divider()
    st.header("Option-chain settings")
    symbol = st.text_input("Underlying symbol", value=DEFAULT_SYMBOL).strip().upper()
    strike_count = st.slider("Strikes on each side of ATM", 1, 50, 50,
                             help="50 requests up to 50 ITM + ATM + 50 OTM strikes.")
    oi_multiplier = st.number_input(
        "OI quantity multiplier", min_value=0.000001, max_value=1000000.0,
        value=1.0, step=1.0,
        help=("Keep 1 if the API OI is already quantity-based. If your returned OI is in lots, "
              "enter the relevant lot size. This changes payout amounts, not the Max Pain strike.")
    )
    st.divider()
    auto_refresh = st.checkbox("Auto-refresh", value=False)
    refresh_seconds = st.select_slider("Refresh interval", options=[15, 30, 45, 60, 90, 120, 180, 300],
                                       value=30, format_func=lambda x: f"{x} seconds",
                                       disabled=not auto_refresh)
    if st.button("Clear expiry cache", use_container_width=True):
        get_expiries.clear()
        st.rerun()

if not client_id or not access_token:
    st.info("Enter your FYERS Client ID and current access token in the sidebar.")
    st.stop()
if not symbol:
    st.error("Underlying symbol cannot be blank.")
    st.stop()

if auto_refresh:
    if st_autorefresh is None:
        st.warning("Install streamlit-autorefresh to enable auto-refresh: pip install streamlit-autorefresh")
    else:
        st_autorefresh(interval=int(refresh_seconds * 1000), key="fyers_chain_refresh")

try:
    expiries = get_expiries(client_id, access_token, symbol)
except Exception as exc:
    st.error(f"Could not load expiry dates: {exc}")
    st.stop()

expiry_labels = [
    f"{e['date']}" + (f" | {e['flag']}" if e["flag"] else "") + f" [{e['expiry']}]"
    for e in expiries
]
expiry_by_label = {label: item["expiry"] for label, item in zip(expiry_labels, expiries)}

with st.sidebar:
    selected_expiry = st.selectbox("Expiry", expiry_labels, index=0)
    expiry_timestamp = expiry_by_label[selected_expiry]
    st.button("Fetch / refresh option chain", type="primary", use_container_width=True)

try:
    with st.spinner(f"Fetching {symbol} option chain..."):
        payload = get_chain(client_id, access_token, symbol, strike_count, expiry_timestamp)
except Exception as exc:
    st.error(f"Option-chain request failed: {exc}")
    st.stop()

chain = payload.get("optionsChain", [])
if not isinstance(chain, list) or not chain:
    st.error("FYERS returned an empty optionsChain.")
    st.stop()

try:
    strike_df, warnings = make_strike_table(chain)
except Exception as exc:
    st.error(f"Could not process option-chain data: {exc}")
    st.stop()

underlying = get_underlying(chain)
spot = num(underlying.get("ltp"))
future = num(underlying.get("fp"))
vix_data = payload.get("indiavixData", {})
vix = num(vix_data.get("ltp")) if isinstance(vix_data, dict) else 0.0
if spot <= 0:
    spot = float(strike_df["Strike"].iloc[(strike_df["Strike"] - strike_df["Strike"].median()).abs().argmin()])
    st.warning("Underlying LTP missing; the spot reference is approximated from the returned strikes.")

curves = payout_curves(strike_df, oi_multiplier)
max_pain, ties, min_payout, mp_row = max_pain_level(curves, spot)
ce_at_mp, pe_at_mp = float(mp_row["CE Payout"]), float(mp_row["PE Payout"])
lo, hi = float(strike_df["Strike"].min()), float(strike_df["Strike"].max())

st.caption(
    f"{symbol}  |  Expiry: {selected_expiry.split(' [')[0]}  |  "
    f"Fetched: {datetime.now(IST).strftime('%d-%b-%Y %H:%M:%S IST')}  |  "
    f"Returned strikes: {len(strike_df)} ({lo:,.0f}–{hi:,.0f})"
)

m = st.columns(5)
m[0].metric("Spot LTP", f"{spot:,.2f}")
m[1].metric("Future (fp)", f"{future:,.2f}" if future else "—")
m[2].metric("India VIX", f"{vix:.2f}" if vix else "—")
atm = float(strike_df.iloc[(strike_df["Strike"] - spot).abs().argmin()]["Strike"])
m[3].metric("ATM strike", f"{atm:,.0f}")
m[4].metric("Max Pain", f"{max_pain:,.0f}", delta=f"{max_pain - spot:+,.0f} vs spot")

for message in warnings:
    st.warning(message)
if np.isclose(max_pain, lo) or np.isclose(max_pain, hi):
    st.warning(
        f"Max Pain is at the edge of the fetched range ({lo:,.0f}–{hi:,.0f}). "
        "The true minimum may be outside this range. The API allows a maximum strikecount of 50."
    )
if len(ties) > 1:
    st.info("Minimum payout is tied at: " + ", ".join(f"{x:,.0f}" for x in ties) +
            f". Headline Max Pain is the tied strike closest to spot: {max_pain:,.0f}.")

st.subheader("Gross intrinsic payout at Max Pain")
c = st.columns(3)
c[0].metric("CE-side payout", f"₹{ce_at_mp:,.0f}")
c[1].metric("PE-side payout", f"₹{pe_at_mp:,.0f}")
c[2].metric("Combined payout", f"₹{min_payout:,.0f}")
st.caption("Payout = OI × OI quantity multiplier × intrinsic value. Amounts are ₹ estimates only "
           "when the API OI unit convention and multiplier are correct.")

st.subheader("Expiry settlement payout curve")
st.write("For each possible settlement price, the chart shows aggregate intrinsic payout to "
         "CE holders, PE holders and both sides combined.")
fig = go.Figure()
for column, label, width in [
    ("CE Payout", "CE buyer payout", 2),
    ("PE Payout", "PE buyer payout", 2),
    ("Total Payout", "Combined payout", 4),
]:
    fig.add_trace(go.Scatter(x=curves["Settlement"], y=curves[column],
                             mode="lines+markers", name=label, line={"width": width}))
fig.add_vline(x=max_pain, line_dash="dash", annotation_text=f"Max Pain {max_pain:,.0f}")
fig.add_vline(x=spot, line_dash="dot", annotation_text=f"Spot {spot:,.0f}")
fig.update_layout(height=490, xaxis_title="Hypothetical expiry settlement price",
                  yaxis_title="Gross intrinsic payout (₹ estimate)", hovermode="x unified",
                  margin={"l": 10, "r": 10, "t": 40, "b": 10})
fig.update_yaxes(tickformat=",.0f")
st.plotly_chart(fig, use_container_width=True)

with st.expander("How the calculations work — and why there are no separate CE Pain / PE Pain strikes", expanded=True):
    st.markdown(r"""
**CE payout at settlement S**

\[CE(S)=\sum_K OI_{CE}(K)\times\max(S-K,0)\]

**PE payout at settlement S**

\[PE(S)=\sum_K OI_{PE}(K)\times\max(K-S,0)\]

**Combined payout**

\[Total(S)=CE(S)+PE(S)\]

Max Pain is the candidate settlement price with the minimum combined intrinsic payout.

A standalone CE-only minimum is always at the lowest tested settlement price because CE payout
cannot decrease as settlement rises. A standalone PE-only minimum is always at the highest tested
settlement price because PE payout cannot increase as settlement rises. Those are just range edges,
not useful independent pain levels. This dashboard therefore shows the separate CE/PE payout curves
and their contributions at combined Max Pain instead.
""")

st.subheader("Option chain — returned strikes")
st.caption("OI and OI change are shown as returned by FYERS. The payout curve uses current OI, not OI change.")
cols = ["CE LTP", "CE OI", "CE OI Chg", "CE OI Chg %", "CE Volume", "CE Delta", "CE Gamma", "CE IV",
        "Strike", "PE IV", "PE Gamma", "PE Delta", "PE Volume", "PE OI Chg %", "PE OI Chg", "PE OI", "PE LTP"]
view = strike_df[cols].copy()
for col in ["CE OI", "CE OI Chg", "CE Volume", "PE Volume", "PE OI Chg", "PE OI"]:
    view[col] = view[col].round(0).astype("int64")
st.dataframe(view, use_container_width=True, hide_index=True, height=620)

with st.expander("Data coverage and API totals"):
    st.write(f"OI sum in returned strikes — CE: {strike_df['CE OI'].sum():,.0f} | "
             f"PE: {strike_df['PE OI'].sum():,.0f}")
    st.write(f"FYERS aggregate callOi: {num(payload.get('callOi')):,.0f} | "
             f"putOi: {num(payload.get('putOi')):,.0f}")
    st.write("The aggregate callOi/putOi fields may cover more strikes than this request. "
             "The payout curve uses only contracts present in optionsChain for the selected strike window.")

st.divider()
st.caption(
    "Important: every open option contract has both a long and a short side. OI does not identify "
    "which participant is the writer or their entry premium. These are gross expiry intrinsic-payout "
    "calculations, not net trader P&L, a forecast, or evidence of deliberate price control. Premiums, "
    "hedges, charges and positions outside the returned strike window are excluded."
)
