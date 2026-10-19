
"""NIFTY OTM CE/PE LTP-ATP signal (Fyers + Streamlit).
Display only, no orders. Includes matched-moneyness interpolation.
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fyers_apiv3 import fyersModel
from streamlit_autorefresh import st_autorefresh

IST = ZoneInfo("Asia/Kolkata")
QUOTE_BATCH = 50

# Matched-moneyness offsets, measured in ATM-straddle units.
MATCHED_OFFSETS = (0.5, 1.0)


# ---------------------------------------------------------------------------
# Fyers API
# ---------------------------------------------------------------------------

def fetch_chain(fyers, symbol, strikecount, expiry=0):
    return fyers.optionchain(data={
        "symbol": symbol,
        "strikecount": int(strikecount),
        "timestamp": int(expiry),
    })


def fetch_quotes(fyers, symbols):
    """Return {symbol: {lp, atp, bid, ask, volume}}."""
    symbols = list(dict.fromkeys(symbols))
    quotes = {}

    for i in range(0, len(symbols), QUOTE_BATCH):
        response = fyers.quotes({
            "symbols": ",".join(symbols[i:i + QUOTE_BATCH])
        })

        if response.get("s") != "ok":
            raise RuntimeError(f"Quotes request failed: {response}")

        for item in response.get("d", []):
            if item.get("s") != "ok":
                continue

            v = item.get("v", {})
            name = item.get("n") or v.get("symbol")

            if name:
                quotes[name] = {
                    k: v.get(k)
                    for k in ("lp", "atp", "bid", "ask", "volume")
                }

    return quotes


def fetch_spot(fyers, symbol):
    quote = fetch_quotes(fyers, [symbol]).get(symbol)

    if not quote or not is_num(quote.get("lp")):
        raise RuntimeError(f"Spot LTP unavailable for {symbol}")

    return float(quote["lp"])


# ---------------------------------------------------------------------------
# Chain helpers
# ---------------------------------------------------------------------------

def is_num(x):
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def expiry_label(item):
    return str(
        item.get("expiryDate")
        or item.get("date")
        or item.get("expiry")
        or "Unknown"
    )


def expiry_value(item):
    if item.get("expiry") is not None:
        return int(item["expiry"])
    return item.get("expiryDate")


def build_option_map(rows):
    """Build {strike: {'CE': symbol, 'PE': symbol}}."""
    option_map = {}

    for row in rows:
        kind = row.get("option_type")
        symbol = (
            row.get("symbol")
            or row.get("name")
            or row.get("original_name")
        )

        try:
            strike = float(row.get("strike_price"))
        except (TypeError, ValueError):
            continue

        if kind in ("CE", "PE") and symbol:
            option_map.setdefault(strike, {})[kind] = symbol

    return option_map


def closest(strikes, price):
    return min(strikes, key=lambda s: abs(s - price))


def three_around(strikes, centre, step):
    """Centre strike and its two neighbours."""
    if centre not in strikes:
        raise RuntimeError(
            f"Strike {centre:,.0f} is outside the loaded chain. "
            "Increase Strike Count."
        )

    i = strikes.index(centre)

    if i == 0 or i == len(strikes) - 1:
        raise RuntimeError(
            f"Strike {centre:,.0f} is at the chain edge. "
            "Increase Strike Count."
        )

    window = strikes[i - 1:i + 2]

    if any(b - a > step for a, b in zip(window, window[1:])):
        raise RuntimeError(
            f"Strikes {[int(s) for s in window]} are not adjacent. "
            "Increase Strike Count."
        )

    return window


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def ltp(quotes, symbol, what="LTP"):
    q = quotes.get(symbol)

    if not q or not is_num(q.get("lp")):
        raise RuntimeError(f"Invalid {what} for {symbol}")

    return float(q["lp"])


def synthetic_future(window, option_map, quotes):
    """Mean of K + CE LTP - PE LTP over the three-strike window."""
    values = [
        k
        + ltp(quotes, option_map[k]["CE"])
        - ltp(quotes, option_map[k]["PE"])
        for k in window
    ]

    return sum(values) / len(values)


def otm_windows(strikes, syn_fut, straddle):
    """Breakeven-based OTM windows, extending the shorter side if needed."""
    atm = closest(strikes, syn_fut)
    atm_i = strikes.index(atm)

    above = strikes[atm_i + 1:]
    below = strikes[:atm_i]

    if not above or not below:
        raise RuntimeError(
            f"Syn ATM {atm:,.0f} has no strikes on one side. "
            "Increase Strike Count."
        )

    upper_be = syn_fut + straddle
    lower_be = syn_fut - straddle

    upper_init = closest(above, upper_be)
    lower_init = closest(below, lower_be)

    ce_init = strikes.index(upper_init) - atm_i
    pe_init = atm_i - strikes.index(lower_init)

    n = max(ce_init, pe_init)

    if atm_i + n >= len(strikes) or atm_i - n < 0:
        raise RuntimeError(
            f"Need {n} OTM strikes per side but the chain is too short. "
            "Increase Strike Count."
        )

    return {
        "atm": atm,
        "upper_be": upper_be,
        "lower_be": lower_be,
        "upper_init": upper_init,
        "lower_init": lower_init,
        "upper": strikes[atm_i + n],
        "lower": strikes[atm_i - n],
        "ce_init": ce_init,
        "pe_init": pe_init,
        "n": n,
        "ce_strikes": strikes[atm_i + 1:atm_i + n + 1],
        "pe_strikes": strikes[atm_i - n:atm_i],
    }


def deviation_table(
    strikes, leg, option_map, quotes, center, bandwidth
):
    """Calculate per-strike LTP-ATP deviation and normalized distance weight."""
    if bandwidth <= 0:
        raise RuntimeError(
            "Straddle is zero or negative; cannot weight strikes."
        )

    rows = []

    for k in strikes:
        symbol = option_map[k][leg]
        q = quotes.get(symbol)

        if (
            not q
            or not is_num(q.get("lp"))
            or not is_num(q.get("atp"))
            or float(q["atp"]) <= 0
        ):
            raise RuntimeError(f"Invalid LTP/ATP for {symbol}")

        lp = float(q["lp"])
        atp = float(q["atp"])
        dev = (lp - atp) / atp * 100
        weight = math.exp(-(((k - center) / bandwidth) ** 2))

        rows.append({
            "Strike": k,
            f"{leg} LTP": lp,
            f"{leg} ATP": atp,
            f"{leg} Dev %": dev,
            f"{leg} Weight": weight,
        })

    df = pd.DataFrame(rows)
    df[f"{leg} Weight"] /= df[f"{leg} Weight"].sum()

    return df


def weighted_avg(df, leg):
    return float(
        (df[f"{leg} Dev %"] * df[f"{leg} Weight"]).sum()
    )


# ---------------------------------------------------------------------------
# Matched-moneyness interpolation
# ---------------------------------------------------------------------------

def matched_moneyness_table(
    strikes,
    leg,
    option_map,
    quotes,
    syn_fut,
    straddle,
    offsets=MATCHED_OFFSETS,
):
    """
    Estimate LTP-ATP deviation at fixed relative-moneyness offsets.

    CE target: Syn Future + offset * straddle
    PE target: Syn Future - offset * straddle

    Interpolates deviation percentages between adjacent available strikes.
    Does not extrapolate outside the available strike range.
    """
    if straddle <= 0:
        raise RuntimeError("Straddle must be positive.")

    points = []

    for strike in strikes:
        symbol = option_map.get(strike, {}).get(leg)

        if not symbol:
            continue

        q = quotes.get(symbol)

        if (
            not q
            or not is_num(q.get("lp"))
            or not is_num(q.get("atp"))
            or float(q["atp"]) <= 0
        ):
            continue

        lp = float(q["lp"])
        atp = float(q["atp"])
        dev = (lp - atp) / atp * 100

        points.append((float(strike), dev))

    points.sort(key=lambda item: item[0])

    rows = []

    for offset in offsets:
        if leg == "CE":
            target = syn_fut + offset * straddle
        else:
            target = syn_fut - offset * straddle

        lower = [p for p in points if p[0] <= target]
        upper = [p for p in points if p[0] >= target]

        if not lower or not upper:
            rows.append({
                "Offset": offset,
                "Target Strike": target,
                f"{leg} Interpolated Dev %": None,
                "Status": "Outside available strike range",
            })
            continue

        k1, d1 = lower[-1]
        k2, d2 = upper[0]

        if k1 == k2:
            interpolated = d1
            status = "Exact strike"
        else:
            interpolated = d1 + (
                (target - k1) / (k2 - k1)
            ) * (d2 - d1)
            status = "Interpolated"

        rows.append({
            "Offset": offset,
            "Target Strike": target,
            f"{leg} Interpolated Dev %": interpolated,
            "Status": status,
        })

    return pd.DataFrame(rows)


def matched_comparison_table(matched_ce, matched_pe):
    """Join CE and PE estimates by their common relative offset."""
    ce = matched_ce.set_index("Offset")
    pe = matched_pe.set_index("Offset")

    return pd.DataFrame({
        "Offset (Straddles)": ce.index,
        "CE Target Strike": ce["Target Strike"].to_numpy(),
        "CE Dev %": ce["CE Interpolated Dev %"].to_numpy(),
        "PE Target Strike": pe["Target Strike"].to_numpy(),
        "PE Dev %": pe["PE Interpolated Dev %"].to_numpy(),
        "CE Status": ce["Status"].to_numpy(),
        "PE Status": pe["Status"].to_numpy(),
    })


# ---------------------------------------------------------------------------
# Main calculation pipeline
# ---------------------------------------------------------------------------

def compute(fyers, underlying, strikecount, expiry):
    """Fetch current market data and calculate both original and matched metrics."""
    chain = fetch_chain(
        fyers, underlying, strikecount, expiry
    )

    rows = chain.get("data", {}).get("optionsChain", [])

    if not rows:
        raise RuntimeError("No option chain rows returned.")

    option_map = build_option_map(rows)

    strikes = sorted(
        k for k, legs in option_map.items()
        if "CE" in legs and "PE" in legs
    )

    if len(strikes) < 3:
        raise RuntimeError(
            "Fewer than 3 strikes with both CE and PE in the chain."
        )

    step = min(
        b - a for a, b in zip(strikes, strikes[1:])
    )

    spot = fetch_spot(fyers, underlying)

    # Synthetic future using the three strikes around spot.
    spot_window = three_around(
        strikes, closest(strikes, spot), step
    )

    symbols = [
        option_map[k][leg]
        for k in spot_window
        for leg in ("CE", "PE")
    ]

    quotes = fetch_quotes(fyers, symbols)

    syn_fut = synthetic_future(
        spot_window, option_map, quotes
    )

    atm = closest(strikes, syn_fut)
    atm_symbols = [
        option_map[atm]["CE"],
        option_map[atm]["PE"],
    ]

    quotes.update(
        fetch_quotes(
            fyers,
            [s for s in atm_symbols if s not in quotes],
        )
    )

    straddle = sum(
        ltp(quotes, symbol) for symbol in atm_symbols
    )

    win = otm_windows(strikes, syn_fut, straddle)

    # Fetch selected OTM CE and PE quotes.
    otm_symbols = (
        [option_map[k]["CE"] for k in win["ce_strikes"]]
        + [option_map[k]["PE"] for k in win["pe_strikes"]]
    )

    quotes.update(
        fetch_quotes(
            fyers,
            [s for s in otm_symbols if s not in quotes],
        )
    )

    # Original weighted-deviation calculation.
    ce_df = deviation_table(
        win["ce_strikes"],
        "CE",
        option_map,
        quotes,
        syn_fut,
        straddle,
    )

    pe_df = deviation_table(
        win["pe_strikes"],
        "PE",
        option_map,
        quotes,
        syn_fut,
        straddle,
    )

    avg_ce = weighted_avg(ce_df, "CE")
    avg_pe = weighted_avg(pe_df, "PE")

    # Matched-moneyness calculation uses all loaded strikes with valid quotes.
    # Fetch quotes for both legs at every available strike so that interpolation
    # can use adjacent strikes even outside the original selected OTM windows.
    all_option_symbols = [
        option_map[k][leg]
        for k in strikes
        for leg in ("CE", "PE")
    ]

    missing_symbols = [
        symbol for symbol in all_option_symbols
        if symbol not in quotes
    ]

    if missing_symbols:
        quotes.update(fetch_quotes(fyers, missing_symbols))

    matched_ce = matched_moneyness_table(
        strikes,
        "CE",
        option_map,
        quotes,
        syn_fut,
        straddle,
    )

    matched_pe = matched_moneyness_table(
        strikes,
        "PE",
        option_map,
        quotes,
        syn_fut,
        straddle,
    )

    matched = matched_comparison_table(
        matched_ce, matched_pe
    )

    return {
        "spot": spot,
        "syn_fut": syn_fut,
        "straddle": straddle,
        "win": win,
        "ce_df": ce_df,
        "pe_df": pe_df,
        "avg_ce": avg_ce,
        "avg_pe": avg_pe,
        "matched_ce": matched_ce,
        "matched_pe": matched_pe,
        "matched": matched,
        "quotes": quotes,
        "strike_range": (
            strikes[0], strikes[-1], len(strikes), step
        ),
    }


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def ms_until_next_boundary(step_seconds, buffer_ms=300):
    step_ms = max(int(step_seconds), 1) * 1000
    now = datetime.now(IST)

    ms_today = (
        ((now.hour * 60 + now.minute) * 60 + now.second) * 1000
        + now.microsecond // 1000
    )

    return max(
        step_ms - ms_today % step_ms + buffer_ms,
        200,
    )


def render_calculation(df, leg, avg):
    st.write(
        f"**OTM {leg} Strikes "
        f"(({leg} LTP − {leg} ATP) / {leg} ATP):**"
    )

    for _, r in df.iterrows():
        lp = r[f"{leg} LTP"]
        atp = r[f"{leg} ATP"]
        dev = r[f"{leg} Dev %"]
        weight = r[f"{leg} Weight"]

        st.write(
            f"{r['Strike']:,.0f}: "
            f"({lp:,.2f} − {atp:,.2f}) / {atp:,.2f} "
            f"= {dev:+,.2f}% × weight {weight:.1%} "
            f"= **{dev * weight:+,.2f}%**"
        )

    terms = " + ".join(
        f"({d * w:+,.2f}%)"
        for d, w in zip(
            df[f"{leg} Dev %"],
            df[f"{leg} Weight"],
        )
    )

    st.write(
        f"**Weighted Avg {leg} Dev:** "
        f"{terms} = **{avg:+,.2f}%**"
    )


def render(r):
    w = r["win"]
    lo, hi, count, step = r["strike_range"]

    st.caption(
        f"Loaded both-leg strikes: "
        f"{int(lo):,}–{int(hi):,} "
        f"({count} strikes, step {int(step)})"
    )

    for side, other, init, init_n, final in (
        (
            "CE", "PE", w["upper_init"],
            w["ce_init"], w["upper"]
        ),
        (
            "PE", "CE", w["lower_init"],
            w["pe_init"], w["lower"]
        ),
    ):
        if init != final:
            st.info(
                f"**OTM {side} side extended** — closest strike "
                f"to the breakeven was {init:,.0f} "
                f"({init_n} strikes), extended to {final:,.0f} "
                f"({w['n']} strikes) to match {other}."
            )

    m = st.columns(3)
    m[0].metric("Spot", f"{r['spot']:,.2f}")
    m[1].metric("Syn Future", f"{r['syn_fut']:,.2f}")
    m[2].metric("Syn ATM", f"{w['atm']:,.0f}")

    m = st.columns(3)
    m[0].metric(
        "Straddle @ Syn ATM", f"{r['straddle']:,.2f}"
    )
    m[1].metric("Upper BE Strike", f"{w['upper']:,.0f}")
    m[2].metric("Lower BE Strike", f"{w['lower']:,.0f}")

    m = st.columns(3)
    m[0].metric("Wtd Avg CE Dev. %", f"{r['avg_ce']:+,.2f}%")
    m[1].metric("Wtd Avg PE Dev. %", f"{r['avg_pe']:+,.2f}%")
    m[2].metric("OTM Strikes / Side", str(w["n"]))

    st.caption(
        f"**OTM CE** ({len(w['ce_strikes'])}): "
        f"{[int(s) for s in w['ce_strikes']]}  ·  "
        f"**OTM PE** ({len(w['pe_strikes'])}): "
        f"{[int(s) for s in w['pe_strikes']]}"
    )

    st.subheader("Original CE / PE OTM Deviations")

    left, right = st.columns(2)

    def pct(leg):
        return {
            f"{leg} Dev %": "{:+.2f}%",
            f"{leg} Weight": "{:.1%}",
        }

    left.dataframe(
        r["ce_df"].style.format(pct("CE")),
        use_container_width=True,
        hide_index=True,
    )

    right.dataframe(
        r["pe_df"].style.format(pct("PE")),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Original Weighted Calculation")
    render_calculation(r["ce_df"], "CE", r["avg_ce"])
    render_calculation(r["pe_df"], "PE", r["avg_pe"])

    # New matched-moneyness results.
    st.subheader("Matched-Moneyness CE / PE Deviations")

    st.caption(
        "CE and PE deviations are interpolated at fixed relative "
        "distances from the continuous Syn Future. These are estimated "
        "deviations, not tradable option prices or confirmed signals."
    )

    matched = r["matched"]

    st.dataframe(
        matched.style.format({
            "Offset (Straddles)": "{:.1f}",
            "CE Target Strike": "{:,.2f}",
            "CE Dev %": "{:+.2f}%",
            "PE Target Strike": "{:,.2f}",
            "PE Dev %": "{:+.2f}%",
        }, na_rep="N/A"),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Raw Quotes API Data"):
        raw = pd.DataFrame([
            {
                "Symbol": symbol,
                "LTP": q["lp"],
                "ATP": q["atp"],
                "Bid": q["bid"],
                "Ask": q["ask"],
                "Volume": q["volume"],
            }
            for symbol, q in r["quotes"].items()
        ])

        st.dataframe(
            raw,
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Strategy Formula"):
        st.latex(
            r"Dev_K=\frac{LTP_K-ATP_K}{ATP_K}\times100"
        )

        st.latex(
            r"SynFut=\frac{1}{3}\sum_{K\in SpotATM\pm1}"
            r"(K+CE_{LTP,K}-PE_{LTP,K})"
        )

        st.latex(
            r"Straddle=CE_{LTP,SynATM}+PE_{LTP,SynATM}"
        )

        st.latex(
            r"BE_{\pm}=SynFut\pm Straddle"
        )

        st.latex(
            r"w_K=\frac{e^{-((K-SynFut)/Straddle)^2}}"
            r"{\sum_J e^{-((J-SynFut)/Straddle)^2}}"
        )

        st.latex(
            r"AvgCEDev=\sum_Kw_KDev^{CE}_K,\quad"
            r"AvgPEDev=\sum_Kw_KDev^{PE}_K"
        )

        st.latex(
            r"K^*_{CE}=SynFut+x\cdot Straddle"
        )

        st.latex(
            r"K^*_{PE}=SynFut-x\cdot Straddle"
        )

        st.write(
            "Original OTM weighted calculations are preserved. "
            "Matched-moneyness calculations interpolate each leg's "
            "LTP-ATP deviation at offsets of 0.5 and 1.0 straddles. "
            "Targets outside the loaded strike range are not extrapolated. "
            "Informational only; no orders are sent."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="NIFTY LTP-ATP Signal",
        layout="wide",
    )

    st.title("NIFTY LTP − ATP Signal")

    st.markdown(
        "<style>[data-testid='stMetricValue']{font-size:22px!important}"
        "[data-testid='stMetricLabel'] p{font-size:13px!important}</style>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Settings")

        client_id = st.text_input("Client ID")
        access_token = st.text_input(
            "Access Token", type="password"
        )
        underlying = st.text_input(
            "Underlying", value="NSE:NIFTY50-INDEX"
        )

        strikecount = st.number_input(
            "Strike Count", 1, 50, 15, 1
        )

        auto = st.checkbox("Auto Refresh", value=True)

        every = st.number_input(
            "Refresh Every (seconds)", 1, 3600, 15, 1
        )

    if auto:
        st_autorefresh(
            interval=ms_until_next_boundary(every),
            key="atp_refresh",
        )

    st.caption(
        f"Last refreshed: "
        f"{datetime.now(IST).strftime('%d-%b-%Y %I:%M:%S %p IST')}"
    )

    if not client_id or not access_token:
        st.info("Enter Client ID and Access Token in the sidebar.")
        return

    try:
        fyers = fyersModel.FyersModel(
            client_id=client_id,
            token=access_token,
            is_async=False,
            log_path="",
        )

        expiries = (
            fetch_chain(fyers, underlying, strikecount)
            .get("data", {})
            .get("expiryData", [])
        )

        if not expiries:
            raise RuntimeError(
                "No expiry data returned by the option chain."
            )

        with st.sidebar:
            st.divider()

            i = st.selectbox(
                "Expiry",
                range(len(expiries)),
                format_func=lambda index: expiry_label(
                    expiries[index]
                ),
            )

        result = compute(
            fyers,
            underlying,
            strikecount,
            expiry_value(expiries[i]),
        )

    except Exception as e:
        st.error(str(e))
        return

    render(result)

    if st.button("Manual Refresh"):
        st.rerun()


if __name__ == "__main__":
    main()
