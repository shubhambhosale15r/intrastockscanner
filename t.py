"""NIFTY 500 relative-strength scanner (Fyers WebSocket + Streamlit).

Subscribes to the NIFTY 500 index and its constituents over the Fyers data
socket, then ranks stocks by (stock % change - NIFTY 500 % change) so you can
see the top 3 outperformers and top 3 underperformers live. Display only.

Run:  streamlit run t.py
"""

import threading
import time
from datetime import datetime
from io import BytesIO
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fyers_apiv3.FyersWebsocket import data_ws
from streamlit_autorefresh import st_autorefresh

IST = ZoneInfo("Asia/Kolkata")
INDEX_SYMBOL = "NSE:NIFTY500-INDEX"
INDEX_NAME = "NIFTY 500"
TOP_N = 3
STALE_AFTER_S = 15
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
SYMBOL_MASTER_CACHE_SECONDS = 24 * 60 * 60

_TICKER_TEXT = """
360ONE ABB ACC ACMESOLAR AIAENG APLAPOLLO AUBANK AWL AADHARHFC AARTIIND AAVAS
ABBOTINDIA ACE ACUTAAS ADANIENSOL ADANIENT ADANIGREEN ADANIPORTS ADANIPOWER ATGL
ABCAPITAL ABREL ABSLAMC CPPLUS AEGISLOG AEGISVOPAK AETHER AFCONS AFFLE AJANTPHARM
ALKEM ABDL ARE&M AMBER AMBUJACEM ANANDRATHI ANANTRAJ ANGELONE ANTHEM ANURAS
APARINDS APOLLOHOSP APOLLOTYRE APTUS ASAHIINDIA ASHOKLEY ASIANPAINT ASTERDM ASTRAL
ATHERENERG ATUL AUROPHARMA AIIL AVANTIFEED DMART AXISBANK AZAD BEML BLS BSE BAGMANE
BAJAJ-AUTO BAJFINANCE BAJAJFINSV BAJAJHLDNG BAJAJHFL BALKRISIND BALRAMCHIN
BANDHANBNK BANKBARODA BANKINDIA MAHABANK BATAINDIA BELRISE BERGEPAINT BHARATCOAL
BDL BEL BHARATFORG BHEL BPCL BHARTIARTL BHARTIHEXA GROWW BIOCON BSOFT BBOX
BLUESTARCO BOSCHLTD FIRSTCRY BRIGADE BRITANNIA BIRET CCL CESC CGPOWER CIEINDIA
CRISIL CANFINHOME CANBK CANHLIFE CAPLIPOINT CGCL CARBORUNIV CARTRADE CASTROLIND
CEATLTD CEMPRO CENTRALBK CDSL CMPDI CHAMBLFERT CHENNPETRO CHOICEIN CHOLAHLDNG
CHOLAFIN CIPLA CUB CLEANMAX CLEAN COALINDIA COCHINSHIP COFORGE COHANCE COLPAL CAMS
CONCORDBIO CONCOR COROMANDEL CRAFTSMAN CREDITACC CROMPTON CUMMINSIND CUPID CYIENT
DLF DOMS DABUR DALBHARAT DATAPATTNS DEEPAKFERT DEEPAKNTR DELHIVERY DEVYANI
DIVISLAB DIXON LALPATHLAB DRREDDY EIDPARRY EIHOTEL EMBASSY EICHERMOT ELECON
ELGIEQUIP EMAMILTD EMCURE EMMVEE ENDURANCE ENGINERSIN ESCORTS ETERNAL EXIDEIND
NYKAA FEDERALBNK FACT FINCABLES FSL FIVESTAR FORCEMOT FORTIS FRACTAL GAIL GVT&D
GMRAIRPORT GABRIEL GALLANTT GRSE GICRE GILLETTE GLAND GLAXO GLENMARK MEDANTA GPIL
GODFRYPHLP GODREJCP GODREJIND GODREJPROP GRANULES GRAPHITE GRASIM GRAVITA GESHIP
FLUOROCHEM GMDCLTD HBLENGINE HCLTECH HDBFS HDFCAMC HDFCBANK HDFCLIFE HEGAM HFCL
HAVELLS HEROMOTOCO HEXT HSCL HINDALCO HAL HINDCOPPER HINDPETRO HINDUNILVR HINDZINC
POWERINDIA HOMEFIRST HONASA HONAUT HUDCO HYUNDAI ICICIBANK ICICILIFE ICICIGI
ICICIAMC IDBI IDFCFIRSTB IFCI IIFL INOXINDIA IRB IRCON ITCHOTELS ITC ITI INDIACEM
INDIAMART INDIANB IEX INDHOTEL IOC IOB IRCTC IRFC IREDA IGL INDUSTOWER INDUSINDBK
NAUKRI INFY INOXWIND INTELLECT INDIGO IGIL IKS IPCALAB JKCEMENT JBMA JKTYRE
JMFINANCIL JSWCEMENT JSWENERGY JSWINFRA JSWSTEEL JAINREC JPPOWER J&KBANK
JINDALSAW JSL JINDALSTEL JIOFIN JUBLFOOD JUBLINGREA JWL JYOTICNC KPRMILL KEI
KPITTECH KSB KAJARIACER KPIL KALYANKJIL KARURVYSYA KAYNES KEC KFINTECH KIRLOSBROS
KIRLOSENG KOTAKBANK KIMS LTF LTTS LGEINDIA LICHSGFIN LTFOODS LTM LT LAURUSLABS
LEMONTREE LENSKART LICI LINDEINDIA LLOYDSME LODHA LUPIN MMTC MRF MTARTECH MGL
M&MFIN M&M MANAPPURAM MRPL MANKIND MARICO MARUTI MFSL MAXHEALTH MAZDOCK MEESHO
MINDACORP MSUMI MOTILALOFS MPHASIS MCX MUTHOOTFIN NATCOPHARM NBCC NCC NHPC
NLCINDIA NMDC NSLNISP NTPCGREEN NTPC NH NATIONALUM NAVA NAVINFLUOR NESTLEIND
NETWEB NEULANDLAB NAM-INDIA NUVAMA NUVOCO OBEROIRLTY ONGC OIL OLAELEC OLECTRA
PAYTM ONESOURCE OFSS POLICYBZR PCBL PGEL PIIND PNBHOUSING PTCIL PVRINOX PAGEIND
PARADEEP PATANJALI PERSISTENT PETRONET PHOENIXLTD PWL PIDILITIND PINELABS
PIRAMALFIN PPLPHARMA POLYMED POLYCAB POONAWALLA PFC POWERGRID PREMIERENE PRESTIGE
PFOCUS PRIVISCL PNB RRKABEL RBLBANK RECLTD RHIM RITES RADICO RVNL RAILTEL RAINBOW
RKFORGE REDINGTON RELIANCE RPOWER RUBICON SBICARD SBILIFE SJVN SHRIPISTON SRF
SAGILITY SAILIFE SAMMAANCAP MOTHERSON SANSERA SARDAEN SCHAEFFLER SCHNEIDER SCI
SHREECEM SHRIRAMFIN SHYAMMETL ENRIN SIEMENS SIGNATURE SOBHA SOLARINDS SONACOMS
STARHEALTH SBIN SAIL STLTECH SUMICHEM SUNPHARMA SUNTV SUNDARMFIN SUPREMEIND SUZLON
SWANCORP SWIGGY SYNGENE SYRMA TBOTEK TDPOWERSYS TVSMOTOR TATACAP TATACHEM TATACOMM
TCS TATACONSUM TATAELXSI TATAINVEST TMCV TMPV TATAPOWER TATASTEEL TATATECH TTML
TECHM TECHNOE TEGA TEJASNET TENNIND THANGAMAYIL NIACL RAMCOCEM THERMAX TIMKEN
TITAGARH TITAN TORNTPHARM TORNTPOWER TARIL TRENT TRIDENT TRITURBINE TIINDIA
UCOBANK UNOMINDA UPL UTIAMC ULTRACEMCO UNIONBANK UBL UNITDSPR URBANCO USHAMART VTL
VBL VAML VISL VEDL VOGL VEDPOWER VIJAYA VMM IDEA VOLTAS WAAREEENER WELCORP
WELSPUNLIV WHIRLPOOL WIPRO WOCKPHARMA YESBANK ZFCVINDIA ZEEL ZENTEC ZENSARTECH
ZYDUSLIFE ZYDUSWELL ECLERX
"""
TICKERS = list(dict.fromkeys(_TICKER_TEXT.split()))

# Aliases for names used in the scanner that differ from the FYERS underlying.
# Left side is the displayed/configured label; right side is the master underlying.
TICKER_ALIASES = {
    "THANGAMAYIL": "THANGAMAYL"
}

# If a ticker has multiple valid series, prefer the first listed series below.
# The selected exact symbol always comes from FYERS NSE_CM.csv.
SERIES_PRIORITY = ["EQ", "BE", "SM", "ST", "BZ", "SZ", "T", "XT", "BL", "IL", "RL"]


@st.cache_data(ttl=SYMBOL_MASTER_CACHE_SECONDS, show_spinner=False)
def load_nse_cm_symbol_master():
    """Load FYERS NSE cash-market symbols from the official CSV.

    The CSV is headerless. Relevant zero-based columns:
      1  = Symbol Details / company name
      9  = Symbol ticker (exact FYERS API symbol, e.g. NSE:ABB-EQ)
      13 = Underlying symbol (e.g. ABB)
    """
    request = Request(
        SYMBOL_MASTER_URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"},
    )
    with urlopen(request, timeout=30) as response:
        payload = response.read()

    if not payload:
        raise RuntimeError("FYERS returned an empty NSE_CM.csv file.")

    raw = pd.read_csv(
        BytesIO(payload),
        header=None,
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )
    if raw.shape[1] < 14:
        raise RuntimeError(
            f"Unexpected NSE_CM.csv format: expected at least 14 columns, got {raw.shape[1]}."
        )

    master = raw.iloc[:, [1, 9, 13]].copy()
    master.columns = ["symbol_details", "symbol", "underlying"]
    for col in master.columns:
        master[col] = master[col].astype(str).str.strip()

    master["symbol"] = master["symbol"].str.upper()
    master["underlying"] = master["underlying"].str.upper()
    master = master[
        master["symbol"].str.startswith("NSE:", na=False)
        & master["symbol"].ne("")
    ].copy()
    master = master.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)

    if master.empty:
        raise RuntimeError("No NSE symbols were found in the downloaded FYERS symbol master.")
    return master


def get_series(symbol):
    """Return suffix after the final hyphen, e.g. NSE:ABC-BE -> BE."""
    symbol = str(symbol).strip().upper()
    return symbol.rsplit("-", 1)[-1] if "-" in symbol else ""


def choose_exact_symbol(matches):
    """Select a master-listed symbol, avoiding guesses or arbitrary ties."""
    candidates = sorted(set(matches["symbol"].astype(str).str.upper()))
    if not candidates:
        return None, "No matching symbol"
    if len(candidates) == 1:
        return candidates[0], None

    priority = {series: rank for rank, series in enumerate(SERIES_PRIORITY)}
    ranked = [(priority.get(get_series(symbol), 999), symbol) for symbol in candidates]
    best_rank = min(rank for rank, _ in ranked)
    best = [symbol for rank, symbol in ranked if rank == best_rank]

    if len(best) == 1:
        return best[0], None
    return None, "Ambiguous symbols in FYERS master: " + ", ".join(best)


def resolve_universe_symbols(tickers, master):
    """Resolve display tickers to exact API symbols from FYERS's CSV."""
    resolved = {}
    unresolved = {}

    for ticker in tickers:
        label = str(ticker).strip().upper()
        lookup_key = TICKER_ALIASES.get(label, label)

        # Primary: match the FYERS underlying-symbol column exactly.
        matches = master[master["underlying"].eq(lookup_key)].copy()

        # Fallback: match the exact symbol root (suffix may vary).
        if matches.empty:
            prefix = f"NSE:{lookup_key}-"
            matches = master[master["symbol"].str.startswith(prefix, na=False)].copy()

        if matches.empty:
            unresolved[ticker] = (
                f"No match for lookup key '{lookup_key}' in NSE_CM.csv"
            )
            continue

        exact_symbol, reason = choose_exact_symbol(matches)
        if exact_symbol is None:
            unresolved[ticker] = reason
        else:
            resolved[ticker] = exact_symbol

    return resolved, unresolved


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class MarketWSManager:
    def __init__(self, access_token, symbols):
        self.access_token = access_token
        self.symbols = list(dict.fromkeys(symbols))
        self.ws = None
        self.ws_thread = None
        self.running = False
        self.connected = False
        self.last_error = None
        self.invalid = set()
        self.lock = threading.RLock()
        self.live_quotes = {}

    def on_message(self, message):
        if not isinstance(message, dict):
            return
        symbol = message.get("symbol")
        if not symbol:
            return  # connection / subscribe acknowledgements
        ltp = _num(message.get("ltp"))
        ch = _num(message.get("ch"))
        with self.lock:
            q = self.live_quotes.setdefault(symbol, {})
            if ltp is not None:
                q["ltp"] = ltp
            if ch is not None:
                q["ch"] = ch

            # Today's intraday percentage change: use open_price, NOT prev_close_price.
            # Keep the latest valid open in memory in case a later tick omits it.
            opn = _num(message.get("open_price"))
            if opn is not None and opn > 0:
                q["open"] = opn
            if q.get("ltp") is not None and q.get("open") is not None and q["open"] > 0:
                q["chp_open"] = (q["ltp"] - q["open"]) / q["open"] * 100
            vol = _num(message.get("vol_traded_today"))
            if vol is not None:
                q["volume"] = int(vol)
            q["time"] = time.time()

    def on_error(self, msg):
        bad = msg.get("invalid_symbols") if isinstance(msg, dict) else None
        if bad:
            # Rejected symbols are shown separately; they don't affect the rest.
            if isinstance(bad, str):
                bad = [bad]
            if isinstance(bad, (list, tuple, set)):
                with self.lock:
                    self.invalid.update(str(symbol) for symbol in bad)
            else:
                self.last_error = str(msg)
        else:
            self.last_error = str(msg)

    def on_close(self, msg):
        self.connected = False

    def on_open(self):
        self.connected = True
        try:
            # Subscribe in chunks to keep each request small.
            for i in range(0, len(self.symbols), 100):
                self.ws.subscribe(self.symbols[i:i + 100], "SymbolUpdate")
        except Exception as e:
            self.last_error = f"subscribe failed: {e}"
        self.ws.keep_running()

    def _ws_connect(self):
        try:
            self.ws = data_ws.FyersDataSocket(
                access_token=self.access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=self.on_open,
                on_close=self.on_close,
                on_error=self.on_error,
                on_message=self.on_message,
            )
            self.ws.connect()
        except Exception as e:
            self.last_error = str(e)

    def start(self):
        if self.running:
            return
        self.running = True
        self.ws_thread = threading.Thread(target=self._ws_connect, daemon=True)
        self.ws_thread.start()

    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close_connection()
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.live_quotes.items()}


def build_table(snap, symbols):
    index_q = snap.get(INDEX_SYMBOL, {})
    index_chp = index_q.get("chp_open")  # % move since today's open; used for ranking
    now = time.time()
    rows = []
    for ticker, sym in symbols.items():
        q = snap.get(sym, {})
        chp = q.get("chp_open")  # % move since today's open; used for ranking
        rel = (chp - index_chp) if (chp is not None and index_chp is not None) else None
        rows.append({
            "Symbol": ticker,
            "FYERS Symbol": sym,
            "Open": q.get("open"),
            "LTP": q.get("ltp"),
            "From open %": chp,
            f"{INDEX_NAME} %": index_chp,
            "Relative %": rel,
            "Volume": q.get("volume"),
            "Age (s)": round(now - q["time"], 1) if q.get("time") else None,
        })
    return index_q, pd.DataFrame(rows)


def fmt_table(df):
    return df.style.format({
        "Open": "{:,.2f}", "LTP": "{:,.2f}", "From open %": "{:+.2f}%",
        f"{INDEX_NAME} %": "{:+.2f}%",
        "Relative %": "{:+.2f}%", "Volume": "{:,.0f}", "Age (s)": "{:.1f}",
    }, na_rep="—")


def main():
    st.set_page_config(
        page_title=f"{INDEX_NAME} Relative Strength",
        page_icon="📈",
        layout="wide",
    )
    st.title(f"{INDEX_NAME} · Outperformers vs Underperformers")
    st.caption(
        f"Relative % = stock % move since today's open − {INDEX_NAME} % move since today's open. "
        "Exact stock symbols are resolved from FYERS NSE_CM.csv. Live via WebSocket. Display only."
    )

    with st.sidebar:
        st.header("Connection")
        client_id = st.text_input(
            "Client ID (app id)", placeholder="XXXXXXXX-100", key="client_id"
        )
        token = st.text_input("Access token", type="password", key="token_in")

        st.header("Filters")
        min_vol = st.number_input(
            "Min volume today", min_value=0, value=0, step=10000,
            help="Hide thinly traded stocks from the rankings.",
        )

        st.header("Refresh")
        auto = st.checkbox("Auto refresh", value=True)
        every = st.number_input("Interval (seconds)", 1, 60, 3, disabled=not auto)

        if st.button("Disconnect"):
            mgr = st.session_state.pop("ws_manager", None)
            if mgr:
                mgr.stop()
            st.session_state.pop("ws_token", None)
            st.session_state.pop("ws_symbols", None)
            st.rerun()

    if auto:
        st_autorefresh(interval=int(every) * 1000, key="rs_refresh")

    if not token:
        st.info("Enter your Fyers client ID and access token in the sidebar to start.")
        return

    # Load the official symbol master. Do not guess a suffix if it fails.
    try:
        with st.spinner("Loading FYERS NSE symbol master..."):
            master = load_nse_cm_symbol_master()
            symbols, unresolved = resolve_universe_symbols(TICKERS, master)
    except Exception as exc:
        st.error(
            "Could not load or parse FYERS NSE_CM.csv. The scanner will not "
            "fall back to guessed -EQ symbols. Check your network and try again."
        )
        st.exception(exc)
        return

    if not symbols:
        st.error("No configured tickers could be resolved from FYERS NSE_CM.csv.")
        if unresolved:
            st.dataframe(
                pd.DataFrame([{"Ticker": t, "Reason": reason} for t, reason in unresolved.items()]),
                hide_index=True,
                width="stretch",
            )
        return

    full_token = token if ":" in token else f"{client_id}:{token}"
    desired_symbols = [INDEX_SYMBOL] + list(dict.fromkeys(symbols.values()))

    # Recreate the socket if credentials or the resolved universe changes.
    if (
        st.session_state.get("ws_token") != full_token
        or st.session_state.get("ws_symbols") != desired_symbols
    ):
        old = st.session_state.get("ws_manager")
        if old:
            old.stop()

        mgr = MarketWSManager(full_token, desired_symbols)
        mgr.start()
        st.session_state.ws_manager = mgr
        st.session_state.ws_token = full_token
        st.session_state.ws_symbols = desired_symbols

    mgr = st.session_state.ws_manager
    index_q, df = build_table(mgr.snapshot(), symbols)

    # Show mapping and resolution details so a name mismatch is easy to diagnose.
    with st.expander(
        f"FYERS symbol master · {len(symbols)} resolved, {len(unresolved)} unresolved",
        expanded=False,
    ):
        st.caption(
            f"Source: {SYMBOL_MASTER_URL}. Cached for up to 24 hours. "
            "The displayed Symbol is your configured label; FYERS Symbol is the exact subscription symbol."
        )
        mapping_rows = [
            {"Ticker": ticker, "FYERS Symbol": symbol, "Series": get_series(symbol)}
            for ticker, symbol in symbols.items()
        ]
        st.dataframe(
            pd.DataFrame(mapping_rows),
            hide_index=True,
            width="stretch",
            height=350,
        )
        if mapping_rows:
            st.download_button(
                "Download resolved symbol map",
                pd.DataFrame(mapping_rows).to_csv(index=False).encode("utf-8"),
                file_name="fyers_resolved_symbols.csv",
                mime="text/csv",
            )
        if unresolved:
            st.warning(f"{len(unresolved)} configured ticker(s) could not be resolved.")
            unresolved_df = pd.DataFrame(
                [{"Ticker": ticker, "Reason": reason} for ticker, reason in unresolved.items()]
            )
            st.dataframe(unresolved_df, hide_index=True, width="stretch")

    live = df.dropna(subset=["Relative %"]).copy()
    if min_vol:
        live = live[live["Volume"].fillna(0) >= min_vol]

    invalid = set(mgr.invalid)
    rejected_tickers = {t for t, sym in symbols.items() if sym in invalid}
    expected_count = max(len(symbols) - len(rejected_tickers), 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("WebSocket", "Connected" if mgr.connected else "Connecting…")

    index_ltp = index_q.get("ltp")
    index_change = index_q.get("chp_open")
    c2.metric(
        INDEX_NAME,
        f"{index_ltp:,.2f}" if index_ltp is not None else "—",
        f"{index_change:+.2f}% from open" if index_change is not None else None,
    )
    c3.metric("Stocks ranked", f"{len(live)} / {expected_count}")

    stale = df[df["Age (s)"].notna() & (df["Age (s)"] > STALE_AFTER_S)]
    c4.metric(
        f"Quiet (>{STALE_AFTER_S}s)",
        len(stale),
        help="Resolved stocks with an old last tick. An illiquid stock can legitimately go quiet.",
    )

    if mgr.last_error:
        st.warning(f"WebSocket message: {mgr.last_error}")

    if index_change is None:
        st.info(
            f"Waiting for the {INDEX_NAME} index tick with an open price. "
            "If this never clears, check whether the index feed is sending open_price."
        )
        with st.expander("Raw index quote"):
            st.json(index_q)
        return

    if live.empty:
        st.info("Waiting for stock ticks with both LTP and today's open price.")
        return

    ranked = live.sort_values("Relative %", ascending=False).reset_index(drop=True)
    top = ranked.head(TOP_N).copy()
    bottom = ranked.tail(TOP_N).sort_values("Relative %", ascending=True).copy()

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader(f"🟢 Top {TOP_N} outperformers")
        st.dataframe(fmt_table(top), hide_index=True, width="stretch")
    with right:
        st.subheader(f"🔴 Top {TOP_N} underperformers")
        st.dataframe(fmt_table(bottom), hide_index=True, width="stretch")

    st.subheader("Extremes · top 15 and bottom 15 by relative %")
    extremes = pd.concat([ranked.head(15), ranked.tail(15)]).drop_duplicates("Symbol")
    st.bar_chart(extremes.set_index("Symbol")["Relative %"], height=320)

    with st.expander("Full ranked table", expanded=False):
        search = st.text_input("Filter symbol", key="sym_filter").strip().upper()
        view = ranked[ranked["Symbol"].str.contains(search, regex=False)] if search else ranked
        st.dataframe(fmt_table(view), hide_index=True, width="stretch", height=520)
        st.download_button(
            "Export CSV",
            ranked.to_csv(index=False).encode("utf-8"),
            file_name=f"nifty500_relative_{datetime.now(IST):%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
        )

    if invalid:
        with st.expander(f"Rejected by FYERS · {len(invalid)} symbols"):
            st.write(", ".join(sorted(invalid)))
            st.caption(
                "These symbols were rejected by the live WebSocket. Compare them against "
                "the exact FYERS Symbol column in the symbol-master mapping above."
            )

    missing = [
        ticker
        for ticker in df.loc[df["Relative %"].isna(), "Symbol"].tolist()
        if ticker not in rejected_tickers
    ]
    if missing:
        with st.expander(f"No usable quote yet · {len(missing)} symbols"):
            st.write(", ".join(missing))
            st.caption(
                "These symbols have not yet received both LTP and today's open price. "
                "They may be waiting for their first tick."
            )

    st.caption(f"Updated {datetime.now(IST):%d %b %Y %I:%M:%S %p IST}")


if __name__ == "__main__":
    main()
