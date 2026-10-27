"""NIFTY 500 relative-strength scanner (Fyers WebSocket + Streamlit).

Subscribes to the NIFTY 500 index and its constituents over the Fyers data
socket, then ranks stocks by (stock % change - NIFTY 500 % change) so you can
see the top 3 outperformers and top 3 underperformers live. Display only.

Run:  streamlit run t.py
"""

import threading
import time
from datetime import datetime
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
TECHM TECHNOE TEGA TEJASNET TENNIND THANGAMAYL NIACL RAMCOCEM THERMAX TIMKEN
TITAGARH TITAN TORNTPHARM TORNTPOWER TARIL TRENT TRIDENT TRITURBINE TIINDIA
UCOBANK UNOMINDA UPL UTIAMC ULTRACEMCO UNIONBANK UBL UNITDSPR URBANCO USHAMART VTL
VBL VAML VISL VEDL VOGL VEDPOWER VIJAYA VMM IDEA VOLTAS WAAREEENER WELCORP
WELSPUNLIV WHIRLPOOL WIPRO WOCKPHARMA YESBANK ZFCVINDIA ZEEL ZENTEC ZENSARTECH
ZYDUSLIFE ZYDUSWELL ECLERX
"""
TICKERS = list(dict.fromkeys(_TICKER_TEXT.split()))

# Some instruments trade in a series other than EQ (e.g. REITs). Add overrides
# here as "TICKER": "SERIES" if a symbol never ticks. Example: {"EMBASSY": "RR"}
SERIES_OVERRIDE = {}
SYMBOLS = {t: f"NSE:{t}-{SERIES_OVERRIDE.get(t, 'EQ')}" for t in TICKERS}


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
        self.lock = threading.RLock()
        self.live_quotes = {}

    def on_message(self, message):
        if not isinstance(message, dict):
            return
        symbol = message.get("symbol")
        if not symbol:
            return  # connection / subscribe acknowledgements
        ltp = _num(message.get("ltp"))
        prev = _num(message.get("prev_close_price"))
        chp = _num(message.get("chp"))
        ch = _num(message.get("ch"))
        with self.lock:
            q = self.live_quotes.setdefault(symbol, {})
            if ltp is not None:
                q["ltp"] = ltp
            if prev is not None and prev > 0:
                q["prev"] = prev
            if ch is not None:
                q["ch"] = ch
            if chp is not None:
                q["chp"] = chp
            elif q.get("ltp") is not None and q.get("prev"):
                q["chp"] = (q["ltp"] - q["prev"]) / q["prev"] * 100
            vol = _num(message.get("vol_traded_today"))
            if vol is not None:
                q["volume"] = int(vol)
            q["time"] = time.time()

    def on_error(self, msg):
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


def build_table(snap):
    index_q = snap.get(INDEX_SYMBOL, {})
    index_chp = index_q.get("chp")
    now = time.time()
    rows = []
    for ticker, sym in SYMBOLS.items():
        q = snap.get(sym, {})
        chp = q.get("chp")
        rel = (chp - index_chp) if (chp is not None and index_chp is not None) else None
        rows.append({
            "Symbol": ticker,
            "LTP": q.get("ltp"),
            "Change %": chp,
            f"{INDEX_NAME} %": index_chp,
            "Relative %": rel,
            "Volume": q.get("volume"),
            "Age (s)": round(now - q["time"], 1) if q.get("time") else None,
        })
    return index_q, pd.DataFrame(rows)


def fmt_table(df):
    return df.style.format({
        "LTP": "{:,.2f}", "Change %": "{:+.2f}%", f"{INDEX_NAME} %": "{:+.2f}%",
        "Relative %": "{:+.2f}%", "Volume": "{:,.0f}", "Age (s)": "{:.1f}",
    }, na_rep="—")


def main():
    st.set_page_config(page_title=f"{INDEX_NAME} Relative Strength", page_icon="📈", layout="wide")
    st.title(f"{INDEX_NAME} · Outperformers vs Underperformers")
    st.caption(
        f"Relative % = stock % change today − {INDEX_NAME} % change today. "
        "Live via Fyers WebSocket. Display only."
    )

    with st.sidebar:
        st.header("Connection")
        client_id = st.text_input("Client ID (app id)", placeholder="XXXXXXXX-100", key="client_id")
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
            st.rerun()

    if auto:
        st_autorefresh(interval=int(every) * 1000, key="rs_refresh")

    if not token:
        st.info("Enter your Fyers client ID and access token in the sidebar to start.")
        return

    full_token = token if ":" in token else f"{client_id}:{token}"
    if st.session_state.get("ws_token") != full_token:
        old = st.session_state.get("ws_manager")
        if old:
            old.stop()
        mgr = MarketWSManager(full_token, [INDEX_SYMBOL] + list(SYMBOLS.values()))
        mgr.start()
        st.session_state.ws_manager = mgr
        st.session_state.ws_token = full_token
    mgr = st.session_state.ws_manager

    index_q, df = build_table(mgr.snapshot())
    live = df.dropna(subset=["Relative %"])
    if min_vol:
        live = live[live["Volume"].fillna(0) >= min_vol]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("WebSocket", "Connected" if mgr.connected else "Connecting…")
    c2.metric(INDEX_NAME, f"{index_q['ltp']:,.2f}" if index_q.get("ltp") else "—",
              f"{index_q['chp']:+.2f}%" if index_q.get("chp") is not None else None)
    c3.metric("Stocks ranked", f"{len(live)} / {len(TICKERS)}")
    stale = df[df["Age (s)"].fillna(0) > STALE_AFTER_S]
    c4.metric(f"Quiet (>{STALE_AFTER_S}s)", len(stale),
              help="Illiquid names in a 500-stock universe can legitimately go quiet.")

    if mgr.last_error:
        st.warning(f"Socket message: {mgr.last_error}")

    if index_q.get("chp") is None:
        st.info(f"Waiting for the {INDEX_NAME} index tick…")
        return
    if live.empty:
        st.info("Waiting for stock ticks…")
        return

    ranked = live.sort_values("Relative %", ascending=False).reset_index(drop=True)
    top = ranked.head(TOP_N).copy()
    bottom = ranked.tail(TOP_N).sort_values("Relative %").copy()

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
            "Export CSV", ranked.to_csv(index=False).encode(),
            file_name=f"nifty500_relative_{datetime.now(IST):%Y%m%d_%H%M%S}.csv", mime="text/csv",
        )

    missing = df[df["Relative %"].isna()]["Symbol"].tolist()
    if missing:
        with st.expander(f"No tick yet · {len(missing)} symbols"):
            st.write(", ".join(missing))
            st.caption("Persistent entries may use a non-EQ series or a different FYERS symbol; "
                       "adjust SERIES_OVERRIDE in the code.")
    st.caption(f"Updated {datetime.now(IST):%d %b %Y %I:%M:%S %p IST}")


if __name__ == "__main__":
    main()
