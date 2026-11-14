"""
IRCTC booking assistant (semi-automatic, built for a slow/busy site).

What it does:
  - Opens the IRCTC train-search page (retries if the site is slow or errors)
  - Skips the intro tour if it shows up
  - Clicks LOGIN, fills your username/password, clicks Sign In
  - Fills FROM/TO stations, quota, configurable journey date
  - Clicks Search Trains
  - Dismisses the post-search "skip" popup, opens filters, applies them —
    each step is optional; if the element isn't shown we just continue
  - Clicks every "Check availability" button on the train list
  - Stops so you can pick the train, enter passenger details and pay yourself

Setup:
  pip install playwright
  playwright install chromium

Run (set credentials first):
  Windows:    set IRCTC_USER=you  &  set IRCTC_PASS=secret  &  python irctc_assist.py
  Mac/Linux:  IRCTC_USER=you IRCTC_PASS=secret python irctc_assist.py
  Add IRCTC_DEBUG=1 to dump all visible form elements after login.
"""

import os
import re
import time
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------- EDIT THESE ----------------
FROM_STATION = "KYN"
TO_STATION   = "BGM"
JOURNEY_DATE = "15/11/2026"           # DD/MM/YYYY — target travel date
TRAVEL_CLASS = "Sleeper (SL)"         # (not used yet)
QUOTA        = "TATKAL"               # GENERAL | TATKAL | LADIES | ...
# --------------------------------------------

URL = "https://www.irctc.co.in/eticket/train-search"

try:
    datetime.strptime(JOURNEY_DATE, "%d/%m/%Y")
except ValueError:
    raise SystemExit(f"JOURNEY_DATE must be DD/MM/YYYY, got {JOURNEY_DATE!r}")

SLOW      = 45_000
PAGE_LOAD = 90_000
RETRIES   = 4

# ---------- Login ----------
SKIP_TOUR   = "xpath=/html/body/app-root/app-home/div/div[2]/div/app-main-page/div/app-jp-input/div[2]/div[2]/div/div[3]/button[2]"
LOGIN_BTN   = "xpath=/html/body/app-root/app-home/div/div[1]/app-header/header/div[2]/div/nav/div[3]/div/button"
LOGIN_FORM  = "/html/body/app-root/app-home/div/div[2]/app-login/div/p-dialog/div/div/div/form/div/div[3]/div/div[1]"
USER_BOX    = f"xpath={LOGIN_FORM}/div[1]/input"
PASS_BOX    = f"xpath={LOGIN_FORM}/div[2]/input"
SIGNIN_BTN  = f"xpath={LOGIN_FORM}/div[4]/button"

# ---------- Station / quota / date / search fields ----------
JP = "/html/body/app-root/app-home/div/div[2]/div/app-main-page/div/app-jp-input/section/div[2]/div/div[1]/form/div/div"

FROM_SPAN = f"xpath={JP}[1]/div[1]/div/div/span[2]"
TO_SPAN   = f"xpath={JP}[1]/div[2]/div/div/span[2]"

FROM_INPUT_NEW_SELS = ["input[formcontrolname='origin']",
                       "input.from-search-input",
                       "input.station-search-input"]
TO_INPUT_NEW_SELS   = ["input[formcontrolname='destination']",
                       "input.to-search-input"]
FROM_INPUT_OLD = f"xpath={JP}[1]/div[1]/div/div[1]/input"
TO_INPUT_OLD   = f"xpath={JP}[1]/div[2]/div/div[1]/input"

QUOTA_BTN_PATH   = f"{JP}[2]/div[1]/div/div/span[2]"
QUOTA_PANEL_PATH = f"{JP}[2]/div[1]/div/div[2]"
QUOTA_BTN        = f"xpath={QUOTA_BTN_PATH}"
QUOTA_PANEL      = f"xpath={QUOTA_PANEL_PATH}"

DATE_FIELD = f"xpath={JP}[1]/div[3]/div"
DATE_IMG   = f"xpath={JP}[1]/div[3]/div/img"

SEARCH_BTN_PATH = f"{JP}[2]/div[3]/button"
SEARCH_BTN      = f"xpath={SEARCH_BTN_PATH}"

# Optional skip popup on the train-list page after search
SKIP_POPUP = ("xpath=/html/body/app-root/app-home/div/div[2]/div/app-train-list/"
              "div[3]/div/div[1]/app-train-avl-enq/div/div[3]/div/button[2]")

# Filter flow on the train-list page (all optional — train list still usable if missing)
FILTERS_OPEN_BTN  = ("xpath=/html/body/app-root/app-home/div/div[2]/div/app-train-list/"
                     "div[2]/div/app-train-list-panel/div/div/div/div[2]/button")
FILTER_SECTION3_SPAN = ("xpath=/html/body/app-root/app-home/div/div[2]/div/app-train-list/"
                        "app-train-filter-panel/aside/div[2]/section[3]/button/span[2]")
FILTER_SECTION3_BTN  = ("xpath=/html/body/app-root/app-home/div/div[2]/div/app-train-list/"
                        "app-train-filter-panel/aside/div[2]/section[3]/button")
FILTER_APPLY_BTN     = ("xpath=/html/body/app-root/app-home/div/div[2]/div/app-train-list/"
                        "app-train-filter-panel/aside/div[4]/button")


# ---------------- logging ----------------
def log(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


def log_step(n, msg):
    print(f"\n=== STEP {n}: {msg} ===", flush=True)


def snapshot(page, tag):
    print(f"\n--- STATE SNAPSHOT ({tag}) ---", flush=True)
    print(f"URL: {page.url}", flush=True)
    for name, sel in (
        ("FROM span",   FROM_SPAN),
        ("TO span",     TO_SPAN),
        ("FROM input",  FROM_INPUT_OLD),
        ("TO input",    TO_INPUT_OLD),
        ("Quota span",  QUOTA_BTN),
        ("Quota panel", QUOTA_PANEL),
        ("Date field",  DATE_FIELD),
        ("Search btn",  SEARCH_BTN),
        ("Skip popup",  SKIP_POPUP),
        ("Filters btn", FILTERS_OPEN_BTN),
        ("Filter s3 span", FILTER_SECTION3_SPAN),
        ("Filter apply",   FILTER_APPLY_BTN),
        ("Check-avail", "button:has-text('availability')"),
    ):
        try:
            loc = page.locator(sel)
            n = loc.count()
            vis = loc.first.is_visible() if n else False
            val = ""
            if n and vis:
                try:
                    tag_name = loc.first.evaluate("e => e.tagName")
                    if tag_name == "INPUT":
                        val = loc.first.input_value(timeout=1000) or ""
                    else:
                        val = (loc.first.inner_text(timeout=1000) or "").strip()
                except Exception:
                    pass
            print(f"  {name} [{sel}]: found={n} visible={vis} value={val!r}", flush=True)
        except Exception as exc:
            print(f"  {name} [{sel}]: error {exc}", flush=True)
    try:
        focused = page.evaluate(
            "() => { const a = document.activeElement; "
            "return a ? (a.tagName + ' id=' + (a.id||'') + "
            "' fcname=' + (a.getAttribute('formcontrolname')||'') + "
            "' value=' + (a.value||'')) : null; }"
        )
        print(f"  focused: {focused!r}", flush=True)
    except Exception:
        pass
    print("--- END SNAPSHOT ---\n", flush=True)


# ---------------- retry / click ----------------
def retry(step_name, fn, attempts=RETRIES):
    for i in range(1, attempts + 1):
        try:
            log("retry", f"{step_name} attempt {i}/{attempts}")
            return fn()
        except PWTimeout as exc:
            if i == attempts:
                log("retry", f"{step_name} giving up after {attempts} attempts")
                raise
            wait = 1.5 * i
            log("retry", f"{step_name} timed out ({exc}) — sleeping {wait:.1f}s")
            time.sleep(wait)


def click_robust(locator, label="element"):
    try:
        locator.click(timeout=10_000)
        log("click", f"{label}: normal click OK")
        return
    except PWTimeout:
        log("click", f"{label}: normal click timed out, trying scroll+force")
    try:
        locator.scroll_into_view_if_needed(timeout=5000)
        locator.click(force=True, timeout=5000)
        log("click", f"{label}: force click OK")
        return
    except PWTimeout:
        log("click", f"{label}: force click timed out, trying focus+JS")
    locator.focus(timeout=5000)
    locator.dispatch_event("click")
    log("click", f"{label}: JS click dispatched")


def dblclick_robust(locator, label="element"):
    try:
        locator.dblclick(timeout=10_000)
        log("dblclick", f"{label}: normal dblclick OK")
        return
    except PWTimeout:
        log("dblclick", f"{label}: normal dblclick timed out, trying scroll+force")
    try:
        locator.scroll_into_view_if_needed(timeout=5000)
        locator.dblclick(force=True, timeout=5000)
        log("dblclick", f"{label}: force dblclick OK")
        return
    except PWTimeout:
        log("dblclick", f"{label}: force dblclick timed out, trying two clicks")
    try:
        locator.click(timeout=3000)
        locator.click(timeout=3000)
        log("dblclick", f"{label}: two single clicks OK")
        return
    except PWTimeout:
        log("dblclick", f"{label}: two clicks timed out, dispatching dblclick event")
    locator.focus(timeout=5000)
    locator.dispatch_event("dblclick")
    log("dblclick", f"{label}: JS dblclick dispatched")


def click_optional(page, selectors, tag, label, step_num=None, timeout=6.0):
    """Wait up to `timeout` for any of `selectors`; click first visible one.
    Never raises — if the element doesn't show, log and continue."""
    if step_num is not None:
        log_step(step_num, label)

    deadline = time.time() + timeout
    target = None
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    target = loc
                    break
            except Exception:
                continue
        if target:
            break
        time.sleep(0.25)

    if target is None:
        log(tag, f"not shown within {timeout:.1f}s — continuing")
        return False

    try:
        try:
            txt = (target.inner_text(timeout=500) or "").strip()
            log(tag, f"found (text={txt!r}), clicking")
        except Exception:
            log(tag, "found, clicking")
        click_robust(target, label)
        log(tag, "clicked")
        time.sleep(0.3)
        return True
    except Exception as exc:
        log(tag, f"click failed ({exc}) — continuing anyway")
        return False


# ---------------- click all "Check availability" ----------------
def click_all_check_availability(page, wait_between=0.5):
    """Click every 'Check availability' button on the train list.

    Handles BOTH behaviours:
      - Button disappears after click (count shrinks) → DON'T advance index
        (the next unclicked button is now at the same index)
      - Button stays after click (count unchanged)   → advance index
    This avoids the classic 'nth(idx) on a shrinking list skips every
    other item' bug.
    """
    log_step(13, "Click all Check Availability buttons")

    # Wait for the train list to render
    deadline = time.time() + 10
    while time.time() < deadline:
        if page.locator("app-train-avl-enq").count() > 0:
            break
        time.sleep(0.25)
    else:
        log("check-avail", "no train rows found within 10s — continuing")
        return 0

    # Case-insensitive, matches 'Check availability', 'Check Availability',
    # 'Check availability / fare', and excludes unrelated 'Check PNR Status'.
    sel = "button:has-text('availability')"
    initial = page.locator(sel).count()
    log("check-avail", f"selector: {sel}")
    log("check-avail", f"initial matches: {initial}")

    if initial == 0:
        log("check-avail", "no 'availability' buttons found — continuing")
        return 0

    clicked = 0
    i = 0
    max_clicks = 100

    while clicked < max_clicks:
        loc = page.locator(sel)
        n = loc.count()

        if i >= n:
            break

        btn = loc.nth(i)

        # Scroll into view — some buttons render below the fold
        try:
            btn.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        if not btn.is_visible():
            log("check-avail", f"idx {i}/{n}: not visible, skipping")
            i += 1
            continue

        try:
            txt = (btn.inner_text(timeout=500) or "").strip()[:60]
        except Exception:
            txt = ""

        log("check-avail", f"idx {i}/{n}: clicking ({txt!r})")

        try:
            click_robust(btn, f"check-avail idx {i}")
            clicked += 1
        except Exception as exc:
            log("check-avail", f"idx {i}: click failed ({exc}), skipping")
            i += 1
            continue

        time.sleep(wait_between)

        # Decide whether to advance based on count change
        new_n = page.locator(sel).count()
        if new_n == n:
            i += 1              # button still there → skip past it
        else:
            # Button removed → next unclicked button is now at same index
            log("check-avail", f"count dropped {n}→{new_n}, staying at idx {i}")

    remaining = page.locator(sel).count()
    log("check-avail", f"clicked {clicked}, {remaining} still match")
    return clicked


# ---------------- page load + login ----------------
def load_page(page):
    log_step(1, "Load IRCTC page")
    def go():
        log("load", f"navigating to {URL}")
        resp = page.goto(URL, wait_until="domcontentloaded", timeout=PAGE_LOAD)
        if resp is not None and resp.status >= 500:
            raise PWTimeout(f"server returned {resp.status}")
        log("load", f"HTTP status: {resp.status if resp else 'no response'}")
        log("load", "waiting for LOGIN button or tour popup")
        page.locator(LOGIN_BTN).or_(page.locator(SKIP_TOUR)).first.wait_for(
            state="visible", timeout=SLOW
        )
        log("load", "page is interactive")
    retry("Page load", go, attempts=5)


def skip_tour_if_shown(page):
    log_step(2, "Skip intro tour if shown")
    skip = page.locator(SKIP_TOUR)
    page.locator(LOGIN_BTN).or_(skip).first.wait_for(state="visible", timeout=SLOW)
    if skip.is_visible():
        click_robust(skip, "tour skip button")
        log("tour", "skipped tour")
        return
    log("tour", "tour not visible — checking short window")
    try:
        skip.wait_for(state="visible", timeout=2500)
        click_robust(skip, "tour skip button (delayed)")
        log("tour", "skipped delayed tour")
    except PWTimeout:
        log("tour", "no tour shown")


def login(page, user, pwd):
    log_step(3, "Log in")
    def open_dialog():
        click_robust(page.locator(LOGIN_BTN), "LOGIN button")
        log("login", "waiting for username box")
        page.locator(USER_BOX).wait_for(state="visible", timeout=SLOW)
        log("login", "login dialog visible")
    retry("Opening login", open_dialog)

    if not (user and pwd):
        log("login", "IRCTC_USER / IRCTC_PASS not set - log in manually")
        return False

    log("login", f"filling username ({len(user)} chars)")
    retry("Username", lambda: page.locator(USER_BOX).fill(user, timeout=SLOW))
    log("login", f"filling password ({len(pwd)} chars)")
    retry("Password", lambda: page.locator(PASS_BOX).fill(pwd, timeout=SLOW))
    log("login", "credentials filled")

    log("login", "clicking Sign In ONCE (no retry — avoids lockout)")
    try:
        page.locator(SIGNIN_BTN).click(timeout=SLOW)
        log("login", "clicked SIGNIN_BTN")
    except PWTimeout:
        log("login", "SIGNIN_BTN timed out, trying role-based fallback")
        try:
            page.get_by_role("button", name=re.compile(r"sign\s*in", re.I)).first.click(timeout=5000)
            log("login", "clicked Sign In via role")
        except PWTimeout:
            log("login", "could not click Sign In — click manually")
            return False

    log("login", "waiting for dialog to close (up to 60s)")
    try:
        page.locator(USER_BOX).wait_for(state="hidden", timeout=60_000)
        log("login", "dialog closed — logged in")
        return True
    except PWTimeout:
        log("login", "dialog still open after 60s — captcha/OTP needed")
        return False


# ---------------- station value readback ----------------
def _read_span_text(page, span_sel, placeholder_words):
    try:
        loc = page.locator(span_sel).first
        if loc.count() > 0 and loc.is_visible():
            txt = (loc.inner_text(timeout=1000) or "").strip()
            if txt and not any(p in txt.lower() for p in placeholder_words):
                return txt
    except Exception:
        pass
    return ""


def _read_from_value(page):
    for sel in FROM_INPUT_NEW_SELS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                val = (loc.input_value(timeout=1000) or "").strip()
                if val and val.lower() != "type to search...":
                    return val, f"input:{sel}"
        except Exception:
            pass
    try:
        loc = page.locator(FROM_INPUT_OLD).first
        if loc.count() > 0 and loc.is_visible():
            val = (loc.input_value(timeout=1000) or "").strip()
            if val and val.lower() != "type to search...":
                return val, "old-input"
    except Exception:
        pass
    txt = _read_span_text(page, FROM_SPAN, ("select source", "type to search"))
    if txt:
        return txt, "old-span"
    return "", "none"


def _read_to_value(page):
    for sel in TO_INPUT_NEW_SELS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                val = (loc.input_value(timeout=1000) or "").strip()
                if val and val.lower() != "type to search...":
                    return val, f"input:{sel}"
        except Exception:
            pass
    try:
        loc = page.locator(TO_INPUT_OLD).first
        if loc.count() > 0 and loc.is_visible():
            val = (loc.input_value(timeout=1000) or "").strip()
            if val and val.lower() != "type to search...":
                return val, "old-input"
    except Exception:
        pass
    txt = _read_span_text(page, TO_SPAN, ("select destination", "type to search"))
    if txt:
        return txt, "old-span"
    return "", "none"


def _wait_for_station_value(page, which, code, timeout=2.5):
    read = _read_from_value if which == "from" else _read_to_value
    deadline = time.time() + timeout
    last = ("", "none")
    while time.time() < deadline:
        last = read(page)
        if code.upper() in last[0].upper():
            return last
        time.sleep(0.15)
    return last


# ---------------- generic station fill ----------------
def _fill_station(page, which, span_sel, code):
    log(f"{which}", f"setting {which.upper()} to {code}")

    read = _read_from_value if which == "from" else _read_to_value
    current, src = read(page)
    log(f"{which}", f"initial read: {current!r} (source={src})")
    if code.upper() in current.upper():
        log(f"{which}", f"already contains {code} — skipping")
        return

    span = page.locator(span_sel).first
    if span.count() == 0:
        raise PWTimeout(f"{which.upper()} span not found at {span_sel}")
    span.wait_for(state="visible", timeout=SLOW)
    log(f"{which}", "span visible, double-clicking")

    dblclick_robust(span, f"{which.upper()} span")

    log(f"{which}", "waiting 0.5s for field activation / auto-populate")
    time.sleep(0.5)

    current, src = read(page)
    log(f"{which}", f"after open: {current!r} (source={src})")
    if code.upper() in current.upper():
        log(f"{which}", f"auto-populated to {current!r} — pressing Enter to confirm")
        page.keyboard.press("Enter")
        return

    if current:
        log(f"{which}", f"clearing existing {current!r}")
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    time.sleep(0.15)

    log(f"{which}", f"typing {code!r}")
    page.keyboard.type(code, delay=100)

    log(f"{which}", "waiting 0.5s for autocomplete suggestions")
    time.sleep(0.5)

    log(f"{which}", "pressing Enter to accept suggestion")
    page.keyboard.press("Enter")

    final, src2 = _wait_for_station_value(page, which, code, timeout=2.5)
    log(f"{which}", f"final read: {final!r} (source={src2})")

    if not final:
        raise PWTimeout(f"{which.upper()} field is empty after typing {code!r}")
    if code.upper() not in final.upper():
        log(f"{which}", f"WARNING: readback {final!r} does not contain {code!r} "
                        f"(accepted anyway — display name may differ)")


def pick_from_station(page, code):
    log_step(4, f"Set FROM station to {code}")
    retry("From station", lambda: _fill_station(page, "from", FROM_SPAN, code))


def pick_to_station(page, code):
    log_step(5, f"Set TO station to {code}")
    retry("To station", lambda: _fill_station(page, "to", TO_SPAN, code))


# ---------------- quota ----------------
def pick_quota(page, quota):
    log_step(6, f"Set quota to {quota}")
    def run():
        btn = page.locator(QUOTA_BTN).first
        btn.wait_for(state="visible", timeout=SLOW)

        current = (btn.inner_text(timeout=1000) or "").strip()
        log("quota", f"current: {current!r}")
        if quota.upper() == current.upper() or (quota.upper() in current.upper()
                                                and len(current) <= len(quota) + 4):
            log("quota", "already set — skipping")
            return

        panel = page.locator(QUOTA_PANEL).first
        panel_open = panel.count() > 0 and panel.is_visible()
        log("quota", f"panel already open: {panel_open}")

        if not panel_open:
            click_robust(btn, "quota button")
            log("quota", "clicked quota button, waiting for panel")
            panel = page.locator(QUOTA_PANEL).first
            panel.wait_for(state="visible", timeout=SLOW)
            log("quota", "panel visible")

        candidates = []
        for sel in (
            f"xpath={QUOTA_PANEL_PATH}/div",
            f"{QUOTA_PANEL} [role='option']",
        ):
            try:
                for c in page.locator(sel).all():
                    try:
                        if c.is_visible():
                            candidates.append(c)
                    except Exception:
                        continue
            except Exception:
                pass

        log("quota", f"found {len(candidates)} candidate options")

        target = None
        texts = []
        for c in candidates:
            try:
                txt = (c.inner_text(timeout=500) or "").strip()
                texts.append(txt)
                if txt.upper() == quota.upper():
                    target = c
                    break
            except Exception:
                continue

        if target is None:
            for c, txt in zip(candidates, texts):
                if quota.upper() in (txt or "").upper():
                    target = c
                    break

        if target is None:
            log("quota", f"options seen: {texts}")
            raise PWTimeout(f"quota option {quota!r} not found in panel")

        click_robust(target, f"quota option {quota}")
        time.sleep(0.4)

        final = (btn.inner_text(timeout=1000) or "").strip()
        log("quota", f"final: {final!r}")
        if quota.upper() not in final.upper():
            raise PWTimeout(f"quota not set (button reads {final!r})")
    retry("Quota", run)


# ---------------- configurable date ----------------
MONTHS_FULL = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]


def _read_date_field(page):
    try:
        loc = page.locator(DATE_FIELD).first
        if loc.count() > 0 and loc.is_visible():
            return (loc.inner_text(timeout=1000) or "").strip()
    except Exception:
        pass
    return ""


def _calendar_open(page):
    for sel in ("p-calendar", ".p-datepicker", ".ui-datepicker"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            pass
    return False


def _click_calendar_today(page):
    for sel in (
        "p-calendar .p-datepicker-today a",
        "p-calendar .ui-datepicker-today a",
        ".p-datepicker-today a",
        ".ui-datepicker-today a",
        "p-calendar td a.p-highlight",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                log("date", f"clicking today via {sel}")
                click_robust(loc, "today cell")
                return True
        except Exception:
            continue
    return False


def _read_calendar_header(page):
    for sel in (
        "p-calendar .p-datepicker-title",
        "p-calendar .ui-datepicker-title",
        ".p-datepicker-title",
        ".ui-datepicker-title",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return (loc.inner_text(timeout=500) or "").strip()
        except Exception:
            continue
    return ""


def _parse_header(text):
    if not text:
        return None
    t = text.lower()
    ym = re.search(r"\b(19|20)\d{2}\b", t)
    if not ym:
        return None
    year = int(ym.group())
    for i, m in enumerate(MONTHS_FULL):
        if m in t or m[:3] in re.split(r"\W+", t):
            return (year, i + 1)
    return None


def _click_calendar_nav(page, kind):
    sels = {
        "next": ("p-calendar .p-datepicker-next",
                 "p-calendar .ui-datepicker-next",
                 ".p-datepicker-next", ".ui-datepicker-next"),
        "prev": ("p-calendar .p-datepicker-prev",
                 "p-calendar .ui-datepicker-prev",
                 ".p-datepicker-prev", ".ui-datepicker-prev"),
    }[kind]
    for sel in sels:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                click_robust(loc, f"calendar {kind}")
                time.sleep(0.2)
                return True
        except Exception:
            continue
    return False


def _click_calendar_day(page, day):
    for sel in (
        f"p-calendar td a:text-is('{day}')",
        f".p-datepicker td a:text-is('{day}')",
        f".ui-datepicker td a:text-is('{day}')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                log("date", f"clicking day {day} via {sel}")
                click_robust(loc, f"day {day}")
                return True
        except Exception:
            continue
    return False


def pick_journey_date(page, date_str):
    target = datetime.strptime(date_str, "%d/%m/%Y").date()
    target_short = target.strftime("%d %b %Y")
    target_alt   = target.strftime("%d/%m/%Y")
    is_today     = (target == datetime.now().date())

    log_step(7, f"Set journey date to {target_short}")

    def run():
        field = page.locator(DATE_FIELD).first
        field.wait_for(state="visible", timeout=SLOW)

        current = (field.inner_text(timeout=1000) or "").strip()
        log("date", f"current field reads: {current!r}")
        if target_short.lower() in current.lower() or target_alt in current:
            log("date", f"already set to {target_short} — skipping")
            return

        img = page.locator(DATE_IMG).first
        if img.count() == 0:
            raise PWTimeout(f"Date image not found at {DATE_IMG}")
        img.wait_for(state="visible", timeout=SLOW)
        click_robust(img, "date image")

        log("date", "waiting 0.5s for calendar to appear")
        time.sleep(0.5)

        selected = False
        if is_today:
            log("date", "target is today — trying today-marker shortcut")
            selected = _click_calendar_today(page)

        if not selected:
            log("date", f"navigating to {target.strftime('%b %Y')}")
            for _ in range(24):
                parsed = _parse_header(_read_calendar_header(page))
                if parsed is None:
                    log("date", "calendar header not parseable — breaking")
                    break
                if parsed == (target.year, target.month):
                    log("date", "calendar is at target month")
                    break
                direction = "next" if parsed < (target.year, target.month) else "prev"
                if not _click_calendar_nav(page, direction):
                    log("date", f"could not click calendar {direction}")
                    break

            selected = _click_calendar_day(page, target.day)

        if not selected:
            raise PWTimeout(f"Could not locate day {target.day} in the calendar")

        time.sleep(0.5)
        if _calendar_open(page):
            log("date", "calendar still open — pressing Escape")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.3)

        final = _read_date_field(page)
        log("date", f"final field reads: {final!r}")
        if target_short.lower() not in final.lower() and target_alt not in final:
            month_tok = target.strftime("%b")
            if str(target.day) in final and month_tok.lower() in final.lower():
                log("date", "accepted (day + month tokens match)")
            else:
                raise PWTimeout(f"date not set to {target_short} (reads {final!r})")
    retry("Date", run)


# ---------------- search ----------------
def click_search(page):
    log_step(8, "Click Search Trains")
    def run():
        clicked = False
        for sel in (
            SEARCH_BTN,
            "button.search-btn",
            "button[type='submit']:has-text('Search Trains')",
            "button:has-text('Search Trains')",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    log("search", f"clicking via {sel}")
                    click_robust(loc, "Search button")
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            raise PWTimeout("Search button not found with any selector")

        log("search", "clicked — waiting for navigation / train list")
        deadline = time.time() + 10
        while time.time() < deadline:
            url_now = page.url
            if "train-search" not in url_now:
                log("search", f"URL changed to {url_now} — search submitted")
                return
            try:
                if page.locator("app-train-list").first.count() > 0:
                    log("search", "train list rendered — search submitted")
                    return
            except Exception:
                pass
            time.sleep(0.25)

        log("search", f"URL still {page.url} after 10s — "
                      f"likely a form validation error, check the browser")
    retry("Search", run)


# ---------------- orchestration ----------------
def search_trains(page):
    step = "From station"
    try:
        pick_from_station(page, FROM_STATION)
        print(f"\n>>> FROM STATION SET: {FROM_STATION}\n", flush=True)

        step = "To station"
        pick_to_station(page, TO_STATION)
        print(f"\n>>> TO STATION SET: {TO_STATION}\n", flush=True)

        step = "Quota"
        pick_quota(page, QUOTA)
        print(f"\n>>> QUOTA SET: {QUOTA}\n", flush=True)

        step = "Date"
        pick_journey_date(page, JOURNEY_DATE)
        print(f"\n>>> DATE SET: {JOURNEY_DATE}\n", flush=True)

        step = "Search"
        click_search(page)
        print("\n>>> SEARCH CLICKED.\n", flush=True)

        step = "Post-search popups and filters"
        click_optional(
            page,
            [SKIP_POPUP, "button:has-text('Skip')", "button:has-text('SKIP')",
             "button:has-text('Skip & Continue')"],
            tag="skip", label="Skip popup after search", step_num=9,
        )
        click_optional(
            page,
            [FILTERS_OPEN_BTN, "button:has-text('Filters')",
             "button:has-text('FILTERS')"],
            tag="filter-open", label="Open train filters", step_num=10,
        )
        click_optional(
            page,
            [FILTER_SECTION3_SPAN, FILTER_SECTION3_BTN],
            tag="filter-section", label="Toggle filter section 3", step_num=11,
        )
        click_optional(
            page,
            [FILTER_APPLY_BTN, "button:has-text('Apply')",
             "button:has-text('APPLY')"],
            tag="filter-apply", label="Apply filters", step_num=12, timeout=8.0,
        )

        step = "Check availability"
        click_all_check_availability(page)
        print("\n>>> All trains expanded. Continue by hand from here "
              "(pick train, passengers, captcha, pay).\n", flush=True)
    except PWTimeout as exc:
        print(f"\n!!! FAILED at step: {step}", flush=True)
        print(f"!!! Exception: {type(exc).__name__}: {exc}", flush=True)
        snapshot(page, f"failure at {step}")
        dump_elements(page)
        try:
            page.screenshot(path="search_fail.png", full_page=True)
            log("fail", "saved search_fail.png")
        except Exception as e:
            log("fail", f"screenshot failed: {e}")
        raise


def dump_elements(page):
    try:
        info = page.eval_on_selector_all(
            "input, button, a, [role='button'], [role='option'], "
            "p-autocomplete, p-dropdown, p-calendar",
            """els => els.filter(e => e.offsetParent !== null).map(e => e.tagName +
                ' id=' + e.id +
                ' type=' + (e.getAttribute('type')||'') +
                ' placeholder=' + (e.getAttribute('placeholder')||'') +
                ' aria-label=' + (e.getAttribute('aria-label')||'') +
                ' formcontrolname=' + (e.getAttribute('formcontrolname')||'') +
                ' class=' + e.className +
                ' text=' + (e.innerText||'').trim().slice(0, 30))""",
        )
        print("\n--- VISIBLE PAGE ELEMENTS ---", flush=True)
        print("\n".join(info), flush=True)
        print("--- END ---\n", flush=True)
    except Exception as exc:
        log("dump", f"failed: {exc}")


def main():
    user = ""
    pwd = ""
    debug = 1

    log("start", f"user={'set' if user else 'MISSING'}, "
                 f"pwd={'set' if pwd else 'MISSING'}, debug={debug}")
    log("start", f"journey: {FROM_STATION} -> {TO_STATION} on {JOURNEY_DATE} "
                 f"({QUOTA})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_default_timeout(SLOW)
        log("start", "browser launched")

        try:
            load_page(page)
            skip_tour_if_shown(page)
            logged_in = login(page, user, pwd)
        except PWTimeout as exc:
            print(f"\n!!! FAILED at load/login: {type(exc).__name__}: {exc}", flush=True)
            snapshot(page, "after load/login failure")
            logged_in = False

        if not logged_in:
            print("\n>>> If IRCTC asks for captcha/OTP, complete it in the browser now.", flush=True)
            try:
                input(">>> Press ENTER here once you are logged in... ")
            except (EOFError, KeyboardInterrupt):
                log("exit", "user interrupted at login prompt")
                try: browser.close()
                except Exception: pass
                return

        log("post-login", "waiting for FROM field to be ready")
        try:
            page.locator(FROM_SPAN).first.wait_for(state="visible", timeout=20_000)
            log("post-login", "FROM field ready")
        except PWTimeout:
            log("post-login", "FROM field not ready after 20s — continuing anyway")

        if debug:
            log("debug", "IRCTC_DEBUG=1 — dumping all form elements")
            dump_elements(page)
            snapshot(page, "before station fill")

        try:
            search_trains(page)
        except PWTimeout:
            print("\n>>> Search flow failed — see snapshot above. Fill by hand if needed.", flush=True)

        print("\n>>> Complete passenger details, captcha and payment yourself.", flush=True)
        try:
            input(">>> Press ENTER here to close the browser... ")
        except (EOFError, KeyboardInterrupt):
            log("exit", "user interrupted at close prompt")
        log("end", "closing browser")
        try: browser.close()
        except Exception: pass


if __name__ == "__main__":
    main()
