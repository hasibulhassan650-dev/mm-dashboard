"""
fetchers/fx.py — Fetch FX auction results from BB.

TARGET: https://www.bb.org.bd/en/index.php/fxmarket/fx_auction_result

The default page load returns all historical FX auction results — no form
submission needed. Uses Chrome to bypass F5/TSPD bot protection.

TABLE COLUMNS (10):
  AuctionDate | SettlementDate | AuctionType | NumberofBids/Offers |
  Bid/OfferAmount($m) | Bid/OfferRange | NumberofAcceptedBid/Offers |
  AcceptedAmount($m) | CutoffRate | WeightedAverageRate
"""
import datetime
import logging
import re
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

_URL = "https://www.bb.org.bd/en/index.php/fxmarket/fx_auction_result"


def _chrome_major_version() -> Optional[int]:
    from fetchers.bb_session import get_chrome_version
    return get_chrome_version()


def _parse_float(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_int(s: str) -> Optional[int]:
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_date(s: str) -> Optional[datetime.date]:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_fx_table(html: str) -> List[Dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        return []

    rows = []
    today = datetime.date.today()

    for tr in tables[0].find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 10:
            continue

        auction_date = _parse_date(cells[0])
        if not auction_date:
            continue

        raw_settle = _parse_date(cells[1])
        # Clamp obviously wrong settlement dates (e.g. typo 2027 → should be ~auction+2)
        if raw_settle and abs((raw_settle - auction_date).days) > 30:
            raw_settle = auction_date + datetime.timedelta(days=1)

        rows.append({
            "auction_date":             auction_date,
            "settlement_date":          raw_settle,
            "auction_type":             cells[2].strip(),
            "num_bids":                 _parse_int(cells[3]),
            "bid_amount_usd_mill":      _parse_float(cells[4]),
            "bid_range":                cells[5].strip(),
            "num_accepted":             _parse_int(cells[6]),
            "accepted_amount_usd_mill": _parse_float(cells[7]),
            "cutoff_rate":              _parse_float(cells[8]),
            "weighted_avg_rate":        _parse_float(cells[9]),
        })

    return rows


def fetch_fx_auctions() -> List[Dict]:
    """Load the BB FX auction result page and return all rows."""
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
    except ImportError as e:
        log.warning("undetected-chromedriver not available: %s", e)
        return []

    import time

    options = uc.ChromeOptions()
    options.add_argument("--window-position=-10000,-10000")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = uc.Chrome(options=options, version_main=_chrome_major_version())
        driver.get(_URL)

        # Wait for bot challenge to clear and table to appear
        for _ in range(20):
            time.sleep(3)
            if driver.find_elements(By.TAG_NAME, "table"):
                break
        else:
            log.warning("FX: page never loaded table")
            return []

        rows = parse_fx_table(driver.page_source)
        log.info("FX: parsed %d rows", len(rows))
        return rows

    except Exception as exc:
        log.error("FX fetch error: %s", exc)
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
