import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fyers_apiv3 import fyersModel
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="NIFTY CE/PE ATP Paper Trade",
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

    response = fyers.quotes({
        "symbols": ",".join(symbols)
    })

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
    data = chain_response.get("data", {})
    return data.get("expiryData", [])

def get_option_chain_rows(chain_response):
    data = chain_response.get("data", {})
    return data.get("optionsChain", [])

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

        if strike not in option_map:
            option_map[strike] = {}

        option_map[strike][option_type] = symbol

    return option_map

def build_greeks_map(chain):
    """LTP, delta and theta all taken from the same chain row,
    so they always belong to the same snapshot."""
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

def select_three_strikes(option_map, spot):
    strikes = sorted(option_map.keys())

    if not strikes:
        raise RuntimeError("No option strikes found in option chain.")

    atm_index = min(
        range(len(strikes)),
        key=lambda i: abs(strikes[i] - spot)
    )

    if atm_index == 0 or atm_index == len(strikes) - 1:
        raise RuntimeError(
            "Unable to select ATM-1, ATM and ATM+1 from the available strikes."
        )

    selected = strikes[atm_index - 1:atm_index + 2]

    for strike in selected:
        if (
            "CE" not in option_map[strike]
            or "PE" not in option_map[strike]
        ):
            raise RuntimeError(
                f"Both CE and PE are not available for strike {strike}."
            )

    return selected, strikes[atm_index]

def calculate_synthetic_future(selected_strikes, option_map, quotes):
    """Synthetic future per strike = Strike + CE LTP - PE LTP.
    Returns a per-strike table and the average of the synthetic futures."""
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

def find_synthetic_atm(option_map, syn_fut):
    """Listed strike (with both CE and PE) closest to the synthetic future."""
    candidates = sorted(
        strike for strike, legs in option_map.items()
        if "CE" in legs and "PE" in legs
    )

    if not candidates:
        raise RuntimeError(
            "No strike with both CE and PE available for synthetic ATM."
        )

    return min(candidates, key=lambda strike: abs(strike - syn_fut))

def select_three_around(option_map, center_strike):
    """Listed strikes (both CE and PE available) just below, at and just
    above center_strike."""
    strikes = sorted(
        strike for strike, legs in option_map.items()
        if "CE" in legs and "PE" in legs
    )

    index = strikes.index(center_strike)

    if index == 0 or index == len(strikes) - 1:
        raise RuntimeError(
            "Unable to select Syn ATM-1, Syn ATM and Syn ATM+1 "
            "from the available strikes."
        )

    return strikes[index - 1:index + 2]

def premium_share(ce_ltp, pe_ltp):
    """CE / (CE + PE) and PE / (CE + PE). (None, None) if not computable."""
    if not (valid_number(ce_ltp) and valid_number(pe_ltp)):
        return None, None

    total = float(ce_ltp) + float(pe_ltp)

    if total <= 0:
        return None, None

    return float(ce_ltp) / total, float(pe_ltp) / total

def find_breakeven_strikes(option_map, center, straddle):
    strikes = sorted(option_map.keys())

    if not strikes:
        raise RuntimeError(
            "No strikes available for breakeven calculation."
        )

    upper_breakeven = center + straddle
    lower_breakeven = center - straddle

    upper_strike = min(
        strikes,
        key=lambda strike: abs(strike - upper_breakeven)
    )

    lower_strike = min(
        strikes,
        key=lambda strike: abs(strike - lower_breakeven)
    )

    if "CE" not in option_map[upper_strike]:
        raise RuntimeError(
            f"CE unavailable at upper breakeven strike {upper_strike}."
        )

    if "PE" not in option_map[lower_strike]:
        raise RuntimeError(
            f"PE unavailable at lower breakeven strike {lower_strike}."
        )

    return (
        upper_breakeven,
        lower_breakeven,
        upper_strike,
        lower_strike
    )

def valid_number(value):
    if value is None:
        return False

    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False

def theta_ratio(theta, ltp, delta):
    if not all(valid_number(x) for x in (theta, ltp, delta)):
        return None

    # denominator = float(ltp) * abs(float(delta))
    denominator =  abs(float(delta))

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

        if not all(
            valid_number(x)
            for x in [ce_ltp, ce_atp, pe_ltp, pe_atp]
        ):
            raise RuntimeError(
                f"Invalid quote data for strike {strike}: "
                f"CE LTP={ce_ltp}, CE ATP={ce_atp}, "
                f"PE LTP={pe_ltp}, PE ATP={pe_atp}"
            )

        ce_ltp = float(ce_ltp)
        ce_atp = float(ce_atp)
        pe_ltp = float(pe_ltp)
        pe_atp = float(pe_atp)

        ce_deviation = ce_ltp - ce_atp
        pe_deviation = pe_ltp - pe_atp

        rows.append({
            "Strike": strike,
            "CE Symbol": ce_symbol,
            "CE ATP": ce_atp,
            "CE LTP": ce_ltp,
            "CE LTP-ATP": ce_deviation,
            "PE Symbol": pe_symbol,
            "PE LTP": pe_ltp,
            "PE ATP": pe_atp,
            "PE LTP-ATP": pe_deviation
        })

    result = (
        pd.DataFrame(rows)
        .sort_values("Strike")
        .reset_index(drop=True)
    )

    average_ce_deviation = result["CE LTP-ATP"].mean()
    average_pe_deviation = result["PE LTP-ATP"].mean()

    return (
        result,
        float(average_ce_deviation),
        float(average_pe_deviation)
    )


# ---------------------------------------------------------------------------
# Time / refresh helpers
# ---------------------------------------------------------------------------

def get_ist_time():
    return datetime.now(
        ZoneInfo("Asia/Kolkata")
    )

def format_ist_time(dt):
    if isinstance(dt, str):
        return dt
    return dt.strftime(
        "%d-%b-%Y %I:%M:%S %p IST"
    )

def ms_until_next_boundary(step_seconds, buffer_ms=300):
    """Milliseconds until the next wall-clock multiple of step_seconds,
    measured from IST midnight. Examples:
      15s -> :00, :15, :30, :45 of every minute
      60s -> top of every minute (10:00, 10:01, 10:02 ...)
    A small buffer makes the rerun land just after the boundary so the
    fresh candle/quote data is already available."""
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
# Paper trade state
# ---------------------------------------------------------------------------

def initialize_paper_state():
    if "paper_trade" not in st.session_state:
        st.session_state.paper_trade = None

    if "trade_log" not in st.session_state:
        st.session_state.trade_log = []

    if "manual_close_requested" not in st.session_state:
        st.session_state.manual_close_requested = False

    if st.session_state.paper_trade is not None:
        trade = st.session_state.paper_trade

        quantity = int(
            trade.get("quantity", 65)
        )

        lot_size = int(
            trade.get("lot_size", 65)
        )

        lots = int(
            trade.get(
                "lots",
                max(
                    1,
                    round(quantity / lot_size)
                )
            )
        )

        trade["quantity"] = quantity
        trade["lot_size"] = lot_size
        trade["lots"] = lots

        if "signal" not in trade:
            trade["signal"] = ""

        if "action" not in trade:
            option_type = trade.get("option_type", "")
            trade["action"] = f"SELL {option_type}"

        if "entry_price" not in trade:
            trade["entry_price"] = 0.0

        if "entry_spot" not in trade:
            trade["entry_spot"] = 0.0

        if "entry_avg_ce_dev" not in trade:
            trade["entry_avg_ce_dev"] = 0.0

        if "entry_avg_pe_dev" not in trade:
            trade["entry_avg_pe_dev"] = 0.0

def check_buy_signal(avg_ce_dev, avg_pe_dev):
    return (
        avg_ce_dev > 0
        and avg_pe_dev < 0
        and abs(avg_pe_dev) > 1.5 * abs(avg_ce_dev)
    )

def check_sell_signal(avg_ce_dev, avg_pe_dev):
    return (
        avg_pe_dev > 0
        and avg_ce_dev < 0
        and abs(avg_ce_dev) > 1.5 * abs(avg_pe_dev)
    )

def check_exit_signal(trade, avg_ce_dev, avg_pe_dev):
    if trade is None:
        return False

    option_type = trade.get("option_type")

    if option_type == "PE":
        return abs(avg_ce_dev) >= abs(avg_pe_dev)

    if option_type == "CE":
        return abs(avg_pe_dev) >= abs(avg_ce_dev)

    return False

def enter_paper_trade(
    signal,
    option_type,
    strike,
    symbol,
    entry_price,
    avg_ce_dev,
    avg_pe_dev,
    spot,
    lots,
    lot_size,
    upper_breakeven,
    lower_breakeven
):
    lots = int(lots)
    lot_size = int(lot_size)
    quantity = lots * lot_size

    st.session_state.paper_trade = {
        "signal": signal,
        "action": f"SELL {option_type}",
        "option_type": option_type,
        "strike": float(strike),
        "symbol": symbol,
        "lots": lots,
        "lot_size": lot_size,
        "quantity": quantity,
        "entry_price": float(entry_price),
        "entry_time": get_ist_time(),
        "entry_avg_ce_dev": float(avg_ce_dev),
        "entry_avg_pe_dev": float(avg_pe_dev),
        "entry_spot": float(spot),
        "entry_upper_breakeven": float(upper_breakeven),
        "entry_lower_breakeven": float(lower_breakeven)
    }

def exit_paper_trade(
    current_price,
    avg_ce_dev,
    avg_pe_dev,
    spot
):
    trade = st.session_state.paper_trade

    if trade is None:
        return

    exit_time = get_ist_time()

    entry_price = float(
        trade.get("entry_price", 0)
    )

    quantity = int(
        trade.get("quantity", 65)
    )

    lot_size = int(
        trade.get("lot_size", 65)
    )

    lots = int(
        trade.get(
            "lots",
            max(
                1,
                round(quantity / lot_size)
            )
        )
    )

    current_price = float(current_price)

    pnl = (
        entry_price - current_price
    ) * quantity

    trade_record = {
        "Signal": trade.get("signal", ""),
        "Action": trade.get("action", ""),
        "Option": trade.get("option_type", ""),
        "Strike": trade.get("strike", ""),
        "Lots": lots,
        "Qty": quantity,
        "Entry Time": format_ist_time(
            trade.get("entry_time", exit_time)
        ),
        "Entry Price": entry_price,
        "Exit Time": format_ist_time(
            exit_time
        ),
        "Exit Price": current_price,
        "P&L": float(pnl),
        "Entry Spot": float(
            trade.get("entry_spot", 0)
        ),
        "Exit Spot": float(spot),
        "Entry Avg CE Dev": float(
            trade.get("entry_avg_ce_dev", 0)
        ),
        "Entry Avg PE Dev": float(
            trade.get("entry_avg_pe_dev", 0)
        ),
        "Exit Avg CE Dev": float(avg_ce_dev),
        "Exit Avg PE Dev": float(avg_pe_dev),
        "Entry Upper BE": trade.get(
            "entry_upper_breakeven"
        ),
        "Entry Lower BE": trade.get(
            "entry_lower_breakeven"
        ),
        "Symbol": trade.get("symbol", ""),
        "Lot Size": lot_size
    }

    st.session_state.trade_log.append(
        trade_record
    )

    st.session_state.paper_trade = None
    st.session_state.manual_close_requested = False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

initialize_paper_state()

st.title("NIFTY CE/PE ATP Paper Trade")

with st.sidebar:
    st.header("Settings")

    client_id = st.text_input(
        "Client ID",
        type="default"
    )

    access_token = st.text_input(
        "Access Token",
        type="password"
    )

    underlying = st.text_input(
        "Underlying",
        value="NSE:NIFTY50-INDEX"
    )

    strikecount = st.number_input(
        "Strike Count",
        min_value=1,
        max_value=50,
        value=10,
        step=1
    )

    lots = st.number_input(
        "Lots",
        min_value=1,
        max_value=50,
        value=1,
        step=1
    )

    lot_size = 65

    st.caption(
        f"Quantity: {int(lots * lot_size)} "
        f"({int(lots)} × {lot_size})"
    )

    autorefresh_enabled = st.checkbox(
        "Auto Refresh",
        value=True
    )

    refresh_seconds = st.number_input(
        "Refresh Every (seconds)",
        min_value=1,
        max_value=3600,
        value=15,
        step=1
    )

    st.divider()

    if st.button(
        "Close Paper Position",
        use_container_width=True,
        disabled=st.session_state.paper_trade is None
    ):
        st.session_state.manual_close_requested = True
        st.rerun()

    if st.button(
        "Clear Trade History",
        use_container_width=True
    ):
        st.session_state.trade_log = []
        st.rerun()

install_autorefresh(
    autorefresh_enabled,
    refresh_seconds
)

last_refreshed = get_ist_time()

st.caption(
    f"Last refreshed: {format_ist_time(last_refreshed)}"
)

if not client_id or not access_token:
    st.info(
        "Enter Client ID and Access Token in the sidebar."
    )
    st.stop()

try:
    fyers = get_fyers(
        client_id,
        access_token
    )
except Exception as e:
    st.error(
        f"Fyers initialization failed: {e}"
    )
    st.stop()

try:
    initial_chain = fetch_chain(
        fyers,
        underlying,
        int(strikecount),
        0
    )
except Exception as e:
    st.error(
        f"Option chain fetch failed: {e}"
    )
    st.stop()

expiry_data = get_expiry_data(
    initial_chain
)

if not expiry_data:
    st.error(
        "No expiry data returned by the option chain."
    )
    st.stop()

expiry_labels = []
expiry_values = []

for item in expiry_data:
    expiry_labels.append(
        format_expiry(item)
    )

    expiry_values.append(
        get_expiry_value(item)
    )

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
    st.error(
        f"Option chain fetch failed: {e}"
    )
    st.stop()

try:
    spot = fetch_spot(
        fyers,
        underlying
    )
except Exception as e:
    st.error(
        f"Spot fetch failed: {e}"
    )
    st.stop()

chain_rows = get_option_chain_rows(
    chain_response
)

if not chain_rows:
    st.error(
        "No option chain rows returned."
    )
    st.stop()

option_map = build_option_map(
    chain_rows
)

try:
    selected_strikes, spot_atm = select_three_strikes(
        option_map,
        spot
    )
except Exception as e:
    st.error(str(e))
    st.stop()

# --- Step 1: quotes for spot ATM-1, ATM, ATM+1 -----------------------------
trade_symbols = []

for strike in selected_strikes:
    trade_symbols.append(option_map[strike]["CE"])
    trade_symbols.append(option_map[strike]["PE"])

try:
    quotes = fetch_option_quotes(
        fyers,
        tuple(trade_symbols)
    )
except Exception as e:
    st.error(
        f"Option quotes fetch failed: {e}"
    )
    st.stop()

# --- Step 2: synthetic future = avg(Strike + CE LTP - PE LTP) ----------------
try:
    syn_df, syn_fut = calculate_synthetic_future(
        selected_strikes,
        option_map,
        quotes
    )

    # `atm` below = listed strike closest to the synthetic future
    atm = find_synthetic_atm(
        option_map,
        syn_fut
    )

    # strikes used for Avg CE/PE Dev = Syn ATM-1, Syn ATM, Syn ATM+1
    dev_strikes = select_three_around(
        option_map,
        atm
    )
except Exception as e:
    st.error(str(e))
    st.stop()

# --- Step 3: straddle at that strike ------------------------------------------
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
        quotes.update(
            fetch_option_quotes(
                fyers,
                tuple(missing_dev_symbols)
            )
        )
    except Exception as e:
        st.error(
            f"Synthetic ATM option quotes fetch failed: {e}"
        )
        st.stop()

atm_ce_quote = quotes.get(
    atm_ce_symbol
)

atm_pe_quote = quotes.get(
    atm_pe_symbol
)

if not atm_ce_quote or not atm_pe_quote:
    st.error(
        "Synthetic ATM CE/PE quotes unavailable."
    )
    st.stop()

atm_ce_ltp = atm_ce_quote.get("lp")
atm_pe_ltp = atm_pe_quote.get("lp")

if (
    not valid_number(atm_ce_ltp)
    or not valid_number(atm_pe_ltp)
):
    st.error(
        f"Invalid synthetic ATM option prices: "
        f"CE={atm_ce_ltp}, PE={atm_pe_ltp}"
    )
    st.stop()

atm_ce_ltp = float(atm_ce_ltp)
atm_pe_ltp = float(atm_pe_ltp)

atm_straddle = (
    atm_ce_ltp +
    atm_pe_ltp
)

# --- Step 4: breakevens = synthetic future +/- straddle ---------------------
try:
    (
        upper_breakeven,
        lower_breakeven,
        upper_strike,
        lower_strike
    ) = find_breakeven_strikes(
        option_map,
        syn_fut,
        atm_straddle
    )
except Exception as e:
    st.error(str(e))
    st.stop()

# Upper BE CE and lower BE PE data only
be_legs = []

for be_label, be_strike, opt in (
    ("Upper BE", upper_strike, "CE"),
    ("Lower BE", lower_strike, "PE")
):
    sym = option_map[be_strike].get(opt)
    if sym:
        be_legs.append((be_label, be_strike, opt, sym))

# --- Step 5: quotes still needed (breakeven legs + any open paper trade) -----
extra_symbols = [
    option_map[upper_strike]["CE"],
    option_map[lower_strike]["PE"]
]

if st.session_state.paper_trade is not None:
    open_trade_symbol = st.session_state.paper_trade.get(
        "symbol"
    )

    if open_trade_symbol:
        extra_symbols.append(open_trade_symbol)

extra_symbols = [
    sym for sym in dict.fromkeys(extra_symbols)
    if sym not in quotes
]

if extra_symbols:
    trade_symbols.extend(extra_symbols)

    try:
        quotes.update(
            fetch_option_quotes(
                fyers,
                tuple(extra_symbols)
            )
        )
    except Exception as e:
        st.error(
            f"Option quotes fetch failed: {e}"
        )
        st.stop()

try:
    (
        calculation_df,
        average_ce_deviation,
        average_pe_deviation
    ) = calculate_deviations(
        dev_strikes,
        option_map,
        quotes
    )
except Exception as e:
    st.error(
        f"Deviation calculation failed: {e}"
    )
    st.stop()

# Theta / (LTP x Delta) at breakeven strikes.
# LTP, delta and theta all come from the same chain row.
greeks_map = build_greeks_map(chain_rows)

theta_rows = []

for be_label, be_strike, opt, sym in be_legs:
    g = greeks_map.get(sym, {})
    g_ltp = g.get("ltp")
    g_delta = g.get("delta")
    g_theta = g.get("theta")

    theta_rows.append({
        "Level": be_label,
        "Strike": be_strike,
        "Type": opt,
        "LTP (chain)": g_ltp,
        "Delta": g_delta,
        "Theta": g_theta,
        "|Theta| / (LTP × |Delta|)": theta_ratio(
            g_theta,
            g_ltp,
            g_delta
        )
    })

theta_df = pd.DataFrame(theta_rows)

# CE / (CE + PE) and PE / (CE + PE) for the upper-BE CE and lower-BE PE
be_ce_ltp = quotes.get(
    option_map[upper_strike]["CE"], {}
).get("lp")

be_pe_ltp = quotes.get(
    option_map[lower_strike]["PE"], {}
).get("lp")

ce_share, pe_share = premium_share(
    be_ce_ltp,
    be_pe_ltp
)

buy_signal = check_buy_signal(
    average_ce_deviation,
    average_pe_deviation
)

sell_signal = check_sell_signal(
    average_ce_deviation,
    average_pe_deviation
)

exit_signal = check_exit_signal(
    st.session_state.paper_trade,
    average_ce_deviation,
    average_pe_deviation
)

if st.session_state.paper_trade is not None:
    open_trade = st.session_state.paper_trade

    current_symbol = open_trade.get(
        "symbol"
    )

    current_quote = quotes.get(
        current_symbol
    )

    if (
        current_quote
        and valid_number(
            current_quote.get("lp")
        )
    ):
        current_price = float(
            current_quote.get("lp")
        )

        if st.session_state.manual_close_requested:
            exit_paper_trade(
                current_price,
                average_ce_deviation,
                average_pe_deviation,
                spot
            )
            st.rerun()

        if exit_signal:
            exit_paper_trade(
                current_price,
                average_ce_deviation,
                average_pe_deviation,
                spot
            )
            st.rerun()

else:
    if buy_signal:
        trade_symbol = option_map[
            lower_strike
        ]["PE"]

        trade_quote = quotes.get(
            trade_symbol
        )

        if (
            trade_quote
            and valid_number(
                trade_quote.get("lp")
            )
        ):
            enter_paper_trade(
                signal="BUY",
                option_type="PE",
                strike=lower_strike,
                symbol=trade_symbol,
                entry_price=float(
                    trade_quote.get("lp")
                ),
                avg_ce_dev=average_ce_deviation,
                avg_pe_dev=average_pe_deviation,
                spot=spot,
                lots=int(lots),
                lot_size=lot_size,
                upper_breakeven=upper_breakeven,
                lower_breakeven=lower_breakeven
            )

    elif sell_signal:
        trade_symbol = option_map[
            upper_strike
        ]["CE"]

        trade_quote = quotes.get(
            trade_symbol
        )

        if (
            trade_quote
            and valid_number(
                trade_quote.get("lp")
            )
        ):
            enter_paper_trade(
                signal="SELL",
                option_type="CE",
                strike=upper_strike,
                symbol=trade_symbol,
                entry_price=float(
                    trade_quote.get("lp")
                ),
                avg_ce_dev=average_ce_deviation,
                avg_pe_dev=average_pe_deviation,
                spot=spot,
                lots=int(lots),
                lot_size=lot_size,
                upper_breakeven=upper_breakeven,
                lower_breakeven=lower_breakeven
            )

st.markdown(
    """
    <style>
    /* Change the font size of the metric number/value */
    [data-testid="stMetricValue"] {
        font-size: 20px !important;
    }

    /* Change the font size of the metric top label */
    [data-testid="stMetricLabel"] p {
        font-size: 14px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
m = st.columns(8)

m[0].metric(
    "Spot",
    f"{spot:,.2f}"
)

m[1].metric(
    "Syn Future",
    f"{syn_fut:,.2f}"
)

m[2].metric(
    "Syn ATM",
    f"{atm:,.0f}"
)

m[3].metric(
    "Straddle @ Syn ATM",
    f"{atm_straddle:,.2f}"
)

m[4].metric(
    "Upper BE Strike",
    f"{upper_strike:,.2f}"
)

m[5].metric(
    "Lower BE Strike",
    f"{lower_strike:,.2f}"
)

m[6].metric(
    "Avg CE Dev.",
    f"{average_ce_deviation:+,.2f}"
)

m[7].metric(
    "Avg PE Dev.",
    f"{average_pe_deviation:+,.2f}"
)

st.subheader(
    "Synthetic Future (Strike + CE LTP − PE LTP)"
)

st.dataframe(
    syn_df,
    use_container_width=True,
    hide_index=True
)

st.write(
    f"**Synthetic Future** (average of {len(syn_df)} strikes) = "
    f"**{syn_fut:,.2f}**  |  Spot = {spot:,.2f}  |  "
    f"Syn Future − Spot = {syn_fut - spot:+,.2f}"
)

st.caption(
    "The synthetic future is averaged over the three strikes around spot ATM "
    "(needed to locate the Syn ATM). Avg CE/PE Dev and the straddle use the "
    "strikes around the Syn ATM."
)

st.subheader(
    "Breakeven Strike Selection"
)

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

st.dataframe(
    be_df,
    use_container_width=True,
    hide_index=True
)

st.subheader(
    "Premium Share at Breakeven Strikes"
)

share_df = pd.DataFrame([
    {
        "Upper BE Strike": upper_strike,
        "CE LTP": be_ce_ltp,
        "CE Dist from Syn Fut": upper_strike - syn_fut,
        "Lower BE Strike": lower_strike,
        "PE LTP": be_pe_ltp,
        "PE Dist from Syn Fut": syn_fut - lower_strike,
        "CE / (CE+PE) %": (
            None if ce_share is None else ce_share * 100
        ),
        "PE / (CE+PE) %": (
            None if pe_share is None else pe_share * 100
        )
    }
])

st.dataframe(
    share_df,
    use_container_width=True,
    hide_index=True
)

sh = st.columns(2)

sh[0].metric(
    "CE / (CE + PE)",
    "n/a" if ce_share is None else f"{ce_share * 100:.1f}%"
)

sh[1].metric(
    "PE / (CE + PE)",
    "n/a" if pe_share is None else f"{pe_share * 100:.1f}%"
)

st.caption(
    "CE = upper breakeven strike CE LTP, PE = lower breakeven strike PE LTP. "
    "Strikes snap to the nearest listed strike, so check the two distances "
    "from the synthetic future before reading the gap as skew."
)

st.subheader(
    "Theta / (LTP × Delta) at Breakeven Strikes"
)

st.dataframe(
    theta_df,
    use_container_width=True,
    hide_index=True
)

st.caption(
    "Ratio = abs(Theta) / (LTP × abs(Delta)). "
    "LTP, Delta and Theta are all from the same option chain snapshot."
)

st.subheader(
    "CE / PE LTP − ATP (Syn ATM−1, Syn ATM, Syn ATM+1)"
)

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

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True
)

st.subheader(
    "Signal Conditions"
)

if st.session_state.paper_trade is None:
    exit_condition_text = "No open position"
else:
    open_option = st.session_state.paper_trade.get(
        "option_type",
        ""
    )

    if open_option == "PE":
        exit_condition_text = (
            "abs(Avg CE Dev) >= abs(Avg PE Dev)"
        )
    elif open_option == "CE":
        exit_condition_text = (
            "abs(Avg PE Dev) >= abs(Avg CE Dev)"
        )
    else:
        exit_condition_text = "Invalid open position"

signal_df = pd.DataFrame([
    {
        "Condition": "Buy Signal",
        "Result": buy_signal
    },
    {
        "Condition": "Sell Signal",
        "Result": sell_signal
    },
    {
        "Condition": "Exit Signal",
        "Result": exit_signal
    },
    {
        "Condition": exit_condition_text,
        "Result": exit_signal
    }
])

st.dataframe(
    signal_df,
    use_container_width=True,
    hide_index=True
)

st.subheader(
    "Calculation"
)

for _, row in calculation_df.iterrows():
    st.write(
        f"**{row['Strike']:,.0f}** → "
        f"CE: {row['CE LTP']:,.2f} − "
        f"{row['CE ATP']:,.2f} = "
        f"**{row['CE LTP-ATP']:+,.2f}** | "
        f"PE: {row['PE LTP']:,.2f} − "
        f"{row['PE ATP']:,.2f} = "
        f"**{row['PE LTP-ATP']:+,.2f}**"
    )

ce_values = calculation_df[
    "CE LTP-ATP"
].tolist()

pe_values = calculation_df[
    "PE LTP-ATP"
].tolist()

ce_values_text = " + ".join(
    f"({x:+,.2f})"
    for x in ce_values
)

pe_values_text = " + ".join(
    f"({x:+,.2f})"
    for x in pe_values
)

st.write(
    f"**Average CE Dev:** "
    f"({ce_values_text}) / {len(ce_values)} = "
    f"**{average_ce_deviation:+,.2f}**"
)

st.write(
    f"**Average PE Dev:** "
    f"({pe_values_text}) / {len(pe_values)} = "
    f"**{average_pe_deviation:+,.2f}**"
)

st.subheader(
    "Paper Trade"
)

if st.session_state.paper_trade is None:
    st.info(
        "No open paper position."
    )
else:
    trade = st.session_state.paper_trade

    current_quote = quotes.get(
        trade.get("symbol")
    )

    current_price = None
    unrealized_pnl = None

    if (
        current_quote
        and valid_number(
            current_quote.get("lp")
        )
    ):
        current_price = float(
            current_quote.get("lp")
        )

        unrealized_pnl = (
            float(
                trade.get(
                    "entry_price",
                    0
                )
            ) - current_price
        ) * int(
            trade.get(
                "quantity",
                65
            )
        )

    open_trade_df = pd.DataFrame([
        {
            "Signal": trade.get(
                "signal",
                ""
            ),
            "Action": trade.get(
                "action",
                ""
            ),
            "Option": trade.get(
                "option_type",
                ""
            ),
            "Strike": trade.get(
                "strike",
                ""
            ),
            "Lots": trade.get(
                "lots",
                1
            ),
            "Qty": trade.get(
                "quantity",
                65
            ),
            "Entry Price": trade.get(
                "entry_price",
                0
            ),
            "Current Price": current_price,
            "Unrealized P&L": unrealized_pnl,
            "Entry Time": format_ist_time(
                trade.get(
                    "entry_time",
                    get_ist_time()
                )
            ),
            "Entry Spot": trade.get(
                "entry_spot",
                0
            ),
            "Symbol": trade.get(
                "symbol",
                ""
            )
        }
    ])

    st.dataframe(
        open_trade_df,
        use_container_width=True,
        hide_index=True
    )

st.subheader(
    "Trade Log"
)

if st.session_state.trade_log:
    trade_log_df = pd.DataFrame(
        st.session_state.trade_log
    )

    total_pnl = trade_log_df[
        "P&L"
    ].sum()

    total_trades = len(
        trade_log_df
    )

    winning_trades = (
        trade_log_df["P&L"] > 0
    ).sum()

    losing_trades = (
        trade_log_df["P&L"] < 0
    ).sum()

    p = st.columns(4)

    p[0].metric(
        "Closed Trades",
        total_trades
    )

    p[1].metric(
        "Winning Trades",
        int(winning_trades)
    )

    p[2].metric(
        "Losing Trades",
        int(losing_trades)
    )

    p[3].metric(
        "Total P&L",
        f"₹{total_pnl:,.2f}"
    )

    st.dataframe(
        trade_log_df,
        use_container_width=True,
        hide_index=True
    )
else:
    st.info(
        "No completed paper trades yet."
    )

with st.expander(
    "Raw Quotes API Data"
):
    raw_rows = []

    for symbol in dict.fromkeys(
        trade_symbols
    ):
        quote = quotes.get(
            symbol,
            {}
        )

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

with st.expander(
    "Strategy Formula"
):
    st.latex(
        r"CE_{dev}=CE_{LTP}-CE_{ATP}"
    )

    st.latex(
        r"PE_{dev}=PE_{LTP}-PE_{ATP}"
    )

    st.latex(
        r"AvgCEDev="
        r"\frac{CEDev_{SynATM-1}+CEDev_{SynATM}+CEDev_{SynATM+1}}{3}"
    )

    st.latex(
        r"AvgPEDev="
        r"\frac{PEDev_{SynATM-1}+PEDev_{SynATM}+PEDev_{SynATM+1}}{3}"
    )

    st.latex(
        r"SynFut_{K}=K+CE_{LTP,K}-PE_{LTP,K}"
    )

    st.latex(
        r"SynFut=\frac{SynFut_{SpotATM-1}+SynFut_{SpotATM}+SynFut_{SpotATM+1}}{3}"
    )

    st.latex(
        r"Syn\ ATM=\text{listed strike closest to }SynFut"
    )

    st.latex(
        r"Straddle=CE_{LTP,SynATM}+PE_{LTP,SynATM}"
    )

    st.latex(
        r"Upper\ BE=SynFut+Straddle"
    )

    st.latex(
        r"Lower\ BE=SynFut-Straddle"
    )

    st.latex(
        r"CE\ Share=\frac{CE_{UpperBE}}{CE_{UpperBE}+PE_{LowerBE}}"
        r",\quad "
        r"PE\ Share=\frac{PE_{LowerBE}}{CE_{UpperBE}+PE_{LowerBE}}"
    )

    st.latex(
        r"Theta\ Ratio=\frac{|\Theta|}{LTP\times|\Delta|}"
    )

    st.write(
        "Buy signal: Avg CE Dev > 0, Avg PE Dev < 0, "
        "and abs(Avg PE Dev) > 1.5 × abs(Avg CE Dev)."
    )

    st.write(
        "Buy signal: SELL the selected number of lots "
        "of the PE at the strike closest to the Lower BE."
    )

    st.write(
        "Sell signal: Avg PE Dev > 0, Avg CE Dev < 0, "
        "and abs(Avg CE Dev) > 1.5 × abs(Avg PE Dev)."
    )

    st.write(
        "Sell signal: SELL the selected number of lots "
        "of the CE at the strike closest to the Upper BE."
    )

    st.write(
        "Short PE exit: abs(Avg CE Dev) >= abs(Avg PE Dev)."
    )

    st.write(
        "Short CE exit: abs(Avg PE Dev) >= abs(Avg CE Dev)."
    )

    st.write(
        "Lot size: 65 quantity per lot."
    )

    st.write(
        "All trades are paper trades. No orders are sent to Fyers."
    )

if st.button(
    "Manual Refresh"
):
    st.rerun()
