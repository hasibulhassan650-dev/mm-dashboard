"""
fetchers/omo.py — Fetch OMO (Open Market Operations) press release PDFs from BB.

TARGET PAGE : https://www.bb.org.bd/en/index.php/mediaroom/press_release
PDF PATTERN : https://www.bb.org.bd/mediaroom/press_release/press/pr{ID}_{YYYYMMDD}.pdf

PAGE STRUCTURE:
  <tr>
    <td>...</td>
    <td>22/04/2026</td>
    <td class="text-left">Open Market Operations as on 21 April 2026
      <a class="pdf-file" pdf-link="https://...pdf"> more....</a>
    </td>
  </tr>

PDF TABLE COLUMNS (from pdfplumber text):
  Instruments | Tenor | Offered Amount | Accepted/Settlement Amount |
  Rate (%) & PSR/EPR | Maturity Amount | Net injection(+)/absorption(-)

EXAMPLE ROWS:
  CB Repo  7-Days   7,614.74  7,614.74  10.00    0.00      7,614.74
  CB Repo  14-Days 12,775.44 12,775.44  10.00  -14,518.00 -1,742.57
  SLF      1-Day       0.00      0.00  11.50   -167.50    -167.50
  IBLF     7-Days    195.00    195.00   4.00      0.00      195.00
  IBLF     28-Days   782.00    782.00   4.00-5.25 -882.37  -100.37
  AR       28-Days   896.70    896.70  10.00   -903.58      -6.88
  SDF      1-Day  -2,463.60 -2,463.60   7.50  2,382.09     -81.51

INTERPRETATION:
  - Accepted/Settlement Amount = new OMO transaction done today
  - Positive = CB Repo/SLF/IBLF/AR = INJECTION (BB lends to banks)
  - Negative = SDF = ABSORPTION (banks deposit at BB)
  - Maturity Amount = what matures today from PREVIOUS transactions (cross-check only)

OUTSTANDING CALCULATION:
  For any date D, outstanding for instrument X =
    SUM(accepted_bdt_crore for all transactions where
        transaction_date <= D  AND  maturity_date > D  AND  instrument = X)
"""
import datetime
import logging
import re
import shutil
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

from config import BB_PRESS_URL

log = logging.getLogger(__name__)

# ── Instrument definitions ────────────────────────────────────────────────────
_INSTRUMENTS = [
    ("CB_REPO", r"cb[\s\-]*repo|central[\s\-]*bank[\s\-]*repo",   "INJECTION"),
    ("CM_REPO", r"cm[\s\-]*repo|capital[\s\-]*market[\s\-]*repo", "INJECTION"),
    ("SLF",     r"\bslf\b|standing\s+lending\s+facilit",          "INJECTION"),
    ("IBLF",    r"\biblf\b|islami\s+bank",                        "INJECTION"),
    ("MLS",     r"\bmls\b|mudaraba\s+liquidity",                  "INJECTION"),
    ("SLS",     r"\bsls\b|special\s+liquidity\s+support",         "INJECTION"),
    ("SRF",     r"\bsrf\b|special\s+repo",                        "INJECTION"),
    ("AR",      r"\bar\b(?!\s*=)|assured[\s\-]*repo",             "INJECTION"),
    ("SDF",     r"\bsdf\b|standing\s+deposit\s+facilit",          "ABSORPTION"),
]

_SKIP_LINES = {
    "sub-total", "grand total", "net liquidity", "injection(+)", "absorption(-)",
    "amount in crore", "instruments", "offered", "settlement", "maturity",
    "psr/epr", "serial no", "press release", "bangladesh bank", "head office",
    "motijheel", "department", "news editors", "dhaka", "all electronic",
    "sd/", "[note:", "cb repo =", "rate (%)", "accepted/", "tenor",
}


def _match_instrument(text: str) -> Optional[tuple]:
    """Return (canonical_name, direction) or None."""
    t = text.strip().lower()
    for name, pat, direction in _INSTRUMENTS:
        if re.search(pat, t):
            return name, direction
    return None


def _parse_tenor(text: str) -> Optional[int]:
    """'7-Days' → 7,  '1-Day' → 1,  '28-Days' → 28.  None if not found."""
    m = re.search(r'(\d+)\s*[-\s]*(days?|day)\b', text, re.I)
    if m:
        return int(m.group(1))
    if re.search(r'\bo/?n\b|overnight', text, re.I):
        return 1
    return None


def _extract_label(line: str) -> Optional[str]:
    """
    The instrument label printed at the START of a data row (the text before the
    tenor). Captures ANY label — recognised or brand-new — so a new BB product is
    never silently inherited from the previous block. None on continuation rows
    (where the merged name renders elsewhere).
    """
    m = re.match(r'^\s*([A-Za-z][A-Za-z0-9/.\s\-]*?)\s+\d+\s*[-\s]*days?\b', line, re.I)
    if not m:
        return None
    label = re.sub(r'\s+', ' ', m.group(1)).strip()
    if not label or len(label) > 30 or _should_skip(label):
        return None
    return label


def _resolve_instrument(label: Optional[str], accepted_negative: bool):
    """
    → (name, direction, known:bool) or None when there is no label.
    Known label → canonical name+direction. UNKNOWN label → keep its own name
    (never borrow a neighbour's) with direction inferred from the amount sign
    (BB prints absorption negative); the caller alarms so it gets added to the
    canonical map.
    """
    if not label:
        return None
    known = _match_instrument(label)
    if known:
        return known[0], known[1], True
    name = re.sub(r'[^A-Z0-9]+', '_', label.upper()).strip('_')[:30] or "UNKNOWN"
    return name, ("ABSORPTION" if accepted_negative else "INJECTION"), False


def _clean_num(s: str) -> Optional[float]:
    try:
        return float(re.sub(r'[,\s]', '', s))
    except (ValueError, AttributeError):
        return None


def _resolve_rate(vals: List[float]) -> tuple:
    """
    Resolve the Rate(%) column into (rate_pct, rate_range).

    BB publishes a single rate ("10.00") or a RANGE ("4.00-5.25"). When the
    range is parsed numerically the upper bound carries the hyphen and so reads
    as negative — e.g. the rate column yields [4.00, -5.25]. We keep the full
    range as a string (so the dashboard can show the WHOLE range) and use the
    lower bound as the numeric rate_pct for charts/integrity.

    Returns (rate_pct, rate_range) where rate_range is None for a single rate.
    """
    if (len(vals) >= 2 and vals[0] is not None and vals[1] is not None
            and vals[0] > 0 and vals[1] < 0
            and abs(vals[0]) < 30 and abs(vals[1]) < 30):
        low, high = vals[0], abs(vals[1])
        lo, hi = (low, high) if low <= high else (high, low)
        return lo, f"{lo:.2f}-{hi:.2f}"
    rate = next((n for n in vals if n is not None and 0 < abs(n) < 30), None)
    return rate, None


def _should_skip(line: str) -> bool:
    ll = line.lower()
    return any(kw in ll for kw in _SKIP_LINES)


def _parse_date_from_text(text: str) -> Optional[datetime.date]:
    """Extract date from 'Open Market Operations as on 21 April 2026' etc."""
    m = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', text)
    if m:
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                return datetime.datetime.strptime(
                    f"{m.group(1)} {m.group(2).capitalize()} {m.group(3)}", fmt
                ).date()
            except ValueError:
                continue
    m = re.search(r'_(\d{8})', text)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    return None


def _parse_pub_meta(text: str):
    """
    Extract the press-release PUBLICATION date ('Date: 04-08-2026') and serial
    ('Serial No- 05/2026-342'). BB publishes each OMO release with a lag — the
    'as on' operation date is normally the previous working day. The pub date +
    serial let us (a) spot BB's occasional same-day mislabels and (b) treat the
    LATEST-published release for an operation date as authoritative (corrections
    supersede originals). Returns (pub_date | None, serial | None).
    """
    pub_date = None
    m = re.search(r'Date:\s*(\d{1,2})-(\d{1,2})-(\d{4})', text)
    if m:
        try:
            pub_date = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pub_date = None
    serial = None
    m = re.search(r'Serial\s*No[-\s:]*([0-9A-Za-z/]+-\d+)', text)
    if m:
        serial = m.group(1).strip()
    return pub_date, serial


# ── PDF parser ────────────────────────────────────────────────────────────────

def parse_omo_pdf(pdf_bytes: bytes, hint_date: Optional[datetime.date], pdf_url: str) -> List[Dict]:
    """
    Parse a BB OMO press release PDF.
    Returns list of dicts, one per (instrument, tenor) line.
    Tries table extraction first (reliable column alignment); falls back to text parsing.

    PDF columns: Instruments | Tenor | Offered | Accepted | Rate | Maturity | Net
    """
    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        log.warning("Not a valid PDF (%s...)", pdf_bytes[:8] if pdf_bytes else b"")
        return []

    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber not installed")
        return []

    full_text = ""
    all_tables: List[List] = []
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
                for tbl in (page.extract_tables() or []):
                    all_tables.extend(tbl)
    except Exception as exc:
        log.error("pdfplumber failed for %s: %s", pdf_url, exc)
        return []

    txn_date = _parse_date_from_text(full_text) or hint_date or datetime.date.today()
    # BB PDF text sometimes has an off-by-one date (e.g. says "23 May" but file is pr_20260524.pdf).
    # OMO never happens on Fri/Sat (BD weekend). If text date lands on a weekend, trust the URL date.
    if txn_date.weekday() in (4, 5):  # Friday=4, Saturday=5
        url_date = _parse_date_from_text(pdf_url)
        if url_date and url_date.weekday() not in (4, 5):
            log.info("Date corrected %s->%s (text was weekend, using URL date)", txn_date, url_date)
            txn_date = url_date
        elif hint_date and hint_date.weekday() not in (4, 5):
            log.info("Date corrected %s->%s (text was weekend, using hint date)", txn_date, hint_date)
            txn_date = hint_date
    log.debug("OMO PDF date: %s from %s", txn_date, pdf_url)

    # ── Text-based parsing is primary: instrument identification is reliable ──
    # Table parser has a known bug where pdfplumber sometimes places the section
    # header (e.g. "IBLF") on a later row, causing the first row to inherit the
    # wrong instrument via carry-forward.  Text extraction always puts the
    # instrument name on its own line before the data rows.
    pub_date, serial = _parse_pub_meta(full_text)
    # BB publishes the next working day; 'as on' == pub date (no lag) means BB
    # mislabelled the date and a corrected release usually follows. Surface it —
    # the store layer treats the latest-published release for a date as truth.
    if pub_date and pub_date <= txn_date:
        log.warning("OMO date anomaly: 'as on' %s but PUBLISHED %s (serial %s) — no lag; "
                    "PROVISIONAL, expect a correction to supersede this", txn_date, pub_date, serial)

    def _annotate(rows: List[Dict]) -> List[Dict]:
        for r in rows:
            r["source_pub_date"] = pub_date
            r["source_serial"] = serial
        return rows

    transactions = _parse_via_text(full_text, txn_date, pdf_url)
    if transactions:
        log.info("Parsed %d rows via text parser (date=%s, pub=%s, serial=%s, url=%s)",
                 len(transactions), txn_date, pub_date, serial, Path(pdf_url).name)
        return _annotate(transactions)

    # ── Fallback: table-based parsing ─────────────────────────────────────────
    log.debug("Text parser yielded nothing, falling back to table extraction")
    transactions = _parse_via_table(all_tables, txn_date, pdf_url)
    log.info("Parsed %d rows via table fallback (date=%s, pub=%s, url=%s)",
             len(transactions), txn_date, pub_date, Path(pdf_url).name)
    return _annotate(transactions)


def _build_txn(instr: str, direction: str, tenor_days: int,
               accepted: float, maturity: float, rate: Optional[float],
               txn_date: datetime.date, pdf_url: str,
               rate_range: Optional[str] = None) -> Optional[Dict]:
    """Build a transaction dict. Only stores rows where a new acceptance happened."""
    if abs(accepted) < 0.01:
        return None
    mat_date = (txn_date + datetime.timedelta(days=tenor_days)
                if abs(accepted) >= 0.01 else txn_date)
    return {
        "transaction_date":   txn_date,
        "maturity_date":      mat_date,
        "instrument":         instr,
        "tenor_label":        f"{tenor_days}D",
        "tenor_days":         tenor_days,
        "accepted_bdt_crore": abs(accepted),
        "maturity_bdt_crore": maturity,
        "rate_pct":           rate,
        "rate_range":         rate_range,
        "direction":          direction,
        "source_pdf":         pdf_url,
    }


def _parse_via_table(rows: List[List], txn_date: datetime.date, pdf_url: str) -> List[Dict]:
    """
    Parse using pdfplumber table rows.
    Expected columns (by position): Instrument | Tenor | Offered | Accepted | Rate | Maturity | Net
    Instrument cell may be empty for continuation rows — carry the last seen value.
    """
    transactions: List[Dict] = []
    current_instrument: Optional[str] = None
    current_direction:  Optional[str] = None

    for row in rows:
        if not row or len(row) < 5:
            continue
        cells = [str(c or "").strip() for c in row]

        # Skip header/footer rows
        joined = " ".join(cells).lower()
        if any(kw in joined for kw in ("instruments", "sub-total", "grand total",
                                       "net liquidity", "offered amount", "tenor")):
            continue

        # Column 0: instrument (may be blank for continuation rows)
        if cells[0]:
            m = _match_instrument(cells[0])
            if m:
                current_instrument, current_direction = m
            elif not any(_parse_tenor(c) for c in cells[1:2]):
                # Non-instrument, non-tenor first cell — skip
                continue

        if not current_instrument:
            continue

        # Column 1: tenor
        tenor_days = _parse_tenor(cells[1]) if len(cells) > 1 else None
        if tenor_days is None:
            continue

        # Columns 2,3,4,5,6 = Offered, Accepted, Rate, Maturity, Net
        # We need at least Accepted (col 3) and Maturity (col 5)
        accepted  = _clean_num(cells[3]) if len(cells) > 3 else None
        maturity  = _clean_num(cells[5]) if len(cells) > 5 else None
        rate_raw  = cells[4]             if len(cells) > 4 else ""

        if accepted is None:
            continue

        # Rate may be a range "4.00-5.25" — keep the WHOLE range, use low as numeric
        parts = [v for v in (_clean_num(t) for t in re.split(r'[-–]', rate_raw))
                 if v is not None and 0 < v < 30]
        if len(parts) >= 2:
            lo, hi = sorted(parts[:2])
            rate, rate_range = lo, f"{lo:.2f}-{hi:.2f}"
        else:
            rate, rate_range = (parts[0] if parts else None), None
        mat_abs = abs(maturity) if maturity is not None else 0.0

        txn = _build_txn(current_instrument, current_direction, tenor_days,
                         abs(accepted), mat_abs, rate, txn_date, pdf_url,
                         rate_range=rate_range)
        if txn:
            transactions.append(txn)

    return transactions


def _parse_via_text(full_text: str, txn_date: datetime.date, pdf_url: str) -> List[Dict]:
    """
    Robust block-based parser.

    BB lists each instrument as a contiguous block of tenor rows whose tenors
    ASCEND (e.g. AR: 7D, 14D, 28D, 180D). The merged instrument-name cell can
    render anywhere inside its block (top, middle, or inline on a data row), so
    'carry forward the last name' mislabels rows. Instead we delimit blocks by a
    TENOR RESET (a tenor <= the previous row's tenor starts a new instrument) and
    assign each block the instrument name found ANYWHERE within it. Column values
    are positional and reliable: Offered | Accepted | Rate[range] | Maturity | Net
    → accepted = nums[1], maturity = nums[-2], rate = first value in nums[2:-2].
    """
    def _extract_nums(line: str) -> List[float]:
        s = re.sub(r'\d+\s*[-\s]*(days?|day)\b', '', line, flags=re.I)
        s = re.sub(r'[A-Za-z\s\(\)\[\]/]+', ' ', s)
        return [v for v in (_clean_num(t) for t in re.findall(r'-?[\d,]+\.?\d*', s))
                if v is not None]

    # ── Pass 1: segment rows into instrument blocks by their OWN label ────────
    blocks: List[Dict] = []
    cur: Dict = {"name": None, "dir": None, "known": True, "rows": []}
    last_tenor: Optional[int] = None

    def _flush():
        nonlocal cur, last_tenor
        if cur["rows"] or cur["name"]:
            blocks.append(cur)
        cur = {"name": None, "dir": None, "known": True, "rows": []}
        last_tenor = None

    for raw_line in full_text.split("\n"):
        line = raw_line.strip()
        if not line or _should_skip(line):
            continue

        tenor_days = _parse_tenor(line)

        if tenor_days is None:
            # Standalone instrument-name line (no data). Resolve to a known
            # instrument, or — if it's an unrecognised alpha label — keep it as a
            # NEW instrument under its own name (never borrow a neighbour's).
            m = _match_instrument(line)
            if m:
                lbl, dirn, known = m[0], m[1], True
            elif re.fullmatch(r'[A-Za-z][A-Za-z0-9/.\s\-]{0,28}', line):
                lbl, dirn, known = (re.sub(r'[^A-Z0-9]+', '_', line.upper()).strip('_')[:30] or None), None, False
            else:
                lbl = None
            if lbl:
                if cur["rows"] and cur["name"] and lbl != cur["name"]:
                    _flush()
                cur["name"], cur["known"] = lbl, known
                if dirn:
                    cur["dir"] = dirn
            continue

        nums = _extract_nums(line)
        if len(nums) < 4:
            continue   # tenor split onto its own line (no data) — skip
        accepted = nums[1]

        # Every data row is classified by its OWN leading label. A different label
        # (recognised OR brand-new) starts a new block — a new BB product can never
        # be silently absorbed into the previous instrument. Tenor reset is a
        # fallback delimiter for genuine continuation rows with no label.
        resolved = _resolve_instrument(_extract_label(line), accepted < 0)
        new_name = resolved[0] if resolved else None
        name_change = new_name is not None and cur["rows"] and cur["name"] is not None and new_name != cur["name"]
        tenor_reset = last_tenor is not None and tenor_days <= last_tenor
        if name_change or (tenor_reset and (cur["rows"] or cur["name"])):
            _flush()
        if resolved:
            cur["name"], cur["dir"], cur["known"] = resolved[0], resolved[1], resolved[2]

        maturity = abs(nums[-2])
        rate, rate_range = _resolve_rate(nums[2:-2])
        cur["rows"].append({"tenor_days": tenor_days, "accepted": accepted,
                            "maturity": maturity, "rate": rate, "rate_range": rate_range})
        last_tenor = tenor_days

    _flush()

    # ── Pass 2: emit transactions; instrument = the block's own label ─────────
    transactions: List[Dict] = []
    unknown: set = set()
    for b in blocks:
        name, direction = b["name"], b["dir"]
        if not name or not b["rows"]:
            continue   # unnamed / empty block — skip rather than risk a wrong label
        if direction is None:   # unknown standalone-name block — infer from amount sign
            direction = "ABSORPTION" if b["rows"][0]["accepted"] < 0 else "INJECTION"
        emitted = 0
        for r in b["rows"]:
            txn = _build_txn(name, direction, r["tenor_days"], abs(r["accepted"]),
                             r["maturity"], r["rate"], txn_date, pdf_url,
                             rate_range=r.get("rate_range"))
            if txn:
                transactions.append(txn)
                emitted += 1
        # Only alarm on an unrecognised instrument that actually STORED a
        # transaction — maturity-only rows (accepted=0, e.g. a wrapped "for
        # maintaining …" fragment) produce nothing and must not raise noise.
        if not b.get("known", True) and emitted:
            unknown.add(name)

    if unknown:
        log.warning("OMO: UNRECOGNISED instrument(s) %s in %s — stored under their own "
                    "name with sign-inferred direction; ADD them to _INSTRUMENTS so the "
                    "canonical label & direction are used.", sorted(unknown), Path(pdf_url).name)

    # SDF is physically overnight-only; any SDF >1D is a misread → treat as AR.
    for txn in transactions:
        if txn["instrument"] == "SDF" and txn["tenor_days"] > 1:
            txn["instrument"] = "AR"
            txn["direction"] = "INJECTION"

    return transactions


# ── Chrome helpers ────────────────────────────────────────────────────────────

def _chrome_major_version() -> Optional[int]:
    # Single source of truth (Windows registry + Linux/macOS `--version`).
    from fetchers.bb_session import get_chrome_version
    return get_chrome_version()


def _wait_for_content(drv, timeout: int = 60) -> bool:
    """Wait until the page has real content (pdf-file links visible)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        WebDriverWait(drv, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.pdf-file"))
        )
        return True
    except Exception:
        return False


def _wait_for_download(download_dir: str, prev_files: set, timeout: int = 40) -> Optional[str]:
    """Wait for a new .pdf file to appear and finish downloading."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = {
            str(f) for f in Path(download_dir).iterdir()
            if f.suffix.lower() == ".pdf" and not f.name.endswith(".crdownload")
        }
        new = current - prev_files
        if new:
            return max(new, key=lambda p: Path(p).stat().st_mtime)
        time.sleep(0.5)
    return None


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch_omo_data(days_back: int = 28, max_files: int = 20) -> List[Dict]:
    """
    1. Load BB press release page via Chrome
    2. Find all 'Open Market Operations' PDFs within days_back days (max max_files)
    3. Download each PDF via Chrome auto-download
    4. Parse transactions with tenors and maturity dates
    Returns list of transaction dicts.
    """
    try:
        import undetected_chromedriver as uc
    except ImportError as exc:
        log.warning("undetected-chromedriver not available: %s", exc)
        return []

    from bs4 import BeautifulSoup

    cutoff_date  = datetime.date.today() - datetime.timedelta(days=days_back)
    download_dir = tempfile.mkdtemp(prefix="bb_omo_")
    chrome_ver   = _chrome_major_version()
    driver       = None
    all_txns: List[Dict] = []
    omo_links: List[Dict] = []

    try:
        opts = uc.ChromeOptions()
        opts.add_argument("--window-position=-10000,-10000")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_experimental_option("prefs", {
            "plugins.always_open_pdf_externally": True,
            "download.default_directory":         download_dir,
            "download.prompt_for_download":       False,
            "download.directory_upgrade":         True,
            "safebrowsing.enabled":               True,
        })

        driver = uc.Chrome(options=opts, version_main=chrome_ver)

        from selenium.webdriver.common.by import By

        # ── 1. Load press release page ────────────────────────────────────────
        log.info("Loading press release page…")
        driver.get(BB_PRESS_URL)

        if not _wait_for_content(driver, timeout=60):
            log.error("Press release page did not load pdf-file links in time")
            return []

        time.sleep(2)

        # ── 2. Paginate DataTables and collect all OMO PDF links ─────────────
        seen_urls: set = set()
        stop_early  = False
        page_num    = 1

        while not stop_early:
            soup = BeautifulSoup(driver.page_source, "lxml")

            for a in soup.find_all("a", class_="pdf-file"):
                pdf_url = (a.get("pdf-link") or "").strip()
                if not pdf_url or pdf_url in seen_urls:
                    continue
                seen_urls.add(pdf_url)

                td    = a.find_parent("td")
                title = td.get_text(" ", strip=True).replace("more....", "").strip() if td else ""
                if "open market operations" not in title.lower():
                    continue

                rdate = _parse_date_from_text(title) or _parse_date_from_text(pdf_url)
                if rdate and rdate < cutoff_date:
                    stop_early = True   # page is sorted newest-first; stop paginating
                    break

                omo_links.append({"url": pdf_url, "title": title, "report_date": rdate})
                if len(omo_links) >= max_files:
                    stop_early = True
                    break

            if stop_early:
                break

            # Click DataTables "Next" button
            try:
                next_li = driver.find_element(By.CSS_SELECTOR, "li#data_table_next")
                if "disabled" in (next_li.get_attribute("class") or ""):
                    log.info("Last page reached (page %d)", page_num)
                    break
                next_a = next_li.find_element(By.TAG_NAME, "a")
                driver.execute_script("arguments[0].click()", next_a)
                time.sleep(2)
                page_num += 1
                log.info("Navigated to page %d — %d OMO links so far", page_num, len(omo_links))
            except Exception as exc:
                log.warning("Pagination stopped at page %d: %s", page_num, exc)
                break

        omo_links.sort(key=lambda x: str(x["report_date"] or ""), reverse=True)
        log.info("Found %d OMO PDF links to download (across %d pages)", len(omo_links), page_num)

        if not omo_links:
            log.warning("No OMO PDFs found. days_back=%d", days_back)
            return []

        time.sleep(1)  # brief settle before first download

        # ── 3. Download & parse each PDF ──────────────────────────────────────
        for entry in omo_links:
            pdf_url    = entry["url"]
            title      = entry["title"]
            rdate      = entry["report_date"]

            log.info("  Downloading: %s", title)
            prev_files = {
                str(f) for f in Path(download_dir).iterdir()
                if f.suffix.lower() == ".pdf"
            }

            driver.get(pdf_url)
            time.sleep(1)

            dl_path = _wait_for_download(download_dir, prev_files, timeout=40)

            if dl_path:
                pdf_bytes = Path(dl_path).read_bytes()
                txns = parse_omo_pdf(pdf_bytes, rdate, pdf_url)
                all_txns.extend(txns)
                log.info("    → %d transactions", len(txns))
            else:
                log.warning("    Download timed out for %s", pdf_url)

    except Exception as exc:
        log.error("OMO fetch error: %s", exc, exc_info=True)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        shutil.rmtree(download_dir, ignore_errors=True)

    log.info("OMO fetch complete: %d total transactions from %d PDFs",
             len(all_txns), len(omo_links))
    return all_txns
