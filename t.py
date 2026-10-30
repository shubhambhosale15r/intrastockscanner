"""NIFTY 500 relative-strength scanner (Fyers WebSocket + Streamlit).

Subscribes to the NIFTY 500 index and its constituents over the Fyers data
socket, then ranks stocks by (stock % change - NIFTY 500 % change) so you can
see the top N outperformers and top N underperformers live. Display only.
N is configurable from the sidebar (default 5).

Run:  streamlit run t.py
"""

import threading
import time
from datetime import datetime, time as dtime
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
TOP_N = 5  # default value for the "Top N to show" input
STALE_AFTER_S = 15
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
SYMBOL_MASTER_CACHE_SECONDS = 24 * 60 * 60

# ---- Paper trading ----------------------------------------------------------
MARKET_OPEN = dtime(9, 15)

# Intraday equity charges (per executed order, NSE).
BROKERAGE_PCT = 0.0003        # 0.03% ...
BROKERAGE_CAP = 20.0          # ... or Rs. 20 per executed order, whichever is lower
STT_SELL_PCT = 0.00025        # 0.025% on the sell side
TXN_PCT = 0.0000307           # NSE transaction charges 0.00307%
SEBI_PER_CRORE = 10.0         # Rs. 10 / crore
GST_PCT = 0.18                # 18% on (brokerage + SEBI + transaction charges)
STAMP_BUY_PCT = 0.00003       # 0.003% on the buy side

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
            # Average traded price (VWAP-like) for the day, sent by Fyers as avg_trade_price.
            atp = _num(message.get("avg_trade_price"))
            if atp is not None and atp > 0:
                q["atp"] = atp
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
            "ATP": q.get("atp"),
            "From open %": chp,
            f"{INDEX_NAME} %": index_chp,
            "Relative %": rel,
            "Volume": q.get("volume"),
            "Age (s)": round(now - q["time"], 1) if q.get("time") else None,
        })
    df = pd.DataFrame(rows)
    # Columns that are still empty (e.g. no ATP tick yet) would otherwise be object
    # dtype full of None, which breaks numeric comparisons. Force them to float/NaN.
    for col in ["Open", "LTP", "ATP", "From open %", f"{INDEX_NAME} %",
                "Relative %", "Volume", "Age (s)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return index_q, df


def fmt_table(df):
    return df.style.format({
        "Open": "{:,.2f}", "LTP": "{:,.2f}", "ATP": "{:,.2f}", "From open %": "{:+.2f}%",
        f"{INDEX_NAME} %": "{:+.2f}%",
        "Relative %": "{:+.2f}%", "Volume": "{:,.0f}", "Age (s)": "{:.1f}",
    }, na_rep="—")


def new_paper_state():
    return {
        "positions": {},      # ticker -> open position dict
        "trades": [],         # closed trades
        "last_key": None,     # (date, clock slot) of the last rebalance
        "was_on": False,
        "rounds": 0,
        "last_rebalance": None,
        "last_per_stock": None,
        "last_round": None,
        "history": [],
    }


def calc_charges(side, turnover):
    """Charges for ONE executed intraday-equity order (side is 'BUY' or 'SELL')."""
    if turnover <= 0:
        return 0.0
    brokerage = min(turnover * BROKERAGE_PCT, BROKERAGE_CAP)
    txn = turnover * TXN_PCT
    sebi = turnover * SEBI_PER_CRORE / 1e7
    gst = GST_PCT * (brokerage + sebi + txn)
    stt = turnover * STT_SELL_PCT if side == "SELL" else 0.0
    stamp = turnover * STAMP_BUY_PCT if side == "BUY" else 0.0
    return brokerage + txn + sebi + gst + stt + stamp


def clock_slot(now, interval):
    """Clock-aligned slot number: 15-min interval -> 11:00, 11:15, 11:30 ..."""
    return (now.hour * 60 + now.minute) // int(interval)


def next_rebalance_label(now, interval, square_off):
    interval = int(interval)
    cur = now.hour * 60 + now.minute
    open_min = MARKET_OPEN.hour * 60 + MARKET_OPEN.minute
    base = max(cur + 1, open_min)
    nxt_min = -(-base // interval) * interval  # ceil to the next multiple
    if nxt_min >= square_off.hour * 60 + square_off.minute:
        return f"Square-off {square_off:%H:%M}"
    return f"{nxt_min // 60:02d}:{nxt_min % 60:02d}"


def paper_close_one(state, cfg, now, ltp_map, sym, reason):
    pos = state["positions"].get(sym)
    if pos is None:
        return
    exit_px = float(ltp_map.get(sym, pos["last_px"]))
    qty = pos["qty"]
    if pos["side"] == "LONG":
        gross = (exit_px - pos["entry"]) * qty
        exit_side = "SELL"
    else:
        gross = (pos["entry"] - exit_px) * qty
        exit_side = "BUY"
    exit_charges = calc_charges(exit_side, exit_px * qty) if cfg["charges_on"] else 0.0
    charges = pos["entry_charges"] + exit_charges
    state["trades"].append({
        "Symbol": sym,
        "Side": pos["side"],
        "Qty": qty,
        "Entry": pos["entry"],
        "Exit": exit_px,
        "Entry time": pos["entry_time"],
        "Exit time": now,
        "Gross P&L": gross,
        "Charges": charges,
        "Net P&L": gross - charges,
        "Reason": reason,
    })
    del state["positions"][sym]


def paper_close_all(state, cfg, now, ltp_map, reason):
    for sym in list(state["positions"]):
        paper_close_one(state, cfg, now, ltp_map, sym, reason)


def paper_open(state, cfg, now, sym, side, ltp, per_stock):
    if ltp is None or ltp <= 0:
        return
    qty = int(per_stock / ltp)
    if qty < 1:
        return
    entry_side = "BUY" if side == "LONG" else "SELL"
    charges = calc_charges(entry_side, ltp * qty) if cfg["charges_on"] else 0.0
    state["positions"][sym] = {
        "side": side,
        "qty": qty,
        "entry": float(ltp),
        "entry_time": now,
        "entry_charges": charges,
        "last_px": float(ltp),
    }


def paper_step(state, cfg, now, top, bottom, ltp_map, force=False):
    """Advance the paper-trading engine by one refresh."""
    for sym, pos in state["positions"].items():
        if sym in ltp_map:
            pos["last_px"] = float(ltp_map[sym])

    if not cfg["on"]:
        if state["positions"]:
            paper_close_all(state, cfg, now, ltp_map, "paper mode off")
        state["was_on"] = False
        return

    key = (now.date(), clock_slot(now, cfg["interval"]))
    if not state["was_on"]:
        # First rebalance happens at the NEXT round boundary, not mid-slot.
        state["was_on"] = True
        state["last_key"] = key

    t = now.time()
    if t >= cfg["square_off"]:
        if state["positions"]:
            paper_close_all(state, cfg, now, ltp_map, "square-off")
        return
    if t < MARKET_OPEN:
        return
    if state["last_key"] == key and not force:
        return

    state["last_key"] = key

    # Target book for this round: symbol -> (side, ltp).
    target = {r["Symbol"]: ("LONG", r["LTP"]) for _, r in top.iterrows()}
    target.update({r["Symbol"]: ("SHORT", r["LTP"]) for _, r in bottom.iterrows()})
    state["rounds"] += 1
    state["last_rebalance"] = now

    # Exit only positions that dropped out of the lists (or flipped side).
    # A stock that is still in the same list is held untouched: no exit, no re-entry.
    exited = 0
    for sym, pos in list(state["positions"].items()):
        want = target.get(sym)
        if want is None or want[0] != pos["side"]:
            paper_close_one(state, cfg, now, ltp_map, sym, "rebalance")
            exited += 1
    held = len(state["positions"])

    if not target or cfg["capital"] <= 0:
        state["last_per_stock"] = None
        state["last_round"] = {"held": held, "exited": exited, "entered": 0}
        return

    # New entries get an equal share of capital across ALL target stocks.
    per_stock = cfg["capital"] / len(target)
    state["last_per_stock"] = per_stock
    entered = 0
    for sym, (side, ltp) in target.items():
        if sym in state["positions"]:
            continue
        paper_open(state, cfg, now, sym, side, ltp, per_stock)
        if sym in state["positions"]:
            entered += 1
    state["last_round"] = {"held": held, "exited": exited, "entered": entered}


def render_paper_section(state, cfg, now, ltp_map):
    st.subheader("📝 Paper trading")
    if not cfg["on"] and not state["trades"] and not state["positions"]:
        st.info("Paper trade mode is off. Turn it on in the sidebar to start.")
        return

    trades_df = pd.DataFrame(state["trades"])
    per_sym = {}  # symbol -> running totals for the live per-stock table
    if not trades_df.empty:
        for sym, g in trades_df.groupby("Symbol"):
            per_sym[sym] = {
                "Status": "Closed", "Trades": len(g),
                "Realized": float(g["Gross P&L"].sum()), "Unrealized": 0.0,
                "Charges": float(g["Charges"].sum()),
            }

    pos_rows = []
    unreal = invested = open_entry_charges = est_exit_charges = 0.0
    for sym, p in state["positions"].items():
        ltp = ltp_map.get(sym, p["last_px"])
        pnl = (ltp - p["entry"]) * p["qty"] if p["side"] == "LONG" else (p["entry"] - ltp) * p["qty"]
        exit_side = "SELL" if p["side"] == "LONG" else "BUY"
        est_exit = calc_charges(exit_side, ltp * p["qty"]) if cfg["charges_on"] else 0.0
        unreal += pnl
        invested += p["entry"] * p["qty"]
        open_entry_charges += p["entry_charges"]
        est_exit_charges += est_exit
        row = per_sym.setdefault(sym, {"Status": "", "Trades": 0, "Realized": 0.0,
                                       "Unrealized": 0.0, "Charges": 0.0})
        row["Status"] = f"Open {p['side']}"
        row["Unrealized"] += pnl
        row["Charges"] += p["entry_charges"] + est_exit
        pos_rows.append({
            "Symbol": sym, "Side": p["side"], "Qty": p["qty"], "Entry": p["entry"],
            "LTP": ltp, "Invested": p["entry"] * p["qty"], "Unrealized P&L": pnl,
            "Entry time": p["entry_time"].strftime("%H:%M:%S"),
        })

    realized_gross = float(trades_df["Gross P&L"].sum()) if not trades_df.empty else 0.0
    closed_charges = float(trades_df["Charges"].sum()) if not trades_df.empty else 0.0
    # Open positions count their entry charges plus the estimated exit charges, so the
    # live net P&L converges to the final number once everything is squared off.
    total_charges = closed_charges + open_entry_charges + est_exit_charges
    net_total = realized_gross + unreal - total_charges
    gross_total = realized_gross + unreal

    # Net P&L history for the live curve (one point per refresh, capped).
    hist = state.setdefault("history", [])
    if not hist or (now - hist[-1][0]).total_seconds() >= 1:
        hist.append((now, net_total, gross_total))
        if len(hist) > 5000:
            del hist[: len(hist) - 5000]

    ret_pct = (net_total / cfg["capital"] * 100) if cfg["capital"] > 0 else None

    # ---- Live P&L --------------------------------------------------------------
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Next rebalance",
              next_rebalance_label(now, cfg["interval"], cfg["square_off"]) if cfg["on"] else "Off")
    c2.metric("Open positions", len(state["positions"]),
              help=f"Rebalances done: {state['rounds']}")
    c3.metric("Realized (gross)", f"₹{realized_gross:,.2f}")
    c4.metric("Unrealized", f"₹{unreal:,.2f}")
    c5.metric("Charges", f"₹{total_charges:,.2f}" if cfg["charges_on"] else "Off",
              help="Includes estimated exit charges on open positions.")
    c6.metric("Net P&L", f"₹{net_total:,.2f}",
              f"{ret_pct:+.2f}% of capital" if ret_pct is not None else None)

    if state["last_rebalance"] is not None:
        per = state["last_per_stock"]
        lr = state.get("last_round") or {}
        st.caption(
            f"Last rebalance {state['last_rebalance']:%H:%M:%S} · "
            + (f"₹{per:,.2f} per new stock · invested ₹{invested:,.2f}"
               if per else "no qualifying stocks, stayed flat")
            + (f" · held {lr.get('held', 0)}, exited {lr.get('exited', 0)}, "
               f"entered {lr.get('entered', 0)}" if lr else "")
        )

    st.markdown("**Open positions**")
    if pos_rows:
        st.dataframe(
            pd.DataFrame(pos_rows).style.format({
                "Entry": "{:,.2f}", "LTP": "{:,.2f}", "Invested": "{:,.2f}",
                "Unrealized P&L": "{:+,.2f}",
            }, na_rep="—"),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("No open positions.")

    # ---- Day summary (live until square-off, final afterwards) ----------------
    is_final = (not state["positions"]) and (not trades_df.empty) \
        and now.time() >= cfg["square_off"]
    st.markdown(f"**Day summary · {'FINAL' if is_final else 'LIVE'}**")

    sym_rows = []
    for sym, v in per_sym.items():
        sym_rows.append({
            "Symbol": sym, "Status": v["Status"], "Closed trades": v["Trades"],
            "Realized": v["Realized"], "Unrealized": v["Unrealized"],
            "Charges": v["Charges"],
            "Net P&L": v["Realized"] + v["Unrealized"] - v["Charges"],
        })
    sym_df = pd.DataFrame(sym_rows)

    s1, s2, s3, s4 = st.columns(4)
    n_closed = len(trades_df)
    win_rate = float((trades_df["Net P&L"] > 0).mean() * 100) if n_closed else None
    s1.metric("Closed trades", n_closed,
              f"win rate {win_rate:.0f}%" if win_rate is not None else None, delta_color="off")
    if not sym_df.empty:
        best = sym_df.loc[sym_df["Net P&L"].idxmax()]
        worst = sym_df.loc[sym_df["Net P&L"].idxmin()]
        s2.metric("Best stock", best["Symbol"], f"₹{best['Net P&L']:+,.2f}")
        s3.metric("Worst stock", worst["Symbol"], f"₹{worst['Net P&L']:+,.2f}")
    else:
        s2.metric("Best stock", "—")
        s3.metric("Worst stock", "—")
    s4.metric("Net P&L" + (" (final)" if is_final else " (live)"), f"₹{net_total:,.2f}")

    if not sym_df.empty:
        sym_df = sym_df.sort_values("Net P&L", ascending=False).reset_index(drop=True)
        total_row = pd.DataFrame([{
            "Symbol": "TOTAL", "Status": "", "Closed trades": int(sym_df["Closed trades"].sum()),
            "Realized": realized_gross, "Unrealized": unreal,
            "Charges": total_charges, "Net P&L": net_total,
        }])
        sym_view = pd.concat([sym_df, total_row], ignore_index=True)
        st.dataframe(
            sym_view.style.format({
                "Realized": "{:+,.2f}", "Unrealized": "{:+,.2f}",
                "Charges": "{:,.2f}", "Net P&L": "{:+,.2f}",
            }, na_rep="—"),
            hide_index=True, width="stretch",
        )
        st.download_button(
            "Export day summary CSV",
            sym_view.to_csv(index=False).encode("utf-8"),
            file_name=f"paper_summary_{now:%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
        )

    if len(hist) >= 2:
        curve = pd.DataFrame(hist, columns=["Time", "Net P&L", "Gross P&L"]).set_index("Time")
        st.line_chart(curve, height=220)

    if not trades_df.empty:
        with st.expander(f"Closed trades · {len(trades_df)}", expanded=False):
            view = trades_df.iloc[::-1].copy()
            view["Entry time"] = view["Entry time"].dt.strftime("%H:%M:%S")
            view["Exit time"] = view["Exit time"].dt.strftime("%H:%M:%S")
            st.dataframe(
                view.style.format({
                    "Entry": "{:,.2f}", "Exit": "{:,.2f}", "Gross P&L": "{:+,.2f}",
                    "Charges": "{:,.2f}", "Net P&L": "{:+,.2f}",
                }, na_rep="—"),
                hide_index=True, width="stretch", height=360,
            )
            st.download_button(
                "Export trades CSV",
                view.to_csv(index=False).encode("utf-8"),
                file_name=f"paper_trades_{now:%Y%m%d_%H%M%S}.csv",
                mime="text/csv",
            )


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
        top_n = st.number_input(
            "Top N to show", min_value=1, max_value=100, value=TOP_N, step=1,
            help="How many top outperformers and underperformers to display.",
        )
        min_vol = st.number_input(
            "Min volume today", min_value=0, value=0, step=10000,
            help="Hide thinly traded stocks from the rankings.",
        )

        st.header("Paper trading")
        paper_on = st.checkbox(
            "Paper trade mode", value=False,
            help="Long the top outperformers and short the top underperformers, "
                 "rebalancing on every round interval.",
        )
        paper_capital = st.number_input(
            "Capital (₹)", min_value=0.0, value=100000.0, step=1000.0,
            help="Split equally across all selected stocks at each rebalance.",
        )
        rebalance_min = st.number_input(
            "Rebalance every (minutes)", min_value=1, max_value=240, value=15, step=1,
            help="Clock-aligned: 15 → 11:00, 11:15, 11:30, 11:45 …",
        )
        square_off_t = st.time_input(
            "Square-off time", value=dtime(15, 5),
            help="All positions are closed at this time and no new ones are opened.",
        )
        charges_on = st.checkbox(
            "Include charges (intraday equity)", value=False,
            help="Brokerage, STT, transaction, SEBI, GST and stamp charges on every order.",
        )
        rebalance_now = st.button("Rebalance now")
        reset_paper = st.button("Reset paper account")

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

    if reset_paper or "paper" not in st.session_state:
        st.session_state["paper"] = new_paper_state()

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
    n = int(top_n)

    # Outperformers must trade above their average traded price (ATP);
    # underperformers must trade below it. Stocks without an ATP are excluded.
    ltp_s = pd.to_numeric(ranked["LTP"], errors="coerce")
    atp_s = pd.to_numeric(ranked["ATP"], errors="coerce")
    valid = ltp_s.notna() & atp_s.notna()
    above_atp = ranked[valid & (ltp_s > atp_s)]
    below_atp = ranked[valid & (ltp_s < atp_s)]
    top = above_atp.sort_values("Relative %", ascending=False).head(n).copy()
    bottom = below_atp.sort_values("Relative %", ascending=True).head(n).copy()

    # Paper trading engine: runs once per refresh on the current top/bottom lists.
    now_ist = datetime.now(IST)
    paper = st.session_state["paper"]
    paper_cfg = {
        "on": paper_on,
        "capital": float(paper_capital),
        "interval": int(rebalance_min),
        "square_off": square_off_t,
        "charges_on": charges_on,
    }
    ltp_map = df.set_index("Symbol")["LTP"].dropna().to_dict()
    paper_step(paper, paper_cfg, now_ist, top, bottom, ltp_map,
               force=bool(rebalance_now and paper_on))

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader(f"🟢 Top {n} outperformers (LTP > ATP)")
        if top.empty:
            st.info("No stocks currently qualify (need LTP above average traded price).")
        else:
            st.dataframe(fmt_table(top), hide_index=True, width="stretch")
    with right:
        st.subheader(f"🔴 Top {n} underperformers (LTP < ATP)")
        if bottom.empty:
            st.info("No stocks currently qualify (need LTP below average traded price).")
        else:
            st.dataframe(fmt_table(bottom), hide_index=True, width="stretch")

    st.subheader(f"Extremes · top {n} and bottom {n} by relative % (ATP-filtered)")
    extremes = pd.concat([top, bottom]).drop_duplicates("Symbol")
    st.bar_chart(extremes.set_index("Symbol")["Relative %"], height=320)

    render_paper_section(paper, paper_cfg, now_ist, ltp_map)

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
