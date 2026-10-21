
"""NIFTY OTM CE/PE LTP - ATP + OI/PCR signal (Fyers + Streamlit).
Display only, no orders.
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fyers_apiv3 import fyersModel
from streamlit_autorefresh import st_autorefresh

IST = ZoneInfo("Asia/Kolkata")
QUOTE_BATCH = 50  # Fyers quotes API symbol limit per call


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

    if not quote or not is_num(quote["lp"]):
        raise RuntimeError(f"Spot LTP unavailable for {symbol}")

    return float(quote["lp"])


# ---------------------------------------------------------------------------
# General helpers
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


# ---------------------------------------------------------------------------
# Option-chain helpers
# ---------------------------------------------------------------------------

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


def build_oi_map(rows):
    """Build {(strike, leg): {'oi': value, 'oich': value}}."""
    oi_map = {}

    for row in rows:
        kind = row.get("option_type")

        if kind not in ("CE", "PE"):
            continue

        try:
            strike = float(row.get("strike_price"))
        except (TypeError, ValueError):
            continue

        oi_map[(strike, kind)] = {
            "oi": row.get("oi"),
            "oich": row.get("oich"),
        }

    return oi_map


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
# OI calculations
# ---------------------------------------------------------------------------

def calculate_oi_metrics(oi_map, ce_strikes, pe_strikes):
    """
    PCR = sum(PE OI) / sum(CE OI)

    Net OI change = sum(PE oich) - sum(CE oich)

    Uses the exact selected CE and PE strike lists.
    """

    def aggregate(strikes, leg, field):
        values = []

        for strike in strikes:
            item = oi_map.get((strike, leg))

            if item is None or not is_num(item.get(field)):
                raise RuntimeError(
                    f"Missing or invalid {field} for "
                    f"{leg} strike {strike:,.0f} in option-chain response."
                )

            values.append(float(item[field]))

        return sum(values)

    ce_oi = aggregate(ce_strikes, "CE", "oi")
    pe_oi = aggregate(pe_strikes, "PE", "oi")

    ce_oich = aggregate(ce_strikes, "CE", "oich")
    pe_oich = aggregate(pe_strikes, "PE", "oich")

    return {
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "pcr": pe_oi / ce_oi if ce_oi != 0 else None,
        "ce_oich": ce_oich,
        "pe_oich": pe_oich,
        "net_oich": pe_oich - ce_oich,
    }


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

def ltp(quotes, symbol, what="LTP"):
    q = quotes.get(symbol)

    if not q or not is_num(q["lp"]):
        raise RuntimeError(f"Invalid {what} for {symbol}")

    return float(q["lp"])


def synthetic_future(window, option_map, quotes):
    """Mean of (K + CE LTP - PE LTP) over the window strikes."""
    values = [
        k
        + ltp(quotes, option_map[k]["CE"])
        - ltp(quotes, option_map[k]["PE"])
        for k in window
    ]

    return sum(values) / len(values)


def otm_windows(strikes, syn_fut, straddle):
    """
    Calculate breakevens and matched OTM CE/PE strike lists.
    The shorter side is extended, never trimmed.
    """
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


def deviation_table(strikes, leg, option_map, quotes, center, bandwidth):
    """
    Calculate (LTP - ATP) / ATP in percent per strike,
    plus a normalized distance weight.
    """
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
            or not is_num(q["lp"])
            or not is_num(q["atp"])
            or float(q["atp"]) <= 0
        ):
            raise RuntimeError(f"Invalid LTP/ATP for {symbol}")

        lp = float(q["lp"])
        atp = float(q["atp"])

        rows.append({
            "Strike": k,
            f"{leg} LTP": lp,
            f"{leg} ATP": atp,
            f"{leg} Dev %": (lp - atp) / atp * 100,
            f"{leg} Weight": math.exp(
                -(((k - center) / bandwidth) ** 2)
            ),
        })

    df = pd.DataFrame(rows)
    df[f"{leg} Weight"] /= df[f"{leg} Weight"].sum()

    return df


def weighted_avg(df, leg):
    return float(
        (df[f"{leg} Dev %"] * df[f"{leg} Weight"]).sum()
    )


def compute(fyers, underlying, strikecount, expiry):
    """Run the full pipeline and return everything the UI needs."""

    chain = fetch_chain(fyers, underlying, strikecount, expiry)

    if chain.get("s") == "error" or chain.get("code", 200) != 200:
        raise RuntimeError(f"Option-chain request failed: {chain}")

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

    step = min(b - a for a, b in zip(strikes, strikes[1:]))

    # Spot
    spot = fetch_spot(fyers, underlying)

    # Synthetic future from 3 strikes around spot
    spot_window = three_around(
        strikes,
        closest(strikes, spot),
        step,
    )

    symbols = [
        option_map[k][leg]
        for k in spot_window
        for leg in ("CE", "PE")
    ]

    quotes = fetch_quotes(fyers, symbols)

    syn_fut = synthetic_future(
        spot_window,
        option_map,
        quotes,
    )

    # Synthetic ATM and ATM straddle
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

    straddle = sum(ltp(quotes, s) for s in atm_symbols)

    # Selected OTM strike ranges
    win = otm_windows(strikes, syn_fut, straddle)

    # ---------------------------------------------------------------
    # NEW: PCR and OI-change calculations from option-chain data
    # ---------------------------------------------------------------
    oi_map = build_oi_map(rows)

    oi_metrics = calculate_oi_metrics(
        oi_map,
        win["ce_strikes"],
        win["pe_strikes"],
    )

    # Fetch quotes for selected OTM CE and PE options
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

    # Distance weights anchored on continuous synthetic future
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

    return {
        "spot": spot,
        "syn_fut": syn_fut,
        "straddle": straddle,
        "win": win,
        "ce_df": ce_df,
        "pe_df": pe_df,
        "avg_ce": weighted_avg(ce_df, "CE"),
        "avg_pe": weighted_avg(pe_df, "PE"),
        "oi_metrics": oi_metrics,
        "quotes": quotes,
        "strike_range": (
            strikes[0],
            strikes[-1],
            len(strikes),
            step,
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

    return max(step_ms - ms_today % step_ms + buffer_ms, 200)


def render_calculation(df, leg, avg):
    st.write(
        f"**OTM {leg} Strikes "
        f"(({leg} LTP − {leg} ATP) / {leg} ATP):**"
    )

    for _, r in df.iterrows():
        ltp_ = r[f"{leg} LTP"]
        atp_ = r[f"{leg} ATP"]
        dev = r[f"{leg} Dev %"]
        weight = r[f"{leg} Weight"]

        st.write(
            f"{r['Strike']:,.0f}: "
            f"({ltp_:,.2f} − {atp_:,.2f}) / {atp_:,.2f} "
            f"= {dev:+,.2f}%  "
            f"× weight {weight:.1%} = "
            f"**{dev * weight:+,.2f}%**"
        )

    terms = " + ".join(
        f"({d * weight:+,.2f}%)"
        for d, weight in zip(
            df[f"{leg} Dev %"],
            df[f"{leg} Weight"],
        )
    )

    st.write(
        f"**Weighted Avg {leg} Dev:** "
        f"{terms} = **{avg:+,.2f}%**"
    )


# ---------------------------------------------------------------------------
# Main rendering
# ---------------------------------------------------------------------------

def render(r):
    w = r["win"]
    lo, hi, count, step = r["strike_range"]

    st.caption(
        f"Loaded both-leg strikes: {int(lo):,}–{int(hi):,} "
        f"({count} strikes, step {int(step)})"
    )

    for side, other, init, init_n, final in (
        ("CE", "PE", w["upper_init"], w["ce_init"], w["upper"]),
        ("PE", "CE", w["lower_init"], w["pe_init"], w["lower"]),
    ):
        if init != final:
            st.info(
                f"**OTM {side} side extended** — closest strike to "
                f"the breakeven was {init:,.0f} ({init_n} strikes), "
                f"extended to {final:,.0f} ({w['n']} strikes) "
                f"to match {other}."
            )

    # Basic market metrics
    m = st.columns(3)
    m[0].metric("Spot", f"{r['spot']:,.2f}")
    m[1].metric("Syn Future", f"{r['syn_fut']:,.2f}")
    m[2].metric("Syn ATM", f"{w['atm']:,.0f}")

    m = st.columns(3)
    m[0].metric("Straddle @ Syn ATM", f"{r['straddle']:,.2f}")
    m[1].metric("Upper BE Strike", f"{w['upper']:,.0f}")
    m[2].metric("Lower BE Strike", f"{w['lower']:,.0f}")

    # LTP - ATP metrics
    m = st.columns(4)

    m[0].metric(
        "Wtd Avg CE Dev. %",
        f"{r['avg_ce']:+,.2f}%",
    )

    m[1].metric(
        "Wtd Avg PE Dev. %",
        f"{r['avg_pe']:+,.2f}%",
    )

    m[2].metric(
        "OTM Strikes / Side",
        str(w["n"]),
    )

    ratio = (
        abs(r["avg_ce"] / r["avg_pe"])
        if r["avg_pe"] != 0
        else None
    )

    m[3].metric(
        "Abs CE Dev / PE Dev",
        f"{ratio:,.2f}" if ratio is not None else "N/A",
    )

    st.caption(
        f"**OTM CE** ({len(w['ce_strikes'])}): "
        f"{[int(s) for s in w['ce_strikes']]}  ·  "
        f"**OTM PE** ({len(w['pe_strikes'])}): "
        f"{[int(s) for s in w['pe_strikes']]}"
    )

    # ---------------------------------------------------------------
    # NEW: PCR and OI change metrics
    # ---------------------------------------------------------------
    oi = r["oi_metrics"]

    st.subheader("OI Analysis — Selected OTM Strike Range")

    m = st.columns(4)

    m[0].metric(
        "PCR (PE OI / CE OI)",
        f"{oi['pcr']:.3f}" if oi["pcr"] is not None else "N/A",
    )

    m[1].metric(
        "Total PE OI Change",
        f"{oi['pe_oich']:+,.0f}",
    )

    m[2].metric(
        "Total CE OI Change",
        f"{oi['ce_oich']:+,.0f}",
    )

    m[3].metric(
        "Net OI Change (PE − CE)",
        f"{oi['net_oich']:+,.0f}",
    )

    st.caption(
        f"PE OI: {oi['pe_oi']:,.0f}  ·  "
        f"CE OI: {oi['ce_oi']:,.0f}  ·  "
        f"PCR = {oi['pe_oi']:,.0f} / {oi['ce_oi']:,.0f}"
    )

    # Individual strike tables
    st.subheader("CE / PE (LTP − ATP) / ATP at OTM Strikes")

    left, right = st.columns(2)

    pct = lambda leg: {
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

    # Calculation details
    st.subheader("Calculation")

    render_calculation(
        r["ce_df"],
        "CE",
        r["avg_ce"],
    )

    render_calculation(
        r["pe_df"],
        "PE",
        r["avg_pe"],
    )

    # Raw quote data
    with st.expander("Raw Quotes API Data"):
        raw = pd.DataFrame([
            {
                "Symbol": symbol,
                "LTP": quote["lp"],
                "ATP": quote["atp"],
                "Bid": quote["bid"],
                "Ask": quote["ask"],
                "Volume": quote["volume"],
            }
            for symbol, quote in r["quotes"].items()
        ])

        st.dataframe(
            raw,
            use_container_width=True,
            hide_index=True,
        )

    # Formula reference
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
            r"AvgCEDev=\sum_{K\in OTM\ CE}w_K Dev^{CE}_K"
        )

        st.latex(
            r"AvgPEDev=\sum_{K\in OTM\ PE}w_K Dev^{PE}_K"
        )

        st.latex(
            r"PCR=\frac{\sum PE\ OI}{\sum CE\ OI}"
        )

        st.latex(
            r"NetOIChange=\sum PE\ oich-\sum CE\ oich"
        )

        st.write(
            "OTM CE = selected strikes above Syn ATM up to the "
            "upper breakeven. OTM PE = selected strikes below "
            "Syn ATM down to the lower breakeven. If the counts "
            "differ, the shorter side is extended outward, never "
            "trimmed. OI metrics use these same selected strike "
            "lists. Informational only — no orders are sent."
        )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="NIFTY LTP-ATP + OI Signal",
        layout="wide",
    )

    st.title("NIFTY LTP − ATP + OI/PCR Signal")

    st.markdown(
        "<style>[data-testid='stMetricValue']"
        "{font-size:22px!important}"
        "[data-testid='stMetricLabel'] p"
        "{font-size:13px!important}</style>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Settings")

        client_id = st.text_input("Client ID")
        access_token = st.text_input(
            "Access Token",
            type="password",
        )

        underlying = st.text_input(
            "Underlying",
            value="NSE:NIFTY50-INDEX",
        )

        strikecount = st.number_input(
            "Strike Count",
            min_value=1,
            max_value=50,
            value=15,
            step=1,
        )

        auto = st.checkbox(
            "Auto Refresh",
            value=True,
        )

        every = st.number_input(
            "Refresh Every (seconds)",
            min_value=1,
            max_value=3600,
            value=15,
            step=1,
        )

    if auto:
        st_autorefresh(
            interval=ms_until_next_boundary(every),
            key="atp_refresh",
        )

    st.caption(
        "Last refreshed: "
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

        expiry_response = fetch_chain(
            fyers,
            underlying,
            strikecount,
        )

        if (
            expiry_response.get("s") == "error"
            or expiry_response.get("code", 200) != 200
        ):
            raise RuntimeError(
                f"Expiry request failed: {expiry_response}"
            )

        expiries = (
            expiry_response.get("data", {}).get("expiryData", [])
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
                format_func=lambda idx: expiry_label(expiries[idx]),
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
