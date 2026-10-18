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
# Fyers API calls
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
    """Fetch quotes in batches of 50 (Fyers API limit)."""
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        return {}
    quotes = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        response = fyers.quotes({"symbols": ",".join(batch)})
        if response.get("s") != "ok":
            raise RuntimeError(f"Option quotes failed: {response}")
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
                "volume": value.get("volume"),
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
            f"cannot be the centre of a 3-strike window "
            f"(loaded range {int(strikes[0]):,}–{int(strikes[-1]):,}). "
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
        value=15,
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

with st.sidebar:
    st.divider()
    st.subheader("Expiry")

    expiry_index = st.selectbox(
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

# --- Step 5: raw OTM CE and OTM PE windows --------------------------------
all_loaded_strikes = both_leg_strikes(option_map)

otm_ce_strikes_raw = [
    s for s in all_loaded_strikes if atm < s <= upper_strike
]
otm_pe_strikes_raw = [
    s for s in all_loaded_strikes if lower_strike <= s < atm
]

if not otm_ce_strikes_raw:
    st.error(
        f"No OTM CE strikes between Syn ATM ({atm:,.0f}) and Upper BE strike "
        f"({upper_strike:,.0f}). Increase Strike Count or check the chain."
    )
    st.stop()

if not otm_pe_strikes_raw:
    st.error(
        f"No OTM PE strikes between Lower BE strike ({lower_strike:,.0f}) and "
        f"Syn ATM ({atm:,.0f}). Increase Strike Count or check the chain."
    )
    st.stop()

# --- Step 5.5: match counts — min(N_CE, N_PE) strikes on each side --------
n_ce_raw = len(otm_ce_strikes_raw)
n_pe_raw = len(otm_pe_strikes_raw)
n_common = min(n_ce_raw, n_pe_raw)

# otm_ce_strikes_raw is ascending; innermost = first N (smallest = closest to ATM)
otm_ce_strikes = sorted(otm_ce_strikes_raw)[:n_common]

# otm_pe_strikes_raw is ascending; innermost = last N (largest = closest to ATM)
otm_pe_strikes = sorted(otm_pe_strikes_raw, reverse=True)[:n_common]
otm_pe_strikes = sorted(otm_pe_strikes)  # re-sort ascending for display

dropped_ce = [s for s in otm_ce_strikes_raw if s not in otm_ce_strikes]
dropped_pe = [s for s in otm_pe_strikes_raw if s not in otm_pe_strikes]

otm_role_map = {s: "OTM CE" for s in otm_ce_strikes}
otm_role_map.update({s: "OTM PE" for s in otm_pe_strikes})

all_otm_strikes = sorted(otm_ce_strikes + otm_pe_strikes)

# --- Step 6: fetch quotes for all (trimmed) OTM strikes -------------------
otm_symbols = []
for strike in all_otm_strikes:
    otm_symbols.append(option_map[strike]["CE"])
    otm_symbols.append(option_map[strike]["PE"])

missing_otm_symbols = [
    s for s in dict.fromkeys(otm_symbols)
    if s not in quotes
]

if missing_otm_symbols:
    trade_symbols.extend(missing_otm_symbols)
    try:
        quotes.update(fetch_option_quotes(fyers, missing_otm_symbols))
    except Exception as e:
        st.error(f"OTM strike quotes fetch failed: {e}")
        st.stop()

# --- Step 7: OTM-based deviations + share --------------------------------
otm_dev_rows = []
otm_share_rows = []

for strike in all_otm_strikes:
    role = otm_role_map[strike]
    ce_sym = option_map[strike]["CE"]
    pe_sym = option_map[strike]["PE"]

    ce_q = quotes.get(ce_sym)
    pe_q = quotes.get(pe_sym)

    if not ce_q or not pe_q:
        st.error(f"Missing quote for OTM strike {strike:,.0f}")
        st.stop()

    if not (
        valid_number(ce_q.get("lp")) and valid_number(ce_q.get("atp"))
        and valid_number(pe_q.get("lp")) and valid_number(pe_q.get("atp"))
    ):
        st.error(f"Invalid quote data at OTM strike {strike:,.0f}")
        st.stop()

    ce_ltp = float(ce_q["lp"])
    ce_atp = float(ce_q["atp"])
    pe_ltp = float(pe_q["lp"])
    pe_atp = float(pe_q["atp"])

    otm_dev_rows.append({
        "Role": role,
        "Strike": strike,
        "CE ATP": ce_atp,
        "CE LTP": ce_ltp,
        "CE LTP-ATP": ce_ltp - ce_atp,
        "PE LTP": pe_ltp,
        "PE ATP": pe_atp,
        "PE LTP-ATP": pe_ltp - pe_atp,
    })

    total = ce_ltp + pe_ltp
    otm_share_rows.append({
        "Role": role,
        "Strike": strike,
        "CE LTP": ce_ltp,
        "PE LTP": pe_ltp,
        "CE / (CE+PE) %": (ce_ltp / total * 100) if total > 0 else float("nan"),
        "PE / (CE+PE) %": (pe_ltp / total * 100) if total > 0 else float("nan"),
    })

otm_dev_df = pd.DataFrame(otm_dev_rows)
otm_share_df = pd.DataFrame(otm_share_rows)

# Greeks map from full chain — build theta rows
greeks_map = build_greeks_map(chain_rows)

otm_theta_rows = []
for strike in all_otm_strikes:
    role = otm_role_map[strike]
    ce_sym = option_map[strike]["CE"]
    pe_sym = option_map[strike]["PE"]

    ce_g = greeks_map.get(ce_sym, {})
    pe_g = greeks_map.get(pe_sym, {})

    ce_theta = ce_g.get("theta")
    ce_delta = ce_g.get("delta")
    pe_theta = pe_g.get("theta")
    pe_delta = pe_g.get("delta")

    otm_theta_rows.append({
        "Role": role,
        "Strike": strike,
        "CE Theta": ce_theta,
        "CE Delta": ce_delta,
        "CE |Θ| / |Δ|": theta_ratio(ce_theta, ce_delta),
        "PE Theta": pe_theta,
        "PE Delta": pe_delta,
        "PE |Θ| / |Δ|": theta_ratio(pe_theta, pe_delta),
    })

otm_theta_df = pd.DataFrame(otm_theta_rows)

# --- Step 8: OTM-based averages (CE over OTM CE, PE over OTM PE) ----------
ce_dev_values = otm_dev_df.loc[
    otm_dev_df["Role"] == "OTM CE", "CE LTP-ATP"
].tolist()
pe_dev_values = otm_dev_df.loc[
    otm_dev_df["Role"] == "OTM PE", "PE LTP-ATP"
].tolist()
average_ce_deviation = float(sum(ce_dev_values) / len(ce_dev_values))
average_pe_deviation = float(sum(pe_dev_values) / len(pe_dev_values))

ce_theta_values = otm_theta_df.loc[
    otm_theta_df["Role"] == "OTM CE", "CE |Θ| / |Δ|"
].dropna().tolist()
pe_theta_values = otm_theta_df.loc[
    otm_theta_df["Role"] == "OTM PE", "PE |Θ| / |Δ|"
].dropna().tolist()
avg_ce_theta_ratio = (
    float(sum(ce_theta_values) / len(ce_theta_values))
    if ce_theta_values else float("nan")
)
avg_pe_theta_ratio = (
    float(sum(pe_theta_values) / len(pe_theta_values))
    if pe_theta_values else float("nan")
)

ce_share_values = otm_share_df.loc[
    otm_share_df["Role"] == "OTM CE", "CE / (CE+PE) %"
].dropna().tolist()
pe_share_values = otm_share_df.loc[
    otm_share_df["Role"] == "OTM PE", "PE / (CE+PE) %"
].dropna().tolist()
avg_ce_share = (
    float(sum(ce_share_values) / len(ce_share_values))
    if ce_share_values else float("nan")
)
avg_pe_share = (
    float(sum(pe_share_values) / len(pe_share_values))
    if pe_share_values else float("nan")
)

# --- Syn ATM-window synthetic future (display only) -----------------------
syn_df_final, syn_fut_final = calculate_synthetic_future(
    dev_strikes,
    option_map,
    quotes
)

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] { font-size: 22px !important; }
    [data-testid="stMetricLabel"] p { font-size: 13px !important; }
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

# --- Truncation info box ---------------------------------------------------
if dropped_ce or dropped_pe:
    dropped_bits = []
    if dropped_ce:
        dropped_bits.append(
            f"dropped OTM CE strike(s) {[int(s) for s in dropped_ce]} "
            f"(outermost, nearest Upper BE strike)"
        )
    if dropped_pe:
        dropped_bits.append(
            f"dropped OTM PE strike(s) {[int(s) for s in dropped_pe]} "
            f"(outermost, nearest Lower BE strike)"
        )
    st.info(
        f"OTM strike counts matched to **N = {n_common} per side** "
        f"(raw: {n_ce_raw} OTM CE / {n_pe_raw} OTM PE). Kept the innermost "
        f"N strikes on each side (closest to Syn ATM {int(atm):,}). "
        f"{' · '.join(dropped_bits)}."
    )

# --- Metrics --------------------------------------------------------------
row1 = st.columns(3)
row1[0].metric("Spot", f"{spot:,.2f}")
row1[1].metric("Syn Future", f"{syn_fut:,.2f}")
row1[2].metric("Syn Fut @ Syn ATM", f"{syn_fut_final:,.2f}")

row2 = st.columns(3)
row2[0].metric("Syn ATM", f"{atm:,.0f}")
row2[1].metric("Straddle @ Syn ATM", f"{atm_straddle:,.2f}")
row2[2].metric("Upper BE Strike", f"{upper_strike:,.2f}")

row3 = st.columns(3)
row3[0].metric("Lower BE Strike", f"{lower_strike:,.2f}")
row3[1].metric("Avg CE Dev.", f"{average_ce_deviation:+,.2f}")
row3[2].metric("Avg PE Dev.", f"{average_pe_deviation:+,.2f}")

row4 = st.columns(3)
row4[0].metric(
    "Avg CE |Θ| / |Δ|",
    "n/a" if pd.isna(avg_ce_theta_ratio) else f"{avg_ce_theta_ratio:.4f}"
)
row4[1].metric(
    "Avg PE |Θ| / |Δ|",
    "n/a" if pd.isna(avg_pe_theta_ratio) else f"{avg_pe_theta_ratio:.4f}"
)
row4[2].metric(
    "Avg CE / (CE + PE)",
    "n/a" if pd.isna(avg_ce_share) else f"{avg_ce_share:.1f}%"
)

row5 = st.columns(3)
row5[0].metric(
    "Avg PE / (CE + PE)",
    "n/a" if pd.isna(avg_pe_share) else f"{avg_pe_share:.1f}%"
)
row5[1].metric("OTM Strikes / Side", str(n_common))

# --- OTM window summary ---------------------------------------------------
st.caption(
    f"**OTM CE** ({len(otm_ce_strikes)}): "
    f"{[int(s) for s in otm_ce_strikes]}  ·  "
    f"**OTM PE** ({len(otm_pe_strikes)}): "
    f"{[int(s) for s in otm_pe_strikes]}"
)

# --- Synthetic Future — Syn ATM window ------------------------------------
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
    "The breakevens use the bootstrap average (from the spot ATM window). "
    "This table is kept for context; the OTM-based calculations below do not "
    "use it."
)

# --- Breakeven Strike Selection -------------------------------------------
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

# --- CE / (CE+PE) at OTM strikes ------------------------------------------
st.subheader(
    "CE / (CE + PE) & PE / (CE + PE) at OTM Strikes"
)

st.dataframe(otm_share_df, use_container_width=True, hide_index=True)

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
    f"Avg CE share = mean of CE/(CE+PE) over the **{len(otm_ce_strikes)}** "
    f"OTM CE strikes. Avg PE share = mean of PE/(CE+PE) over the "
    f"**{len(otm_pe_strikes)}** OTM PE strikes (counts matched to N="
    f"{n_common}). Rows are marked OTM CE / OTM PE in the Role column."
)

# --- |Theta| / |Delta| at OTM strikes -------------------------------------
st.subheader("|Theta| / |Delta| at OTM Strikes")

st.dataframe(otm_theta_df, use_container_width=True, hide_index=True)

th = st.columns(2)
th[0].metric(
    "Avg CE |Θ| / |Δ|",
    "n/a" if pd.isna(avg_ce_theta_ratio) else f"{avg_ce_theta_ratio:.4f}"
)
th[1].metric(
    "Avg PE |Θ| / |Δ|",
    "n/a" if pd.isna(avg_pe_theta_ratio) else f"{avg_pe_theta_ratio:.4f}"
)

st.caption(
    f"Avg CE |Θ|/|Δ| = mean over the **{len(otm_ce_strikes)}** OTM CE strikes. "
    f"Avg PE |Θ|/|Δ| = mean over the **{len(otm_pe_strikes)}** OTM PE strikes. "
    f"Ratio = abs(Theta) / abs(Delta) from the same chain snapshot."
)

# --- CE / PE LTP − ATP at OTM strikes -------------------------------------
st.subheader("CE / PE LTP − ATP at OTM Strikes")

st.dataframe(otm_dev_df, use_container_width=True, hide_index=True)

dv = st.columns(2)
dv[0].metric("Avg CE LTP−ATP", f"{average_ce_deviation:+,.2f}")
dv[1].metric("Avg PE LTP−ATP", f"{average_pe_deviation:+,.2f}")

st.caption(
    f"Avg CE LTP−ATP = mean over the **{len(otm_ce_strikes)}** OTM CE strikes. "
    f"Avg PE LTP−ATP = mean over the **{len(otm_pe_strikes)}** OTM PE strikes. "
    f"These are the same values shown as Avg CE Dev. / Avg PE Dev. in the "
    f"top metrics."
)

# --- Calculation breakdown ------------------------------------------------
st.subheader("Calculation")

ce_dev_df = otm_dev_df[otm_dev_df["Role"] == "OTM CE"]
pe_dev_df = otm_dev_df[otm_dev_df["Role"] == "OTM PE"]

if not ce_dev_df.empty:
    st.write("**OTM CE Strikes (CE LTP − CE ATP):**")
    for _, row in ce_dev_df.iterrows():
        st.write(
            f"  {row['Strike']:,.0f}: "
            f"{row['CE LTP']:,.2f} − {row['CE ATP']:,.2f} = "
            f"**{row['CE LTP-ATP']:+,.2f}**"
        )
    ce_vals_text = " + ".join(
        f"({x:+,.2f})" for x in ce_dev_df["CE LTP-ATP"].tolist()
    )
    st.write(
        f"**Avg CE Dev:** ({ce_vals_text}) / {len(ce_dev_df)} = "
        f"**{average_ce_deviation:+,.2f}**"
    )

if not pe_dev_df.empty:
    st.write("**OTM PE Strikes (PE LTP − PE ATP):**")
    for _, row in pe_dev_df.iterrows():
        st.write(
            f"  {row['Strike']:,.0f}: "
            f"{row['PE LTP']:,.2f} − {row['PE ATP']:,.2f} = "
            f"**{row['PE LTP-ATP']:+,.2f}**"
        )
    pe_vals_text = " + ".join(
        f"({x:+,.2f})" for x in pe_dev_df["PE LTP-ATP"].tolist()
    )
    st.write(
        f"**Avg PE Dev:** ({pe_vals_text}) / {len(pe_dev_df)} = "
        f"**{average_pe_deviation:+,.2f}**"
    )

ce_share_df = otm_share_df[otm_share_df["Role"] == "OTM CE"]
pe_share_df = otm_share_df[otm_share_df["Role"] == "OTM PE"]

if not ce_share_df.empty:
    st.write("**OTM CE Strikes (CE / (CE + PE)):**")
    for _, row in ce_share_df.iterrows():
        st.write(
            f"  {row['Strike']:,.0f}: "
            f"{row['CE LTP']:,.2f} / "
            f"({row['CE LTP']:,.2f} + {row['PE LTP']:,.2f}) = "
            f"**{row['CE / (CE+PE) %']:.1f}%**"
        )
    ce_share_text = " + ".join(
        f"{x:.1f}%" for x in ce_share_df["CE / (CE+PE) %"].tolist()
    )
    st.write(
        f"**Avg CE / (CE + PE):** ({ce_share_text}) / {len(ce_share_df)} = "
        f"**{avg_ce_share:.1f}%**"
    )

if not pe_share_df.empty:
    st.write("**OTM PE Strikes (PE / (CE + PE)):**")
    for _, row in pe_share_df.iterrows():
        st.write(
            f"  {row['Strike']:,.0f}: "
            f"{row['PE LTP']:,.2f} / "
            f"({row['CE LTP']:,.2f} + {row['PE LTP']:,.2f}) = "
            f"**{row['PE / (CE+PE) %']:.1f}%**"
        )
    pe_share_text = " + ".join(
        f"{x:.1f}%" for x in pe_share_df["PE / (CE+PE) %"].tolist()
    )
    st.write(
        f"**Avg PE / (CE + PE):** ({pe_share_text}) / {len(pe_share_df)} = "
        f"**{avg_pe_share:.1f}%**"
    )

# --- Raw quotes ----------------------------------------------------------
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

# --- Strategy formula ----------------------------------------------------
with st.expander("Strategy Formula"):
    st.latex(r"CE_{dev}=CE_{LTP}-CE_{ATP}")
    st.latex(r"PE_{dev}=PE_{LTP}-PE_{ATP}")
    st.latex(
        r"OTM\ CE^{raw}=\{K: SynATM<K\leq UpperBE\ Strike\}"
    )
    st.latex(
        r"OTM\ PE^{raw}=\{K: LowerBE\ Strike\leq K<SynATM\}"
    )
    st.latex(
        r"N=\min(N_{CE}^{raw},\ N_{PE}^{raw})"
    )
    st.latex(
        r"OTM\ CE = \text{innermost }N\text{ strikes of }OTM\ CE^{raw}"
        r"\quad(\text{closest to }SynATM)"
    )
    st.latex(
        r"OTM\ PE = \text{innermost }N\text{ strikes of }OTM\ PE^{raw}"
        r"\quad(\text{closest to }SynATM)"
    )
    st.latex(
        r"AvgCEDev=\frac{1}{N}\sum_{K\in OTM\ CE}(CE_{LTP,K}-CE_{ATP,K})"
    )
    st.latex(
        r"AvgPEDev=\frac{1}{N}\sum_{K\in OTM\ PE}(PE_{LTP,K}-PE_{ATP,K})"
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
    st.latex(
        r"Avg\ CE\ Share=\frac{1}{N}\sum_{K\in OTM\ CE}CE\ Share_{K}"
    )
    st.latex(
        r"Avg\ PE\ Share=\frac{1}{N}\sum_{K\in OTM\ PE}PE\ Share_{K}"
    )
    st.latex(r"Theta\ Ratio_{K}=\frac{|\Theta_{K}|}{|\Delta_{K}|}")
    st.latex(
        r"Avg\ CE\ Theta\ Ratio=\frac{1}{N}"
        r"\sum_{K\in OTM\ CE}\frac{|\Theta_{CE,K}|}{|\Delta_{CE,K}|}"
    )
    st.latex(
        r"Avg\ PE\ Theta\ Ratio=\frac{1}{N}"
        r"\sum_{K\in OTM\ PE}\frac{|\Theta_{PE,K}|}{|\Delta_{PE,K}|}"
    )

    st.write(
        "OTM CE strikes = all both-leg strikes strictly above the Syn ATM, "
        "up to and including the Upper BE strike."
    )
    st.write(
        "OTM PE strikes = all both-leg strikes strictly below the Syn ATM, "
        "down to and including the Lower BE strike."
    )
    st.write(
        "**Count matching:** N = min(N_CE, N_PE). The longer side is "
        "trimmed to its innermost N strikes (the ones closest to the Syn "
        "ATM), so both sides contribute the same number of data points to "
        "every average."
    )
    st.write(
        "All values are informational signals only. No orders are sent to "
        "Fyers."
    )

if st.button("Manual Refresh"):
    st.rerun()
