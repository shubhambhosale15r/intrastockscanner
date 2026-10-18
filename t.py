import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fyers_apiv3 import fyersModel
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="NIFTY CE/PE ATP Signal",
    layout="wide"
)


# ---------------------------------------------------------------------------
# Fyers API calls (no caching: every rerun fetches live data)
# ---------------------------------------------------------------------------

def get_fyers(client_id, access_token):
    return fyersModel.FyersModel(
        client_id=client_id,
        token=access_token,
        is_async=False,
        log_path=""
    )

def fetch_chain(fyers, symbol, strikecount, timestamp):
    return fyers.optionchain(data={
        "symbol": symbol,
        "strikecount": int(strikecount),
        "timestamp": int(timestamp),
        "greeks": "1"
    })

def fetch_spot(fyers, symbol):
    response = fyers.quotes({"symbols": symbol})

    if response.get("s") != "ok":
        raise RuntimeError(f"Spot quote failed: {response}")

    data = response.get("d", [])
    if not data:
        raise RuntimeError(f"Spot quote returned no data: {response}")

    item = data[0]
    if item.get("s") != "ok":
        raise RuntimeError(f"Spot quote failed: {item}")

    value = item.get("v", {})
    lp = value.get("lp")

    if lp is None or pd.isna(lp):
        raise RuntimeError(f"Spot LTP unavailable: {item}")

    return float(lp)

def fetch_option_quotes(fyers, symbols):
    symbols = tuple(dict.fromkeys(symbols))
    if not symbols:
        return {}

    if len(symbols) > 50:
        raise ValueError("Quotes API accepts a maximum of 50 symbols per request.")

    response = fyers.quotes({"symbols": ",".join(symbols)})

    if response.get("s") != "ok":
        raise RuntimeError(f"Option quotes failed: {response}")

    quotes = {}
    for item in response.get("d", []):
        if item.get("s") != "ok":
            continue
        value = item.get("v", {})
        symbol = item.get("n") or value.get("symbol")
        if not symbol:
            continue
        quotes[symbol] = {
            "symbol": symbol,
            "lp": value.get("lp"),
            "atp": value.get("atp"),
            "bid": value.get("bid"),
            "ask": value.get("ask"),
            "volume": value.get("volume")
        }
    return quotes


# ---------------------------------------------------------------------------
# Option chain helpers
# ---------------------------------------------------------------------------

def get_expiry_data(chain_response):
    return chain_response.get("data", {}).get("expiryData", [])

def get_option_chain_rows(chain_response):
    return chain_response.get("data", {}).get("optionsChain", [])

def format_expiry(expiry_item):
    expiry_date = expiry_item.get("expiryDate") or expiry_item.get("date")
    expiry_epoch = expiry_item.get("expiry")

    if expiry_date:
        return str(expiry_date)
    if expiry_epoch:
        return str(expiry_epoch)
    return "Unknown"

def get_expiry_value(expiry_item):
    if expiry_item.get("expiry") is not None:
        return int(expiry_item["expiry"])
    if expiry_item.get("expiryDate") is not None:
        return expiry_item["expiryDate"]
    return None

def build_option_map(chain):
    option_map = {}

    for row in chain:
        option_type = row.get("option_type")
        strike_price = row.get("strike_price")
        symbol = (
            row.get("symbol")
            or row.get("name")
            or row.get("original_name")
        )

        if option_type not in ("CE", "PE"):
            continue
        if strike_price is None or symbol is None:
            continue

        try:
            strike = float(strike_price)
        except (TypeError, ValueError):
            continue

        option_map.setdefault(strike, {})
        option_map[strike][option_type] = symbol

    return option_map

def build_greeks_map(chain):
    greeks_map = {}

    for row in chain:
        symbol = row.get("symbol")
        greeks = row.get("greeks")

        if not symbol or not isinstance(greeks, dict):
            continue

        greeks_map[symbol] = {
            "ltp": row.get("ltp"),
            "delta": greeks.get("delta"),
            "theta": greeks.get("theta")
        }

    return greeks_map

def calculate_synthetic_future(selected_strikes, option_map, quotes):
    rows = []

    for strike in selected_strikes:
        ce_symbol = option_map[strike]["CE"]
        pe_symbol = option_map[strike]["PE"]

        ce_quote = quotes.get(ce_symbol)
        pe_quote = quotes.get(pe_symbol)

        if not ce_quote:
            raise RuntimeError(f"CE quote missing: {ce_symbol}")
        if not pe_quote:
            raise RuntimeError(f"PE quote missing: {pe_symbol}")

        ce_ltp = ce_quote.get("lp")
        pe_ltp = pe_quote.get("lp")

        if not (valid_number(ce_ltp) and valid_number(pe_ltp)):
            raise RuntimeError(
                f"Invalid LTP for synthetic future at strike {strike}: "
                f"CE={ce_ltp}, PE={pe_ltp}"
            )

        ce_ltp = float(ce_ltp)
        pe_ltp = float(pe_ltp)

        rows.append({
            "Strike": strike,
            "CE LTP": ce_ltp,
            "PE LTP": pe_ltp,
            "Synthetic Future": strike + ce_ltp - pe_ltp
        })

    df = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)
    return df, float(df["Synthetic Future"].mean())

def both_leg_strikes(option_map):
    return sorted(
        strike for strike, legs in option_map.items()
        if "CE" in legs and "PE" in legs
    )

def find_closest_strike(option_map, price):
    candidates = both_leg_strikes(option_map)
    if not candidates:
        raise RuntimeError("No strike with both CE and PE available.")
    return min(candidates, key=lambda strike: abs(strike - price))

def select_three_around(option_map, center_strike):
    strikes = both_leg_strikes(option_map)

    if len(strikes) < 3:
        raise RuntimeError(
            "Fewer than 3 strikes with both CE and PE are available."
        )

    if center_strike not in strikes:
        raise RuntimeError(
            f"Centre strike {center_strike:,.0f} is not in the loaded chain "
            f"(both-leg strikes: {int(strikes[0]):,}–{int(strikes[-1]):,}). "
            f"Increase Strike Count."
        )

    index = strikes.index(center_strike)

    if index == 0 or index == len(strikes) - 1:
        raise RuntimeError(
            f"{center_strike:,.0f} is at the edge of the loaded chain, so it "
            f"cannot be the centre of a Syn ATM−1 / Syn ATM / Syn ATM+1 "
            f"window (loaded range {int(strikes[0]):,}–{int(strikes[-1]):,}). "
            f"Increase Strike Count in the sidebar."
        )

    window = strikes[index - 1:index + 2]
    step = min(b - a for a, b in zip(strikes, strikes[1:]))

    if max(b - a for a, b in zip(window, window[1:])) > step:
        raise RuntimeError(
            f"Strikes {[int(x) for x in window]} are not adjacent "
            f"(a CE or PE is missing in between). Increase Strike Count."
        )

    return window, None

def find_breakeven_strikes(option_map, center, straddle):
    strikes = both_leg_strikes(option_map)
    if not strikes:
        raise RuntimeError("No strikes available for breakeven calculation.")

    upper_breakeven = center + straddle
    lower_breakeven = center - straddle

    upper_strike = min(strikes, key=lambda s: abs(s - upper_breakeven))
    lower_strike = min(strikes, key=lambda s: abs(s - lower_breakeven))

    if "CE" not in option_map[upper_strike]:
        raise RuntimeError(
            f"CE unavailable at upper breakeven strike {upper_strike}."
        )
    if "PE" not in option_map[lower_strike]:
        raise RuntimeError(
            f"PE unavailable at lower breakeven strike {lower_strike}."
        )

    return upper_breakeven, lower_breakeven, upper_strike, lower_strike

def valid_number(value):
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False

def theta_ratio(theta, delta):
    """|Theta| / |Delta|."""
    if not all(valid_number(x) for x in (theta, delta)):
        return None

    denominator = abs(float(delta))
    if denominator == 0:
        return None

    return abs(float(theta)) / denominator

def calculate_deviations(selected_strikes, option_map, quotes):
    rows = []

    for strike in selected_strikes:
        ce_symbol = option_map[strike]["CE"]
        pe_symbol = option_map[strike]["PE"]

        ce_quote = quotes.get(ce_symbol)
        pe_quote = quotes.get(pe_symbol)

        if not ce_quote:
            raise RuntimeError(f"CE quote missing: {ce_symbol}")
        if not pe_quote:
            raise RuntimeError(f"PE quote missing: {pe_symbol}")

        ce_ltp = ce_quote.get("lp")
        ce_atp = ce_quote.get("atp")
        pe_ltp = pe_quote.get("lp")
        pe_atp = pe_quote.get("atp")

        if not all(valid_number(x) for x in [ce_ltp, ce_atp, pe_ltp, pe_atp]):
            raise RuntimeError(
                f"Invalid quote data for strike {strike}: "
                f"CE LTP={ce_ltp}, CE ATP={ce_atp}, "
                f"PE LTP={pe_ltp}, PE ATP={pe_atp}"
            )

        ce_ltp = float(ce_ltp)
        ce_atp = float(ce_atp)
        pe_ltp = float(pe_ltp)
        pe_atp = float(pe_atp)

        rows.append({
            "Strike": strike,
            "CE Symbol": ce_symbol,
            "CE ATP": ce_atp,
            "CE LTP": ce_ltp,
            "CE LTP-ATP": ce_ltp - ce_atp,
            "PE Symbol": pe_symbol,
            "PE LTP": pe_ltp,
            "PE ATP": pe_atp,
            "PE LTP-ATP": pe_ltp - pe_atp
        })

    result = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)
    return (
        result,
        float(result["CE LTP-ATP"].mean()),
        float(result["PE LTP-ATP"].mean())
    )


# ---------------------------------------------------------------------------
# Time / refresh helpers
# ---------------------------------------------------------------------------

def get_ist_time():
    return datetime.now(ZoneInfo("Asia/Kolkata"))

def format_ist_time(dt):
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d-%b-%Y %I:%M:%S %p IST")

def ms_until_next_boundary(step_seconds, buffer_ms=300):
    step_ms = max(int(step_seconds), 1) * 1000
    now = get_ist_time()
    ms_since_midnight = (
        ((now.hour * 60 + now.minute) * 60 + now.second) * 1000
        + now.microsecond // 1000
    )
    remainder = ms_since_midnight % step_ms
    wait = step_ms - remainder + buffer_ms
    return int(max(wait, 200))

def install_autorefresh(enabled, refresh_seconds):
    if not enabled:
        return
    st_autorefresh(
        interval=ms_until_next_boundary(refresh_seconds),
        key="nifty_atp_refresh"
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

st.title("NIFTY CE/PE ATP Signal")

with st.sidebar:
    st.header("Settings")

    client_id = st.text_input("Client ID", type="default")
    access_token = st.text_input("Access Token", type="password")
    underlying = st.text_input("Underlying", value="NSE:NIFTY50-INDEX")

    strikecount = st.number_input(
        "Strike Count",
        min_value=1,
        max_value=50,
        value=10,
        step=1
    )

    autorefresh_enabled = st.checkbox("Auto Refresh", value=True)

    refresh_seconds = st.number_input(
        "Refresh Every (seconds)",
        min_value=1,
        max_value=3600,
        value=15,
        step=1
    )

install_autorefresh(autorefresh_enabled, refresh_seconds)

last_refreshed = get_ist_time()
st.caption(f"Last refreshed: {format_ist_time(last_refreshed)}")

if not client_id or not access_token:
    st.info("Enter Client ID and Access Token in the sidebar.")
    st.stop()

try:
    fyers = get_fyers(client_id, access_token)
except Exception as e:
    st.error(f"Fyers initialization failed: {e}")
    st.stop()

try:
    initial_chain = fetch_chain(fyers, underlying, int(strikecount), 0)
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

expiry_data = get_expiry_data(initial_chain)
if not expiry_data:
    st.error("No expiry data returned by the option chain.")
    st.stop()

expiry_labels = [format_expiry(item) for item in expiry_data]
expiry_values = [get_expiry_value(item) for item in expiry_data]

expiry_index = st.sidebar.selectbox(
    "Expiry",
    range(len(expiry_labels)),
    format_func=lambda i: expiry_labels[i]
)

selected_expiry = expiry_values[expiry_index]

try:
    chain_response = fetch_chain(
        fyers,
        underlying,
        int(strikecount),
        selected_expiry
    )
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    st.stop()

try:
    spot = fetch_spot(fyers, underlying)
except Exception as e:
    st.error(f"Spot fetch failed: {e}")
    st.stop()

chain_rows = get_option_chain_rows(chain_response)
if not chain_rows:
    st.error("No option chain rows returned.")
    st.stop()

option_map = build_option_map(chain_rows)
window_warnings = []

strike_list = both_leg_strikes(option_map)
if len(strike_list) < 3:
    st.error("Fewer than 3 strikes with both CE and PE in the option chain.")
    st.stop()

strike_step = min(
    b_ - a_ for a_, b_ in zip(strike_list, strike_list[1:])
)

# --- Step 0: spot ATM window (bootstrap for synthetic future) --------------
try:
    spot_atm = find_closest_strike(option_map, spot)
    selected_strikes, _ = select_three_around(option_map, spot_atm)
except Exception as e:
    st.error(str(e))
    st.stop()

# --- Step 1: quotes for spot ATM-1, ATM, ATM+1 -----------------------------
trade_symbols = []
for strike in selected_strikes:
    trade_symbols.append(option_map[strike]["CE"])
    trade_symbols.append(option_map[strike]["PE"])

try:
    quotes = fetch_option_quotes(fyers, tuple(trade_symbols))
except Exception as e:
    st.error(f"Option quotes fetch failed: {e}")
    st.stop()

# --- Step 2: bootstrap synthetic future, Syn ATM, Syn ATM±1 ----------------
try:
    syn_df, syn_fut = calculate_synthetic_future(
        selected_strikes,
        option_map,
        quotes
    )

    atm = find_closest_strike(option_map, syn_fut)
    dev_strikes, _ = select_three_around(option_map, atm)
except Exception as e:
    st.error(str(e))
    st.stop()

role_map = {
    dev_strikes[0]: "Syn ATM−1",
    dev_strikes[1]: "Syn ATM",
    dev_strikes[2]: "Syn ATM+1",
}

# --- Step 3: straddle at Syn ATM -------------------------------------------
atm_ce_symbol = option_map[atm]["CE"]
atm_pe_symbol = option_map[atm]["PE"]

dev_symbols = []
for strike in dev_strikes:
    dev_symbols.append(option_map[strike]["CE"])
    dev_symbols.append(option_map[strike]["PE"])

missing_dev_symbols = [
    sym for sym in dict.fromkeys(dev_symbols)
    if sym not in quotes
]

if missing_dev_symbols:
    trade_symbols.extend(missing_dev_symbols)
    try:
        quotes.update(fetch_option_quotes(fyers, tuple(missing_dev_symbols)))
    except Exception as e:
        st.error(f"Synthetic ATM option quotes fetch failed: {e}")
        st.stop()

atm_ce_quote = quotes.get(atm_ce_symbol)
atm_pe_quote = quotes.get(atm_pe_symbol)

if not atm_ce_quote or not atm_pe_quote:
    st.error("Synthetic ATM CE/PE quotes unavailable.")
    st.stop()

atm_ce_ltp = atm_ce_quote.get("lp")
atm_pe_ltp = atm_pe_quote.get("lp")

if not valid_number(atm_ce_ltp) or not valid_number(atm_pe_ltp):
    st.error(
        f"Invalid synthetic ATM option prices: "
        f"CE={atm_ce_ltp}, PE={atm_pe_ltp}"
    )
    st.stop()

atm_ce_ltp = float(atm_ce_ltp)
atm_pe_ltp = float(atm_pe_ltp)
atm_straddle = atm_ce_ltp + atm_pe_ltp

# --- Step 4: breakevens = synthetic future +/- straddle --------------------
try:
    (
        upper_breakeven,
        lower_breakeven,
        upper_strike,
        lower_strike
    ) = find_breakeven_strikes(option_map, syn_fut, atm_straddle)
except Exception as e:
    st.error(str(e))
    st.stop()

if abs(upper_strike - upper_breakeven) > strike_step:
    window_warnings.append(
        f"Upper breakeven {upper_breakeven:,.0f} is outside the loaded chain "
        f"(nearest listed strike {upper_strike:,.0f}). Increase Strike Count."
    )

if abs(lower_strike - lower_breakeven) > strike_step:
    window_warnings.append(
        f"Lower breakeven {lower_breakeven:,.0f} is outside the loaded chain "
        f"(nearest listed strike {lower_strike:,.0f}). Increase Strike Count."
    )

# --- Step 5: deviations on the Syn ATM window ------------------------------
try:
    (
        calculation_df,
        average_ce_deviation,
        average_pe_deviation
    ) = calculate_deviations(dev_strikes, option_map, quotes)
except Exception as e:
    st.error(f"Deviation calculation failed: {e}")
    st.stop()

# --- Syn ATM-window synthetic future (display only) ------------------------
syn_df_final, syn_fut_final = calculate_synthetic_future(
    dev_strikes,
    option_map,
    quotes
)

# --- |Theta| / |Delta| at Syn ATM−1, Syn ATM, Syn ATM+1 -------------------
greeks_map = build_greeks_map(chain_rows)

theta_rows = []
for strike in dev_strikes:
    ce_sym = option_map[strike].get("CE")
    pe_sym = option_map[strike].get("PE")

    ce_g = greeks_map.get(ce_sym, {}) if ce_sym else {}
    pe_g = greeks_map.get(pe_sym, {}) if pe_sym else {}

    ce_theta = ce_g.get("theta")
    ce_delta = ce_g.get("delta")
    pe_theta = pe_g.get("theta")
    pe_delta = pe_g.get("delta")

    theta_rows.append({
        "Role": role_map[strike],
        "Strike": strike,
        "CE Theta": ce_theta,
        "CE Delta": ce_delta,
        "CE |Θ| / |Δ|": theta_ratio(ce_theta, ce_delta),
        "PE Theta": pe_theta,
        "PE Delta": pe_delta,
        "PE |Θ| / |Δ|": theta_ratio(pe_theta, pe_delta)
    })

theta_df = pd.DataFrame(theta_rows)

# --- CE / PE share at Syn ATM±1 -------------------------------------------
share_df = calculation_df[["Strike", "CE LTP", "PE LTP"]].copy()
share_total = share_df["CE LTP"] + share_df["PE LTP"]

share_df["CE / (CE+PE) %"] = (
    share_df["CE LTP"] / share_total.where(share_total > 0) * 100
)
share_df["PE / (CE+PE) %"] = (
    share_df["PE LTP"] / share_total.where(share_total > 0) * 100
)

avg_ce_share = float(share_df["CE / (CE+PE) %"].mean())
avg_pe_share = float(share_df["PE / (CE+PE) %"].mean())

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] {
        font-size: 20px !important;
    }
    [data-testid="stMetricLabel"] p {
        font-size: 14px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

for warning_text in window_warnings:
    st.warning(warning_text)

st.caption(
    f"Loaded both-leg strikes: "
    f"{int(strike_list[0]):,}–{int(strike_list[-1]):,} "
    f"({len(strike_list)} strikes, step {int(strike_step)})"
)

m = st.columns(9)

m[0].metric("Spot", f"{spot:,.2f}")
m[1].metric("Syn Future", f"{syn_fut:,.2f}")
m[2].metric("Syn Fut @ Syn ATM", f"{syn_fut_final:,.2f}")
m[3].metric("Syn ATM", f"{atm:,.0f}")
m[4].metric("Straddle @ Syn ATM", f"{atm_straddle:,.2f}")
m[5].metric("Upper BE Strike", f"{upper_strike:,.2f}")
m[6].metric("Lower BE Strike", f"{lower_strike:,.2f}")
m[7].metric("Avg CE Dev.", f"{average_ce_deviation:+,.2f}")
m[8].metric("Avg PE Dev.", f"{average_pe_deviation:+,.2f}")

# --- Synthetic Future — Syn ATM window --------------------------------------
st.subheader(
    "Synthetic Future (Strike + CE LTP − PE LTP) — Syn ATM window"
)

syn_df_final_display = syn_df_final.copy()
syn_df_final_display.insert(0, "Role", ["Syn ATM−1", "Syn ATM", "Syn ATM+1"])

st.dataframe(
    syn_df_final_display,
    use_container_width=True,
    hide_index=True
)

st.write(
    f"**Synthetic Future on the signal strikes** = "
    f"**{syn_fut_final:,.2f}**  |  "
    f"Bootstrap average used for the breakevens = {syn_fut:,.2f}  |  "
    f"Δ = {syn_fut_final - syn_fut:+,.2f}"
)

st.caption(
    "The breakevens below use the bootstrap average (from the spot ATM window). "
    "The two averages only diverge meaningfully when the synthetic future sits "
    "near the midpoint between two strikes."
)

st.subheader("Breakeven Strike Selection")

be_df = pd.DataFrame([
    {
        "Spot": spot,
        "Synthetic Future": syn_fut,
        "Syn ATM Strike": atm,
        "Syn ATM CE LTP": atm_ce_ltp,
        "Syn ATM PE LTP": atm_pe_ltp,
        "Straddle": atm_straddle,
        "Upper Breakeven": upper_breakeven,
        "Upper BE Strike": upper_strike,
        "Lower Breakeven": lower_breakeven,
        "Lower BE Strike": lower_strike
    }
])

st.dataframe(be_df, use_container_width=True, hide_index=True)

# --- CE / (CE+PE) at Syn ATM±1 ---------------------------------------------
st.subheader("CE / (CE + PE) at Syn ATM−1, Syn ATM, Syn ATM+1")

st.dataframe(share_df, use_container_width=True, hide_index=True)

sh = st.columns(2)
sh[0].metric(
    "Avg CE / (CE + PE)",
    "n/a" if pd.isna(avg_ce_share) else f"{avg_ce_share:.1f}%"
)
sh[1].metric(
    "Avg PE / (CE + PE)",
    "n/a" if pd.isna(avg_pe_share) else f"{avg_pe_share:.1f}%"
)

st.caption(
    "Share at each strike = CE LTP / (CE LTP + PE LTP) and "
    "PE LTP / (CE LTP + PE LTP); the metrics are the simple average of the "
    "three strikes."
)

# --- |Theta| / |Delta| at Syn ATM±1 ----------------------------------------
st.subheader(
    "|Theta| / |Delta| at Syn ATM−1, Syn ATM, Syn ATM+1"
)

st.dataframe(theta_df, use_container_width=True, hide_index=True)

st.caption(
    "Ratio = abs(Theta) / abs(Delta). "
    "Theta and Delta are from the same option chain snapshot. "
    "Shown for both CE and PE at each of the three Syn ATM signal strikes."
)

# --- CE / PE LTP − ATP -----------------------------------------------------
st.subheader("CE / PE LTP − ATP (Syn ATM−1, Syn ATM, Syn ATM+1)")

display_df = calculation_df[
    [
        "Strike",
        "CE ATP",
        "CE LTP",
        "CE LTP-ATP",
        "PE LTP",
        "PE ATP",
        "PE LTP-ATP"
    ]
].copy()

display_df.insert(0, "Role", display_df["Strike"].map(role_map))

st.dataframe(display_df, use_container_width=True, hide_index=True)

# --- Calculation breakdown -------------------------------------------------
st.subheader("Calculation")

for _, row in calculation_df.iterrows():
    st.write(
        f"**{row['Strike']:,.0f}** → "
        f"CE: {row['CE LTP']:,.2f} − {row['CE ATP']:,.2f} = "
        f"**{row['CE LTP-ATP']:+,.2f}** | "
        f"PE: {row['PE LTP']:,.2f} − {row['PE ATP']:,.2f} = "
        f"**{row['PE LTP-ATP']:+,.2f}**"
    )

ce_values = calculation_df["CE LTP-ATP"].tolist()
pe_values = calculation_df["PE LTP-ATP"].tolist()

ce_values_text = " + ".join(f"({x:+,.2f})" for x in ce_values)
pe_values_text = " + ".join(f"({x:+,.2f})" for x in pe_values)

st.write(
    f"**Average CE Dev:** ({ce_values_text}) / {len(ce_values)} = "
    f"**{average_ce_deviation:+,.2f}**"
)

st.write(
    f"**Average PE Dev:** ({pe_values_text}) / {len(pe_values)} = "
    f"**{average_pe_deviation:+,.2f}**"
)

for _, row in share_df.iterrows():
    st.write(
        f"**{row['Strike']:,.0f}** → "
        f"CE: {row['CE LTP']:,.2f} / "
        f"({row['CE LTP']:,.2f} + {row['PE LTP']:,.2f}) = "
        f"**{row['CE / (CE+PE) %']:.1f}%** | "
        f"PE: {row['PE LTP']:,.2f} / "
        f"({row['CE LTP']:,.2f} + {row['PE LTP']:,.2f}) = "
        f"**{row['PE / (CE+PE) %']:.1f}%**"
    )

ce_share_text = " + ".join(
    f"{x:.1f}%" for x in share_df["CE / (CE+PE) %"].tolist()
)
pe_share_text = " + ".join(
    f"{x:.1f}%" for x in share_df["PE / (CE+PE) %"].tolist()
)

st.write(
    f"**Avg CE / (CE + PE):** ({ce_share_text}) / {len(share_df)} = "
    f"**{avg_ce_share:.1f}%**"
)

st.write(
    f"**Avg PE / (CE + PE):** ({pe_share_text}) / {len(share_df)} = "
    f"**{avg_pe_share:.1f}%**"
)

# --- Raw quotes -----------------------------------------------------------
with st.expander("Raw Quotes API Data"):
    raw_rows = []
    for symbol in dict.fromkeys(trade_symbols):
        quote = quotes.get(symbol, {})
        raw_rows.append({
            "Symbol": symbol,
            "LTP": quote.get("lp"),
            "ATP": quote.get("atp"),
            "Bid": quote.get("bid"),
            "Ask": quote.get("ask"),
            "Volume": quote.get("volume")
        })

    st.dataframe(
        pd.DataFrame(raw_rows),
        use_container_width=True,
        hide_index=True
    )

# --- Strategy formula ------------------------------------------------------
with st.expander("Strategy Formula"):
    st.latex(r"CE_{dev}=CE_{LTP}-CE_{ATP}")
    st.latex(r"PE_{dev}=PE_{LTP}-PE_{ATP}")
    st.latex(
        r"AvgCEDev="
        r"\frac{CEDev_{SynATM-1}+CEDev_{SynATM}+CEDev_{SynATM+1}}{3}"
    )
    st.latex(
        r"AvgPEDev="
        r"\frac{PEDev_{SynATM-1}+PEDev_{SynATM}+PEDev_{SynATM+1}}{3}"
    )
    st.latex(r"SynFut_{K}=K+CE_{LTP,K}-PE_{LTP,K}")
    st.latex(
        r"SynFut=\frac{SynFut_{SpotATM-1}+SynFut_{SpotATM}+SynFut_{SpotATM+1}}{3}"
    )
    st.latex(r"Syn\ ATM=\text{listed strike closest to }SynFut")
    st.latex(r"Straddle=CE_{LTP,SynATM}+PE_{LTP,SynATM}")
    st.latex(r"Upper\ BE=SynFut+Straddle")
    st.latex(r"Lower\ BE=SynFut-Straddle")
    st.latex(
        r"CE\ Share_{K}=\frac{CE_{LTP,K}}{CE_{LTP,K}+PE_{LTP,K}}"
        r",\quad "
        r"PE\ Share_{K}=\frac{PE_{LTP,K}}{CE_{LTP,K}+PE_{LTP,K}}"
    )
    st.latex(r"Theta\ Ratio=\frac{|\Theta|}{|\Delta|}")

    st.write(
        "Buy signal: Avg CE Dev > 0, Avg PE Dev < 0, "
        "and abs(Avg PE Dev) > 1.5 × abs(Avg CE Dev)."
    )
    st.write(
        "Buy signal suggests shorting the PE at the strike closest to the "
        "Lower BE."
    )
    st.write(
        "Sell signal: Avg PE Dev > 0, Avg CE Dev < 0, "
        "and abs(Avg CE Dev) > 1.5 × abs(Avg PE Dev)."
    )
    st.write(
        "Sell signal suggests shorting the CE at the strike closest to the "
        "Upper BE."
    )
    st.write(
        "All values are informational signals only. No orders are sent to "
        "Fyers."
    )

if st.button("Manual Refresh"):
    st.rerun()
