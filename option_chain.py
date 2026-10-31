import streamlit as st
import pandas as pd
from fyers_apiv3 import fyersModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Optional: auto-refresh support
try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="FYERS Option Chain",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
# CUSTOM UI STYLING
# ============================================================

st.markdown("""
<style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 1900px;
    }

    [data-testid="stMetric"] {
        background: rgba(128, 128, 128, 0.07);
        border: 1px solid rgba(128, 128, 128, 0.18);
        padding: 14px 16px;
        border-radius: 10px;
    }

    [data-testid="stMetricLabel"] {
        font-size: 13px;
        opacity: 0.8;
    }

    [data-testid="stMetricValue"] {
        font-size: 23px;
        font-weight: 650;
    }

    .section-heading {
        font-size: 19px;
        font-weight: 650;
        margin-top: 15px;
        margin-bottom: 10px;
    }

    .sub-heading {
        font-size: 13px;
        opacity: 0.65;
    }

    div[data-testid="stExpander"] {
        border-radius: 9px;
    }

    .stButton button {
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def create_fyers_client(client_id, access_token):
    """Initialize FYERS API client."""

    return fyersModel.FyersModel(
        client_id=client_id,
        token=access_token,
        is_async=False,
        log_path=""
    )


def fetch_option_chain(
    fyers,
    symbol,
    strike_count,
    expiry_timestamp=""
):
    """
    Fetch option chain from FYERS.

    First request:
        expiry_timestamp = ""

    Subsequent requests:
        expiry_timestamp = selected expiry epoch
    """

    request_data = {
        "symbol": symbol,
        "strikecount": int(strike_count),
        "timestamp": str(expiry_timestamp),
        "greeks": "1"
    }

    response = fyers.optionchain(data=request_data)

    if not isinstance(response, dict):
        raise RuntimeError(
            "Invalid response received from FYERS."
        )

    if response.get("s") != "ok":
        message = response.get("message", "Unknown API error")
        code = response.get("code", "")

        raise RuntimeError(
            f"FYERS API Error | Code: {code} | {message}"
        )

    if not isinstance(response.get("data"), dict):
        raise RuntimeError(
            "Response does not contain valid option-chain data."
        )

    return response


def safe_float(value):
    """Convert a value to float safely."""

    try:
        if value is None:
            return None

        result = float(value)

        if pd.isna(result):
            return None

        return result

    except (ValueError, TypeError):
        return None


def safe_int(value):
    """Convert a value to integer safely."""

    try:
        if value is None:
            return None

        return int(float(value))

    except (ValueError, TypeError):
        return None


def get_token(row):
    """Support both token key variations."""

    return row.get("fyToken", row.get("fytoken"))


def get_greek(row, greek_name):
    greeks = row.get("greeks") or {}

    return safe_float(
        greeks.get(greek_name)
    )


def format_price(value):
    value = safe_float(value)

    if value is None:
        return "—"

    return f"{value:,.2f}"


def format_change(change, change_percent):
    change = safe_float(change)
    change_percent = safe_float(change_percent)

    if change is None:
        return "—"

    if change_percent is not None:
        return f"{change:+,.2f} ({change_percent:+.2f}%)"

    return f"{change:+,.2f}"


def get_underlying(chain):
    """Find the underlying entry in optionsChain."""

    for item in chain:

        if not isinstance(item, dict):
            continue

        option_type = str(
            item.get("option_type", "")
        ).upper()

        strike = safe_float(
            item.get("strike_price")
        )

        if option_type == "" or strike == -1:
            return item

    return {}


def get_expiry_label(item):
    expiry_date = item.get("date", "Unknown")
    expiry_flag = item.get("expiry_flag", "")

    flag_text = {
        "W": "Weekly",
        "M": "Monthly"
    }.get(expiry_flag, expiry_flag)

    return f"{expiry_date}  |  {flag_text}"


# ============================================================
# FYERS QUOTES API
# ============================================================

def fetch_option_quotes(
    fyers,
    options_chain,
    batch_size=50
):
    """
    Fetch current LTP and today's open price for all
    CE and PE contracts returned by the option-chain API.

    Quotes API supports a maximum of 50 symbols per request.
    """

    symbols = []

    for item in options_chain:

        if not isinstance(item, dict):
            continue

        option_type = str(
            item.get("option_type", "")
        ).upper()

        if option_type not in ("CE", "PE"):
            continue

        option_symbol = str(
            item.get("symbol", "")
        ).strip()

        if option_symbol:
            symbols.append(option_symbol)

    # Remove duplicate symbols while preserving order.
    symbols = list(
        dict.fromkeys(symbols)
    )

    quote_map = {}
    errors = []

    if not symbols:
        return quote_map, [
            "No CE/PE symbols found in the option chain."
        ]

    # Fetch quotes in batches.
    for start in range(0, len(symbols), batch_size):

        batch = symbols[start:start + batch_size]

        batch_number = (start // batch_size) + 1

        try:

            response = fyers.quotes(
                data={
                    "symbols": ",".join(batch)
                }
            )

            if not isinstance(response, dict):

                errors.append(
                    f"Batch {batch_number}: "
                    "Invalid Quotes API response."
                )

                continue

            if response.get("s") != "ok":

                errors.append(
                    f"Batch {batch_number}: "
                    f"{response.get('message', 'Quotes API error')}"
                )

                continue

            quote_items = response.get("d", [])

            if not isinstance(quote_items, list):

                errors.append(
                    f"Batch {batch_number}: "
                    "Invalid quote data format."
                )

                continue

            for item in quote_items:

                if not isinstance(item, dict):
                    continue

                quote_status = str(
                    item.get("s", "")
                ).lower()

                quote_symbol = (
                    item.get("n")
                    or (item.get("v") or {}).get("symbol")
                )

                if quote_status != "ok":

                    errors.append(
                        f"{quote_symbol or 'Unknown symbol'}: "
                        "Quote unavailable."
                    )

                    continue

                values = item.get("v") or {}

                if not quote_symbol:
                    quote_symbol = values.get("symbol")

                if not quote_symbol:
                    continue

                quote_symbol = str(
                    quote_symbol
                ).strip().upper()

                quote_map[quote_symbol] = {

                    "ltp": safe_float(
                        values.get("lp")
                    ),

                    "open_price": safe_float(
                        values.get("open_price")
                    ),

                    "bid": safe_float(
                        values.get("bid")
                    ),

                    "ask": safe_float(
                        values.get("ask")
                    ),

                    "timestamp": values.get("tt")
                }

        except Exception as error:

            errors.append(
                f"Batch {batch_number}: {str(error)}"
            )

    return quote_map, errors


# ============================================================
# OPTION CHAIN DATA PROCESSING
# ============================================================

def build_option_chain_table(api_data, underlying):
    """
    Convert the raw FYERS option-chain response into
    a paired CE / Strike / PE table.
    """

    options_chain = api_data.get("optionsChain", [])

    strike_map = {}

    for item in options_chain:

        if not isinstance(item, dict):
            continue

        option_type = str(
            item.get("option_type", "")
        ).upper()

        if option_type not in ("CE", "PE"):
            continue

        strike = safe_float(
            item.get("strike_price")
        )

        if strike is None or strike < 0:
            continue

        strike_key = (
            int(strike) if strike.is_integer() else strike
        )

        if strike_key not in strike_map:
            strike_map[strike_key] = {}

        strike_map[strike_key][option_type] = item

    strikes = sorted(
        strike_map.keys()
    )

    if not strikes:
        return pd.DataFrame(), None, None

    spot = safe_float(
        underlying.get("ltp")
    )

    if spot is not None:

        atm_strike = min(
            strikes,
            key=lambda x: abs(x - spot)
        )

    else:

        atm_strike = None

    fields = [
        ("Symbol", "symbol"),
        ("FY Token", "token"),
        ("Bid", "bid"),
        ("Ask", "ask"),
        ("LTP", "ltp"),
        ("LTP Chg", "ltpch"),
        ("LTP Chg %", "ltpchp"),
        ("Volume", "volume"),
        ("OI", "oi"),
        ("Previous OI", "prev_oi"),
        ("OI Chg", "oich"),
        ("OI Chg %", "oichp"),
        ("Delta", "delta"),
        ("Gamma", "gamma"),
        ("Theta", "theta"),
        ("Vega", "vega"),
        ("IV", "iv")
    ]

    table_rows = []

    for strike in strikes:

        ce = strike_map[strike].get("CE", {})
        pe = strike_map[strike].get("PE", {})

        row = {}

        # CALL data
        for label, field in fields:

            if field == "token":

                value = get_token(ce)

            elif field in (
                "delta",
                "gamma",
                "theta",
                "vega",
                "iv"
            ):

                value = get_greek(
                    ce,
                    field
                )

            elif field == "volume":

                value = safe_int(
                    ce.get(field)
                )

            elif field in (
                "oi",
                "prev_oi",
                "oich"
            ):

                value = safe_int(
                    ce.get(field)
                )

            else:

                value = ce.get(field)

                if field in (
                    "bid",
                    "ask",
                    "ltp",
                    "ltpch",
                    "ltpchp",
                    "oichp"
                ):

                    value = safe_float(value)

            row[f"CE {label}"] = value

        row["STRIKE"] = strike

        row["ATM"] = (
            "ATM" if strike == atm_strike else ""
        )

        # PUT data
        for label, field in fields:

            if field == "token":

                value = get_token(pe)

            elif field in (
                "delta",
                "gamma",
                "theta",
                "vega",
                "iv"
            ):

                value = get_greek(
                    pe,
                    field
                )

            elif field == "volume":

                value = safe_int(
                    pe.get(field)
                )

            elif field in (
                "oi",
                "prev_oi",
                "oich"
            ):

                value = safe_int(
                    pe.get(field)
                )

            else:

                value = pe.get(field)

                if field in (
                    "bid",
                    "ask",
                    "ltp",
                    "ltpch",
                    "ltchp",
                    "oichp"
                ):

                    value = safe_float(value)

            row[f"PE {label}"] = value

        table_rows.append(row)

    df = pd.DataFrame(table_rows)

    columns = (
        [f"CE {label}" for label, _ in fields]
        + ["STRIKE", "ATM"]
        + [f"PE {label}" for label, _ in fields]
    )

    df = df.reindex(
        columns=columns
    )

    return df, atm_strike, strike_map


# ============================================================
# DELTA-MATCHED PREMIUM WEAKNESS
# ============================================================

def get_delta_change_points(
    strike_map,
    option_type,
    quote_map
):
    """
    Collect option contracts with:

        Absolute delta
        Strike
        Current LTP from Quotes API
        Today's open price from Quotes API
        Percentage change from today's open
    """

    points = []

    for strike, options in strike_map.items():

        option = options.get(
            option_type,
            {}
        )

        if not option:
            continue

        symbol = str(
            option.get("symbol", "")
        ).strip().upper()

        if not symbol:
            continue

        delta = get_greek(
            option,
            "delta"
        )

        quote = quote_map.get(symbol)

        if not quote:
            continue

        ltp = safe_float(
            quote.get("ltp")
        )

        open_price = safe_float(
            quote.get("open_price")
        )

        if (
            delta is None
            or ltp is None
            or open_price is None
        ):
            continue

        if ltp <= 0 or open_price <= 0:
            continue

        abs_delta = abs(delta)

        if not 0 <= abs_delta <= 1:
            continue

        change_percent = (
            (ltp - open_price) / open_price
        ) * 100

        points.append({

            "abs_delta": abs_delta,

            "strike": strike,

            "ltp": ltp,

            "open_price": open_price,

            "change_percent": change_percent
        })

    points.sort(
        key=lambda x: x["abs_delta"]
    )

    unique_points = []

    for point in points:

        if (
            unique_points
            and abs(
                point["abs_delta"]
                - unique_points[-1]["abs_delta"]
            ) < 0.000001
        ):

            continue

        unique_points.append(point)

    return unique_points


def interpolate_change_at_delta(
    points,
    target_delta
):
    """
    Find percentage change at an exact absolute delta.
    """

    if len(points) < 2:
        return None

    for point in points:

        if abs(
            point["abs_delta"] - target_delta
        ) < 0.000001:

            return {

                "change_percent": point["change_percent"],

                "reference_strikes": (
                    f"{point['strike']:,.0f}"
                )
            }

    for i in range(len(points) - 1):

        lower = points[i]
        upper = points[i + 1]

        x1 = lower["abs_delta"]
        x2 = upper["abs_delta"]

        if x1 < target_delta < x2:

            y1 = lower["change_percent"]
            y2 = upper["change_percent"]

            weight = (
                (target_delta - x1)
                / (x2 - x1)
            )

            interpolated_change = (
                y1 + weight * (y2 - y1)
            )

            return {

                "change_percent": interpolated_change,

                "reference_strikes": (
                    f"{lower['strike']:,.0f} – "
                    f"{upper['strike']:,.0f}"
                )
            }

    return None


def build_delta_matched_comparison(
    strike_map,
    quote_map
):
    """
    Compare CE and PE percentage changes from today's open
    at matched absolute delta levels.
    """

    if not strike_map or not quote_map:

        return pd.DataFrame(), None

    ce_points = get_delta_change_points(
        strike_map,
        "CE",
        quote_map
    )

    pe_points = get_delta_change_points(
        strike_map,
        "PE",
        quote_map
    )

    target_deltas = [
        0.25,
        0.30,
        0.35,
        0.40,
        0.45,
        0.50
    ]

    rows = []

    for target_delta in target_deltas:

        ce_result = interpolate_change_at_delta(
            ce_points,
            target_delta
        )

        pe_result = interpolate_change_at_delta(
            pe_points,
            target_delta
        )

        if ce_result is None or pe_result is None:
            continue

        ce_change = ce_result["change_percent"]
        pe_change = pe_result["change_percent"]

        difference = (
            ce_change - pe_change
        )

        rows.append({

            "Target Delta": (
                f"{target_delta * 100:.0f}Δ"
            ),

            "CE Reference Strikes":
                ce_result["reference_strikes"],

            "CE Change From Open %":
                ce_change,

            "PE Reference Strikes":
                pe_result["reference_strikes"],

            "PE Change From Open %":
                pe_change,

            "CE − PE (pp)":
                difference
        })

    comparison_df = pd.DataFrame(rows)

    if comparison_df.empty:

        return comparison_df, None

    relative_weakness_score = comparison_df[
        "CE − PE (pp)"
    ].mean()

    return comparison_df, relative_weakness_score


# ============================================================
# TABLE FORMATTING
# ============================================================

def get_column_config():

    config = {}

    decimal_fields = [
        "Bid",
        "Ask",
        "LTP",
        "LTP Chg",
        "LTP Chg %",
        "OI Chg %",
        "Delta",
        "Gamma",
        "Theta",
        "Vega",
        "IV"
    ]

    integer_fields = [
        "Volume",
        "OI",
        "Previous OI",
        "OI Chg"
    ]

    for side in ("CE", "PE"):

        for field in decimal_fields:

            config[f"{side} {field}"] = (
                st.column_config.NumberColumn(
                    field,
                    format="%.2f"
                )
            )

        for field in integer_fields:

            config[f"{side} {field}"] = (
                st.column_config.NumberColumn(
                    field,
                    format="%d"
                )
            )

        config[f"{side} Symbol"] = (
            st.column_config.TextColumn(
                f"{side} Symbol",
                width="medium"
            )
        )

        config[f"{side} FY Token"] = (
            st.column_config.TextColumn(
                f"{side} FY Token",
                width="small"
            )
        )

    config["STRIKE"] = (
        st.column_config.NumberColumn(
            "STRIKE",
            format="%d"
        )
    )

    config["ATM"] = (
        st.column_config.TextColumn(
            "ATM",
            width="small"
        )
    )

    return config


# ============================================================
# SIDEBAR - API CONFIGURATION
# ============================================================

st.sidebar.title("⚙️ Configuration")

client_id = st.sidebar.text_input(
    "FYERS Client ID",
    placeholder="XXXXXXXX-100"
)

access_token = st.sidebar.text_input(
    "FYERS Access Token",
    type="password",
    placeholder="Enter your access token"
)

st.sidebar.divider()

symbol = st.sidebar.text_input(
    "Underlying Symbol",
    value="NSE:NIFTY50-INDEX",
    help="Example: NSE:NIFTY50-INDEX"
).strip().upper()

strike_count = st.sidebar.slider(
    "Strikes on each side of ATM",
    min_value=1,
    max_value=50,
    value=20,
    help=(
        "A value of 20 returns ATM plus 20 ITM "
        "and 20 OTM strikes per option type."
    )
)


# ============================================================
# EXPIRY SELECTOR
# ============================================================

expiry_items = []

if st.session_state.get("expiry_symbol") == symbol:

    expiry_items = st.session_state.get(
        "expiry_data",
        []
    )

selected_expiry_label = None
selected_expiry_timestamp = ""

if expiry_items:

    expiry_labels = [
        get_expiry_label(item)
        for item in expiry_items
    ]

    label_to_timestamp = {

        get_expiry_label(item): str(
            item.get("expiry", "")
        )

        for item in expiry_items
    }

    selected_expiry_label = st.sidebar.selectbox(
        "Select Expiry",
        options=expiry_labels,
        key=f"expiry_selector_{symbol}"
    )

    selected_expiry_timestamp = (
        label_to_timestamp.get(
            selected_expiry_label,
            ""
        )
    )

else:

    st.sidebar.info(
        "Fetch the option chain to load available expiries."
    )


# ============================================================
# AUTO-REFRESH CONFIGURATION
# ============================================================

st.sidebar.divider()

auto_refresh_enabled = st.sidebar.checkbox(
    "🔄 Auto-Refresh",
    value=False,
    help=(
        "Automatically re-fetch the option chain and quotes "
        "on a fixed interval."
    )
)

auto_refresh_minutes = 1

if auto_refresh_enabled:

    auto_refresh_minutes = st.sidebar.number_input(
        "Refresh interval (minutes)",
        min_value=1,
        max_value=120,
        value=1,
        step=1,
        help="How often to re-fetch data while auto-refresh is on."
    )

    if not AUTOREFRESH_AVAILABLE:

        st.sidebar.warning(
            "Auto-refresh requires the "
            "**streamlit-autorefresh** package.\n\n"
            "Install it with:\n"
            "`pip install streamlit-autorefresh`"
        )


st.sidebar.divider()

fetch_button = st.sidebar.button(
    "🔄 Fetch / Refresh Option Chain",
    type="primary",
    use_container_width=True
)


# ============================================================
# ARM THE AUTO-REFRESH TIMER (UNCONDITIONAL)
# ============================================================
# The component is called on every rerun so its internal timer
# stays alive. When auto-refresh is disabled, we use a very long
# interval so it effectively never fires.

refresh_counter = 0

if AUTOREFRESH_AVAILABLE:

    if auto_refresh_enabled:

        interval_ms = int(auto_refresh_minutes * 60 * 1000)

    else:

        # 24 hours — effectively off, but component stays mounted.
        interval_ms = 24 * 60 * 60 * 1000

    refresh_counter = st_autorefresh(
        interval=interval_ms,
        limit=None,
        key="auto_refresh_timer"
    )


# ============================================================
# DECIDE WHETHER TO FETCH
# ============================================================
# Fetch fires when:
#   (a) user clicks the manual Fetch button, OR
#   (b) auto-refresh is enabled AND the timer counter has
#       increased since the last time we fetched.
#
# Using the returned counter (rather than wall-clock time)
# guarantees we act on each tick even if the server clock or
# Streamlit rerun timing drifts.

trigger_fetch = fetch_button

if (
    not trigger_fetch
    and auto_refresh_enabled
    and AUTOREFRESH_AVAILABLE
    and client_id
    and access_token
    and symbol
):

    last_counter = st.session_state.get(
        "last_auto_refresh_counter",
        0
    )

    if refresh_counter > last_counter:

        trigger_fetch = True

        st.session_state[
            "last_auto_refresh_counter"
        ] = refresh_counter


# ============================================================
# FETCH OPTION CHAIN AND QUOTES
# ============================================================

if trigger_fetch:

    if not client_id or not access_token:

        st.sidebar.error(
            "Please enter your FYERS Client ID and Access Token."
        )

    elif not symbol:

        st.sidebar.error(
            "Please enter an underlying symbol."
        )

    else:

        try:

            with st.spinner(
                "Fetching option chain and option quotes..."
            ):

                fyers = create_fyers_client(
                    client_id,
                    access_token
                )

                # When loading a new underlying, use an
                # empty timestamp to discover available expiries.
                if (
                    st.session_state.get("expiry_symbol") != symbol
                    or not expiry_items
                ):

                    expiry_timestamp = ""

                else:

                    expiry_timestamp = (
                        selected_expiry_timestamp
                    )

                # --------------------------------------------
                # 1. FETCH OPTION CHAIN
                # --------------------------------------------

                response = fetch_option_chain(
                    fyers=fyers,
                    symbol=symbol,
                    strike_count=strike_count,
                    expiry_timestamp=expiry_timestamp
                )

                api_data = response["data"]

                new_expiries = api_data.get(
                    "expiryData",
                    []
                )

                # --------------------------------------------
                # 2. FETCH QUOTES FOR ALL CE/PE CONTRACTS
                # --------------------------------------------

                option_quotes, quote_errors = (
                    fetch_option_quotes(
                        fyers=fyers,
                        options_chain=api_data.get(
                            "optionsChain",
                            []
                        ),
                        batch_size=50
                    )
                )

                # --------------------------------------------
                # 3. SAVE DATA IN SESSION STATE
                # --------------------------------------------

                st.session_state[
                    "option_chain_response"
                ] = response

                st.session_state[
                    "option_quote_map"
                ] = option_quotes

                st.session_state[
                    "option_quote_errors"
                ] = quote_errors

                st.session_state[
                    "expiry_data"
                ] = new_expiries

                st.session_state[
                    "expiry_symbol"
                ] = symbol

                st.session_state[
                    "loaded_symbol"
                ] = symbol

                st.session_state[
                    "loaded_strike_count"
                ] = strike_count

                st.session_state[
                    "selected_expiry_timestamp"
                ] = expiry_timestamp

                # Determine expiry represented by this response.
                if expiry_timestamp:

                    current_expiry = next(
                        (
                            item
                            for item in new_expiries
                            if str(
                                item.get("expiry")
                            ) == expiry_timestamp
                        ),
                        None
                    )

                else:

                    current_expiry = (
                        new_expiries[0]
                        if new_expiries
                        else None
                    )

                st.session_state[
                    "loaded_expiry"
                ] = current_expiry

                now_ist = datetime.now(
                    ZoneInfo("Asia/Kolkata")
                )

                fetched_at = now_ist.strftime(
                    "%d-%m-%Y %H:%M:%S IST"
                )

                st.session_state[
                    "last_fetched"
                ] = fetched_at

                st.session_state[
                    "last_fetch_dt"
                ] = now_ist

                st.session_state[
                    "option_quote_fetched_at"
                ] = fetched_at

                st.session_state[
                    "option_quote_count"
                ] = len(option_quotes)

            st.rerun()

        except Exception as error:

            st.sidebar.error(
                f"Unable to fetch data: {error}"
            )


# ============================================================
# MAIN DASHBOARD
# ============================================================

st.title("📊 FYERS OPTION CHAIN")

st.caption(
    "Live option-chain dashboard | Prices, Open Interest, "
    "Greeks, India VIX and Delta-Matched Premium Weakness"
)

# Auto-refresh status banner
if auto_refresh_enabled:

    if AUTOREFRESH_AVAILABLE:

        st.caption(
            f"🔄 Auto-refresh enabled — every "
            f"**{auto_refresh_minutes} minute(s)** "
            f"| Timer ticks: {refresh_counter}"
        )

    else:

        st.caption(
            "⚠️ Auto-refresh is enabled but the "
            "`streamlit-autorefresh` package is not installed."
        )


response = st.session_state.get(
    "option_chain_response"
)

if not response:

    st.info(
        "Enter your FYERS credentials in the sidebar and click "
        "**Fetch / Refresh Option Chain** to load market data."
    )

    st.stop()


# ============================================================
# VALIDATE CURRENT SELECTION
# ============================================================

loaded_symbol = st.session_state.get(
    "loaded_symbol"
)

loaded_strike_count = st.session_state.get(
    "loaded_strike_count"
)

if (
    loaded_symbol != symbol
    or loaded_strike_count != strike_count
):

    st.warning(
        "Your underlying or strike count has changed. "
        "Click **Fetch / Refresh Option Chain** "
        "to load the new data."
    )

    st.stop()


api_data = response.get(
    "data",
    {}
)

options_chain = api_data.get(
    "optionsChain",
    []
)

underlying = get_underlying(
    options_chain
)

vix_data = api_data.get(
    "indiavixData",
    {}
)

call_oi = safe_int(
    api_data.get("callOi")
) or 0

put_oi = safe_int(
    api_data.get("putOi")
) or 0

pcr = (
    put_oi / call_oi
    if call_oi > 0
    else None
)


# ============================================================
# LOAD SAVED QUOTES
# ============================================================

quote_map = st.session_state.get(
    "option_quote_map",
    {}
)

quote_errors = st.session_state.get(
    "option_quote_errors",
    []
)


# ============================================================
# BUILD OPTION CHAIN TABLE
# ============================================================

chain_df, atm_strike, strike_map = (
    build_option_chain_table(
        api_data,
        underlying
    )
)


# ============================================================
# UNDERLYING VALUES
# ============================================================

spot = safe_float(
    underlying.get("ltp")
)

spot_change = safe_float(
    underlying.get("ltpch")
)

spot_change_percent = safe_float(
    underlying.get("ltpchp")
)

future_price = safe_float(
    underlying.get("fp")
)

future_change = safe_float(
    underlying.get("fpch")
)

future_change_percent = safe_float(
    underlying.get("fpchp")
)


# ============================================================
# INDIA VIX
# ============================================================

vix = safe_float(
    vix_data.get("ltp")
)

vix_change = safe_float(
    vix_data.get("ltpchp")
)


# ============================================================
# ATM STRADDLE
# ============================================================

atm_straddle = None

if atm_strike is not None and strike_map:

    atm_ce = strike_map.get(
        atm_strike,
        {}
    ).get("CE", {})

    atm_pe = strike_map.get(
        atm_strike,
        {}
    ).get("PE", {})

    ce_ltp = safe_float(
        atm_ce.get("ltp")
    )

    pe_ltp = safe_float(
        atm_pe.get("ltp")
    )

    if (
        ce_ltp is not None
        and pe_ltp is not None
    ):

        atm_straddle = ce_ltp + pe_ltp


# ============================================================
# HEADER / SELECTED EXPIRY
# ============================================================

loaded_expiry = (
    st.session_state.get("loaded_expiry")
    or {}
)

expiry_date = loaded_expiry.get(
    "date",
    "Nearest expiry"
)

expiry_flag = loaded_expiry.get(
    "expiry_flag",
    ""
)

flag_name = {
    "W": "Weekly",
    "M": "Monthly"
}.get(
    expiry_flag,
    ""
)

header_left, header_right = st.columns(
    [3, 1]
)

with header_left:

    st.markdown(
        f"### {symbol}  ·  {expiry_date} {flag_name}"
    )

with header_right:

    st.caption(
        "Last fetched: "
        f"{st.session_state.get('last_fetched', '—')}"
    )


# ============================================================
# UNDERLYING MARKET SNAPSHOT
# ============================================================

st.markdown(
    '<div class="section-heading">'
    'Underlying Market Snapshot'
    '</div>',
    unsafe_allow_html=True
)

c1, c2, c3, c4, c5 = st.columns(5)

with c1:

    st.metric(
        "Spot LTP",
        format_price(spot),
        delta=format_change(
            spot_change,
            spot_change_percent
        )
    )

with c2:

    st.metric(
        "Future Price",
        format_price(future_price),
        delta=format_change(
            future_change,
            future_change_percent
        )
    )

with c3:

    st.metric(
        "India VIX",
        format_price(vix),
        delta=(
            f"{vix_change:+.2f}%"
            if vix_change is not None
            else None
        )
    )

with c4:

    st.metric(
        "ATM Strike",
        (
            f"{atm_strike:,.0f}"
            if atm_strike is not None
            else "—"
        )
    )

with c5:

    st.metric(
        "ATM Straddle",
        format_price(atm_straddle)
    )


# ============================================================
# OPEN INTEREST SNAPSHOT
# ============================================================

st.markdown(
    '<div class="section-heading">'
    'Open Interest Overview'
    '</div>',
    unsafe_allow_html=True
)

# ------------------------------------------------------------
# Find the strike with the highest CE OI and highest PE OI
# ------------------------------------------------------------

max_ce_oi_strike = None
max_ce_oi_value = None
max_pe_oi_strike = None
max_pe_oi_value = None

for strike, options in strike_map.items():

    ce_item = options.get("CE") or {}
    pe_item = options.get("PE") or {}

    ce_oi = safe_int(ce_item.get("oi"))
    pe_oi = safe_int(pe_item.get("oi"))

    if (
        ce_oi is not None
        and (
            max_ce_oi_value is None
            or ce_oi > max_ce_oi_value
        )
    ):
        max_ce_oi_value = ce_oi
        max_ce_oi_strike = strike

    if (
        pe_oi is not None
        and (
            max_pe_oi_value is None
            or pe_oi > max_pe_oi_value
        )
    ):
        max_pe_oi_value = pe_oi
        max_pe_oi_strike = strike


oi1, oi2, oi3 = st.columns(3)

with oi1:

    st.metric(
        "Total Call OI",
        f"{call_oi:,}"
    )

with oi2:

    st.metric(
        "Total Put OI",
        f"{put_oi:,}"
    )

with oi3:

    st.metric(
        "PCR (Put OI / Call OI)",
        (
            f"{pcr:.3f}"
            if pcr is not None
            else "—"
        )
    )


oi4, oi5 = st.columns(2)

with oi4:

    st.metric(
        "Highest CE OI Strike",
        (
            f"{max_ce_oi_strike:,.0f}"
            if max_ce_oi_strike is not None
            else "—"
        ),
        delta=(
            f"OI: {max_ce_oi_value:,}"
            if max_ce_oi_value is not None
            else None
        ),
        delta_color="off"
    )

with oi5:

    st.metric(
        "Highest PE OI Strike",
        (
            f"{max_pe_oi_strike:,.0f}"
            if max_pe_oi_strike is not None
            else "—"
        ),
        delta=(
            f"OI: {max_pe_oi_value:,}"
            if max_pe_oi_value is not None
            else None
        ),
        delta_color="off"
    )


# ============================================================
# DELTA-MATCHED CE vs PE PREMIUM WEAKNESS
# ============================================================

st.divider()

st.markdown(
    '<div class="section-heading">'
    'Delta-Matched CE vs PE Premium Weakness'
    '</div>',
    unsafe_allow_html=True
)

st.caption(
    "Compares CE and PE premium changes from today's opening "
    "price at matched absolute delta levels from 25Δ to 50Δ."
)


comparison_df, relative_weakness_score = (
    build_delta_matched_comparison(
        strike_map,
        quote_map
    )
)


# ============================================================
# QUOTE FETCH INFORMATION
# ============================================================

quote_info_col1, quote_info_col2 = st.columns(2)

with quote_info_col1:

    st.metric(
        "Contracts with Quotes",
        f"{len(quote_map):,}"
    )

with quote_info_col2:

    st.metric(
        "Delta Buckets Available",
        (
            f"{len(comparison_df)} / 6"
            if not comparison_df.empty
            else "0 / 6"
        )
    )


if quote_errors:

    st.warning(
        f"{len(quote_errors)} quote retrieval issue(s). "
        "Some contracts may be missing from the comparison."
    )

    with st.expander(
        "Quote retrieval details",
        expanded=False
    ):

        for error in quote_errors[:20]:

            st.write(error)


# ============================================================
# DELTA-MATCHED COMPARISON TABLE
# ============================================================

if comparison_df.empty:

    st.warning(
        "Unable to calculate the comparison. "
        "Check whether Quotes API returned valid current LTP "
        "and opening prices, and whether both CE and PE have "
        "contracts covering the 25Δ–50Δ range."
    )

else:

    st.dataframe(
        comparison_df,
        width="stretch",
        hide_index=True,

        column_config={

            "Target Delta": (
                st.column_config.TextColumn(
                    "Target Delta"
                )
            ),

            "CE Reference Strikes": (
                st.column_config.TextColumn(
                    "CE Reference Strikes"
                )
            ),

            "CE Change From Open %": (
                st.column_config.NumberColumn(
                    "CE Change From Open %",
                    format="%+.2f%%"
                )
            ),

            "PE Reference Strikes": (
                st.column_config.TextColumn(
                    "PE Reference Strikes"
                )
            ),

            "PE Change From Open %": (
                st.column_config.NumberColumn(
                    "PE Change From Open %",
                    format="%+.2f%%"
                )
            ),

            "CE − PE (pp)": (
                st.column_config.NumberColumn(
                    "CE − PE (pp)",
                    format="%+.2f"
                )
            )
        }
    )


    st.markdown(
        "#### Relative Weakness Score"
    )

    score_col1, score_col2 = st.columns(2)

    with score_col1:

        st.metric(
            "Average CE − PE Difference",
            f"{relative_weakness_score:+.2f} pp"
        )

    with score_col2:

        st.metric(
            "Delta Buckets Compared",
            f"{len(comparison_df)} / 6"
        )


    if relative_weakness_score > 0:

        st.info(
            "PE premiums have relatively underperformed CE "
            "premiums since today's open."
        )

    elif relative_weakness_score < 0:

        st.info(
            "CE premiums have relatively underperformed PE "
            "premiums since today's open."
        )

    else:

        st.info(
            "CE and PE premiums have approximately equal "
            "performance since today's open."
        )


# ============================================================
# COMPARISON FORMULA
# ============================================================

with st.expander(
    "How is this comparison calculated?",
    expanded=False
):

    st.markdown(
        """
        **1. Change from open**

        For each individual option contract:

        \\[
        \\text{Change \\%} =
        \\frac{\\text{Current LTP} - \\text{Today's Open}}
        {\\text{Today's Open}} \\times 100
        \\]

        **2. Delta matching**

        CE and PE contracts are compared at:

        - 25 Delta
        - 30 Delta
        - 35 Delta
        - 40 Delta
        - 45 Delta
        - 50 Delta

        When the exact delta is unavailable, the percentage
        change is linearly interpolated between the surrounding
        contracts.

        **3. Difference for each bucket**

        \\[
        \\text{Difference} =
        \\text{CE Change \\%} - \\text{PE Change \\%}
        \\]

        **4. Relative Weakness Score**

        \\[
        RWS =
        \\frac{\\sum(CE Change \\% - PE Change \\%)}
        {\\text{Number of matched buckets}}
        \\]

        Positive score means PE relatively underperformed CE.

        Negative score means CE relatively underperformed PE.
        """
    )


st.caption(
    "Quotes fetched: "
    f"{st.session_state.get('option_quote_fetched_at', '—')} "
    "| Current LTP and open price use Quotes API. "
    "Delta uses Option Chain API Greeks. "
    "Reference strikes may be interpolated."
)


# ============================================================
# OPTION CHAIN TABLE
# ============================================================

st.divider()

st.markdown(
    '<div class="section-heading">'
    'Option Chain'
    '</div>',
    unsafe_allow_html=True
)

st.caption(
    "CALL options are on the left. PUT options are on the right. "
    "The ATM strike is marked in the middle."
)

if chain_df.empty:

    st.warning(
        "No option contracts were returned by FYERS."
    )

else:

    st.dataframe(
        chain_df,
        column_config=get_column_config(),
        width="stretch",
        height=680,
        hide_index=True
    )

    st.caption(
        f"Showing {len(chain_df)} unique strikes | "
        f"{strike_count} strikes above ATM + ATM + "
        f"{strike_count} strikes below ATM (where available)"
    )


# ============================================================
# EXPIRY INFORMATION
# ============================================================

with st.expander(
    "📅 Available Expiries",
    expanded=False
):

    expiry_data = api_data.get(
        "expiryData",
        []
    )

    if expiry_data:

        expiry_df = pd.DataFrame(
            expiry_data
        )

        expiry_df = expiry_df.rename(
            columns={

                "date": "Expiry Date",

                "expiry": "Expiry Timestamp",

                "expiry_flag": "Type"
            }
        )

        if "Type" in expiry_df.columns:

            expiry_df["Type"] = expiry_df[
                "Type"
            ].map({

                "W": "Weekly",

                "M": "Monthly"

            }).fillna(
                expiry_df["Type"]
            )

        st.dataframe(
            expiry_df,
            width="stretch",
            hide_index=True
        )

    else:

        st.warning(
            "No expiry information returned."
        )


# ============================================================
# UNDERLYING AND VIX DETAILS
# ============================================================

with st.expander(
    "📌 Underlying Details",
    expanded=False
):

    st.json(underlying)


with st.expander(
    "📈 India VIX Details",
    expanded=False
):

    st.json(vix_data)


# ============================================================
# RAW FYERS API RESPONSE
# ============================================================

with st.expander(
    "🛠️ Complete Raw FYERS API Response",
    expanded=False
):

    st.caption(
        "This contains the original option-chain response "
        "returned by FYERS."
    )

    st.json(response)


# ============================================================
# RAW QUOTES API DATA
# ============================================================

with st.expander(
    "📡 Quotes API Data",
    expanded=False
):

    st.caption(
        "This contains the fetched quotes for CE and PE contracts, "
        "including current LTP and today's opening price."
    )

    st.json(quote_map)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "FYERS Option Chain Dashboard | Read-only display | "
    "No order placement or trading functionality"
)