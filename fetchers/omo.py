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
    ("SLF",     r"\bslf\b|standing\s+lending\s+facilit",          "INJECTION"),
    ("IBLF",    r"\biblf\b|islami\s+bank",                        "INJECTION"),
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


def _clean_num(s: str) -> Optional[float]:
    try:
        return float(re.sub(r'[,\s]', '', s))
    except (ValueError, AttributeError):
        return None


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


# ── PDF parser ────────────────────────────────────────────────────────────────

def parse_omo_pdf(pdf_bytes: bytes, hint_date: Optional[datetime.date], pdf_url: str) -> List[Dict]:
    """
    Parse a BB OMO press release PDF.
    Returns list of transaction dicts, one per (instrument, tenor) row where accepted != 0.
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
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
    except Exception as exc:
        log.error("pdfplumber failed for %s: %s", pdf_url, exc)
        return []

    # Extract transaction date from PDF text (e.g. "as on 21 April 2026")
    txn_date = _parse_date_from_text(full_text) or hint_date or datetime.date.today()
    log.debug("OMO PDF date: %s from %s", txn_date, pdf_url)

    transactions: List[Dict] = []
    current_instrument: Optional[str] = None
    current_direction:  Optional[str] = None
    # Some PDFs put the tenor data row BEFORE the instrument name (e.g. CB_REPO in Apr 21).
    # We save that orphan row and assign the instrument when the name appears on the next line.
    pending: Optional[Dict] = None

    def _extract_nums(line: str, tenor_days: int) -> List[float]:
        """Extract all numbers in positional order — KEEP zeros, they are real column values."""
        s = re.sub(r'\d+\s*[-\s]*(days?|day)\b', '', line, flags=re.I)
        s = re.sub(r'[A-Za-z\s\(\)\[\]/]+', ' ', s)
        result = []
        for tok in re.findall(r'-?[\d,]+\.?\d*', s):
            v = _clean_num(tok)
            if v is not None:
                result.append(v)
        return result

    def _make_txn(instr, direction, tenor_days, nums, txn_date, pdf_url) -> Optional[Dict]:
        """Build a transaction dict. Returns None if accepted == 0 (maturity-only row)."""
        if len(nums) < 2:
            return None
        # Column layout: Offered | Accepted | Rate (may split to 2 if "4.00-5.25") | Maturity | Net
        # Zeros are real — accepted = nums[1], not filtered
        accepted = nums[1]
        if abs(accepted) < 0.01:
            # No new transaction today for this instrument/tenor (only maturities happened)
            return None
        rate = next((n for n in nums[2:] if 0 < abs(n) < 30), None)
        return {
            "transaction_date":   txn_date,
            "maturity_date":      txn_date + datetime.timedelta(days=tenor_days),
            "instrument":         instr,
            "tenor_label":        f"{tenor_days}D",
            "tenor_days":         tenor_days,
            "accepted_bdt_crore": abs(accepted),
            "rate_pct":           rate,
            "direction":          direction,
            "source_pdf":         pdf_url,
        }

    for raw_line in full_text.split("\n"):
        line = raw_line.strip()
        if not line or _should_skip(line):
            continue

        tenor_days = _parse_tenor(line)

        if tenor_days is None:
            # Instrument name line — set current instrument and flush any pending orphan row
            m = _match_instrument(line)
            if m:
                current_instrument, current_direction = m
                if pending is not None:
                    # Retroactively assign instrument to the orphan row we held
                    txn = _make_txn(current_instrument, current_direction,
                                    pending["tenor_days"], pending["nums"],
                                    txn_date, pdf_url)
                    if txn:
                        transactions.append(txn)
                    pending = None
            continue

        # ── Data row with a tenor ─────────────────────────────────────────────
        nums = _extract_nums(line, tenor_days)

        # Instrument may be inline on this line
        instr_match = _match_instrument(line)
        if instr_match:
            current_instrument, current_direction = instr_match
            pending = None  # inline instrument means no orphan issue

        if not current_instrument:
            # Orphan row — save it, instrument name will appear on the next line
            pending = {"tenor_days": tenor_days, "nums": nums}
            log.debug("Orphan tenor row saved (tenor=%dD): %s", tenor_days, line)
            continue

        pending = None
        txn = _make_txn(current_instrument, current_direction,
                        tenor_days, nums, txn_date, pdf_url)
        if txn:
            transactions.append(txn)
        else:
            log.debug("Skipped zero-accepted row: %s", line)

    log.info(
        "Parsed %d transactions from OMO PDF (date=%s, url=%s)",
        len(transactions), txn_date, Path(pdf_url).name,
    )
    return transactions


# ── Chrome helpers ────────────────────────────────────────────────────────────

def _chrome_major_version() -> Optional[int]:
    try:
        import winreg, re as _re
        for hive, path in [
            (winreg.HKEY_CURRENT_USER,  r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
        ]:
            try:
                k = winreg.OpenKey(hive, path)
                ver = winreg.QueryValueEx(k, "version")[0]
                m = _re.match(r"(\d+)\.", ver)
                if m:
                    return int(m.group(1))
            except OSError:
                continue
    except ImportError:
        pass
    return None


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
