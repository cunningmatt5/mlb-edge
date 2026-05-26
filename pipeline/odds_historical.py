"""Download historical MLB closing lines, join to games.parquet.

Two data sources:
  - SBRO (Sports Book Reviews Online): free Excel archives, 2019–2021
  - The Odds API: professional plan required, 2022–present

Usage:
    # SBRO (free, 2019-2021)
    python -m pipeline.odds_historical --seasons 2019,2020,2021

    # The Odds API (requires Professional plan key, 2022-2024)
    python -m pipeline.odds_historical --seasons 2022,2023,2024 --source odds_api --api-key YOUR_KEY
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

SBRO_BASE      = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/mlb"
ODDS_API_BASE  = "https://api.the-odds-api.com/v4"
TIMEOUT        = 45
SEASONS_DIR    = Path(__file__).parent.parent / "data" / "seasons"

# Bookmaker preference order for The Odds API (Pinnacle = sharpest closing line)
_PREFERRED_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
    "Referer": "https://www.sportsbookreviewsonline.com/",
}


# ---------------------------------------------------------------------------
# Team name mapping: SBRO abbrev → full MLB name (as in games.parquet)
# ---------------------------------------------------------------------------

SBRO_TO_MLB_NAME: dict[str, str] = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CHW": "Chicago White Sox",
    "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "FLA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SEA": "Seattle Mariners",
    "STL": "St. Louis Cardinals",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    # Common SBRO variants
    "KAN": "Kansas City Royals",
    "KC":  "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LA":  "Los Angeles Angels",
    "ANA": "Los Angeles Angels",
    "OAK": "Oakland Athletics",
    "ATH": "Athletics",
    "SD":  "San Diego Padres",
    "SDG": "San Diego Padres",
    "SDP": "San Diego Padres",
    "SF":  "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "TB":  "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TAM": "Tampa Bay Rays",
    "WAS": "Washington Nationals",
    "WSH": "Washington Nationals",
    # Cleveland: Indians → Guardians in 2022
    "CLE": "_CLEVELAND_RESOLVE_",
    "IND": "_CLEVELAND_RESOLVE_",
    "CLV": "_CLEVELAND_RESOLVE_",
}


def _sbro_to_mlb(abbr: str, year: int) -> str:
    name = SBRO_TO_MLB_NAME.get(abbr.upper().strip())
    if name is None:
        return abbr
    if name == "_CLEVELAND_RESOLVE_":
        return "Cleveland Guardians" if year >= 2022 else "Cleveland Indians"
    return name


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

_XLSX_MAGIC = b"PK\x03\x04"  # ZIP/XLSX files always start with these 4 bytes


def _is_valid_excel(content: bytes) -> bool:
    return len(content) > 4 and content[:4] == _XLSX_MAGIC


def download_sbro_season(year: int, output_dir: Path) -> Optional[Path]:
    """Download SBRO MLB odds Excel for one season. Idempotent.

    SBRO may return an HTML redirect for direct URL requests; validates the
    response is actually an Excel file before saving.

    Manual fallback: if automated download fails, place the Excel file at
        data/seasons/{year}/sbro_raw.xlsx
    and re-run — the function skips existing valid files automatically.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "sbro_raw.xlsx"

    # Skip if a valid Excel file already exists
    if out_path.exists() and out_path.stat().st_size > 10_000:
        if _is_valid_excel(out_path.read_bytes()):
            log.info("SBRO %d: valid sbro_raw.xlsx exists — skipping download", year)
            return out_path
        log.warning("SBRO %d: existing file is not a valid Excel — re-downloading", year)

    # Try multiple URL patterns (SBRO occasionally changes naming)
    url_patterns = [
        f"{SBRO_BASE}/mlb%20odds%20{year}.xlsx",
        f"{SBRO_BASE}/mlb odds {year}.xlsx",
        f"{SBRO_BASE}/mlb%20odds%20{year}.xlsm",
    ]

    # Touch the archive index first to obtain any required cookies/session
    sess = requests.Session()
    try:
        sess.get(f"{SBRO_BASE}/mlboddsarchives.htm", headers=_HEADERS, timeout=TIMEOUT)
    except Exception:
        pass

    for url in url_patterns:
        try:
            log.info("SBRO %d: trying %s", year, url)
            r = sess.get(url, headers=_HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and _is_valid_excel(r.content):
                out_path.write_bytes(r.content)
                log.info("SBRO %d: saved %d bytes → %s", year, len(r.content), out_path)
                return out_path
            log.debug(
                "SBRO %d: HTTP %d, Content-Type=%s, size=%d for %s — not a valid Excel",
                year, r.status_code, r.headers.get("Content-Type", "?"), len(r.content), url,
            )
        except Exception as exc:
            log.warning("SBRO %d: request error for %s: %s", year, url, exc)

    log.error(
        "SBRO %d: automated download failed. Manually download the Excel from "
        "https://www.sportsbookreviewsonline.com/scoresoddsarchives/mlb/ "
        "and place it at data/seasons/%d/sbro_raw.xlsx, then re-run.",
        year, year,
    )
    return None


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_sbro_excel(xlsx_path: Path, year: int) -> pd.DataFrame:
    """Parse SBRO Excel into a normalized game-level DataFrame.

    SBRO format: 2 rows per game — visitor row (VH='V') then home row (VH='H').
    Date appears on the visitor row; other values paired by rotation number.
    """
    try:
        raw = pd.read_excel(xlsx_path, engine="openpyxl", header=0, dtype=str)
    except Exception as exc:
        log.error("SBRO %d: failed to read Excel %s: %s", year, xlsx_path, exc)
        return pd.DataFrame()

    # Normalize whitespace
    for col in raw.columns:
        if raw[col].dtype == object:
            raw[col] = raw[col].str.strip()

    # Identify key columns
    vh_col    = _find_col_by_header(raw, ["VH", "V/H", "V H"]) or _find_vh_by_content(raw)
    if vh_col is None:
        log.error("SBRO %d: cannot identify VH column — columns: %s", year, list(raw.columns))
        return pd.DataFrame()

    cols    = list(raw.columns)
    vh_idx  = cols.index(vh_col)

    date_col  = _find_col_by_header(raw, ["Date", "DATE"]) or _find_date_col_by_content(raw, vh_idx)
    team_col  = _find_col_by_header(raw, ["Team", "TEAM"]) or (cols[vh_idx + 1] if vh_idx + 1 < len(cols) else None)
    final_col = _find_col_by_header(raw, ["Final", "FINAL", "F"])
    close_col = _find_col_by_header(raw, ["Close", "CLOSE", "Cl", "CL"])
    ml_col    = _find_col_by_header(raw, ["ML", "Money", "M/L", "Moneyline"])

    # Positional fallbacks: SBRO standard layout after vh_idx
    if final_col is None:
        final_col = _find_final_positional(raw, vh_idx)
    if close_col is None:
        close_col = _find_close_positional(raw, vh_idx)
    if ml_col is None and close_col:
        close_idx = cols.index(close_col)
        if close_idx + 1 < len(cols):
            ml_col = cols[close_idx + 1]

    log.info(
        "SBRO %d columns: date=%s, team=%s, vh=%s, final=%s, close=%s, ml=%s",
        year, date_col, team_col, vh_col, final_col, close_col, ml_col,
    )
    if not all([date_col, team_col, final_col, close_col, ml_col]):
        log.error("SBRO %d: missing required columns — cannot parse", year)
        return pd.DataFrame()

    # Forward-fill dates (blank on H rows and on repeated dates)
    raw[date_col] = raw[date_col].replace({"": None, "nan": None, "None": None})
    raw[date_col] = raw[date_col].ffill()

    # Filter to V/H rows only
    mask = raw[vh_col].str.upper().isin(["V", "H"])
    rows = raw[mask].reset_index(drop=True)

    records = []
    i = 0
    while i < len(rows) - 1:
        v_row = rows.iloc[i]
        h_row = rows.iloc[i + 1]

        if v_row[vh_col].upper() != "V" or h_row[vh_col].upper() != "H":
            i += 1
            continue

        game_date = _parse_date(str(v_row[date_col]) if pd.notna(v_row[date_col]) else "", year)
        if not game_date:
            i += 2
            continue

        away_abbr = str(v_row[team_col])
        home_abbr = str(h_row[team_col])
        away_team = _sbro_to_mlb(away_abbr, year)
        home_team = _sbro_to_mlb(home_abbr, year)

        away_score = _parse_int(v_row[final_col])
        home_score = _parse_int(h_row[final_col])

        away_ml = _parse_ml(v_row[ml_col])
        home_ml = _parse_ml(h_row[ml_col])

        # Closing total is on whichever row has it (try V first, then H)
        closing_total = _parse_total(v_row[close_col]) or _parse_total(h_row[close_col])

        records.append({
            "date":          game_date,
            "away_team":     away_team,
            "home_team":     home_team,
            "away_score":    away_score,
            "home_score":    home_score,
            "away_ml":       away_ml,
            "home_ml":       home_ml,
            "closing_total": closing_total,
        })
        i += 2

    df = pd.DataFrame(records)
    if df.empty:
        log.warning("SBRO %d: no game records parsed", year)
        return df

    ml_pct    = df["home_ml"].notna().mean() * 100
    total_pct = df["closing_total"].notna().mean() * 100
    log.info(
        "SBRO %d: parsed %d games — ML %.0f%%, total %.0f%%",
        year, len(df), ml_pct, total_pct,
    )
    return df


# ---------------------------------------------------------------------------
# Column detection helpers
# ---------------------------------------------------------------------------

def _find_col_by_header(df: pd.DataFrame, names: list[str]) -> Optional[str]:
    for col in df.columns:
        if str(col).strip().upper() in [n.upper() for n in names]:
            return col
    return None


def _find_vh_by_content(df: pd.DataFrame) -> Optional[str]:
    """Find column whose values are dominantly 'V' and 'H'."""
    for col in df.columns:
        vals = df[col].dropna().astype(str).str.strip().str.upper()
        if len(vals) == 0:
            continue
        vh_ratio = vals.isin(["V", "H"]).sum() / len(vals)
        if vh_ratio > 0.4:
            return col
    return None


def _find_date_col_by_content(df: pd.DataFrame, vh_idx: int) -> Optional[str]:
    """Find column with date-like values (M/D or M/D/YYYY) to the left of VH."""
    cols = list(df.columns)
    for col in cols[:vh_idx + 1]:
        sample = df[col].dropna().astype(str).head(30)
        if sample.str.match(r"\d{1,2}/\d{1,2}").any():
            return col
    return cols[0] if cols else None


def _find_final_positional(df: pd.DataFrame, vh_idx: int) -> Optional[str]:
    """Heuristic: find score column (integers 0-30) after the team column."""
    cols = list(df.columns)
    candidates = cols[vh_idx + 2:]
    for col in candidates:
        sample = df[col].dropna().astype(str).str.strip()
        numeric_pct = sample.str.match(r"^\d{1,2}$").mean()
        # Scores are single/double-digit integers, covering ~half the rows
        if 0.3 < numeric_pct < 0.95:
            return col
    return None


def _find_close_positional(df: pd.DataFrame, vh_idx: int) -> Optional[str]:
    """Heuristic: find closing total column (half-point values like 8, 8.5, 8½)."""
    cols = list(df.columns)
    for col in reversed(cols[vh_idx + 3:]):
        sample = df[col].dropna().astype(str).str.strip()
        # Totals: 7-15 range, often with .5 or ½ suffix
        half_pt   = sample.str.contains(r"[½¼¾]|\.5$", regex=True)
        in_range  = sample.str.match(r"^\d{1,2}(\.\d)?$")
        hit_rate  = (half_pt | in_range).mean()
        if hit_rate > 0.25:
            return col
    return None


# ---------------------------------------------------------------------------
# Value parsers
# ---------------------------------------------------------------------------

def _parse_date(raw: str, year: int) -> Optional[str]:
    """Convert SBRO M/D or M/D/YYYY to ISO YYYY-MM-DD."""
    raw = raw.strip()
    parts = raw.split("/")
    if len(parts) >= 2:
        try:
            month = int(parts[0])
            day   = int(parts[1])
            yr    = int(parts[2]) if len(parts) > 2 else year
            return f"{yr:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass
    return None


def _parse_int(val) -> Optional[int]:
    s = str(val).strip()
    return int(s) if s.isdigit() else None


def _parse_total(val) -> Optional[float]:
    """Parse game total: '8', '8.5', '8½' → float. Returns None for NL/blank."""
    s = str(val).strip()
    if not s or s.lower() in ("nan", "nl", "pk", "ev", "-", "none", ""):
        return None
    s = s.replace("½", ".5").replace("¼", ".25").replace("¾", ".75")
    try:
        v = float(s)
        return v if 5.0 <= v <= 20.0 else None  # sanity: totals are 5-20
    except ValueError:
        return None


def _parse_ml(val) -> Optional[int]:
    """Parse moneyline odds: '-135', '+125', 'pk' → int. Returns None for NL/blank."""
    s = str(val).strip()
    if not s or s.lower() in ("nan", "nl", "-", "none", ""):
        return None
    if s.lower() in ("pk", "ev"):
        return 100
    cleaned = re.sub(r"[^\d+\-]", "", s)
    if not cleaned or cleaned in ("+", "-"):
        return None
    try:
        v = int(cleaned)
        return v if -5000 <= v <= 5000 else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Join to games.parquet and save closing_lines.parquet
# ---------------------------------------------------------------------------

def _norm_team(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def build_season_closing_lines(
    season: int,
    seasons_dir: Optional[Path] = None,
    source: str = "sbro",
    api_key: Optional[str] = None,
) -> None:
    """Build closing_lines.parquet for one season.

    source="sbro"     — download from SBRO Excel archives (free, 2019–2021)
    source="odds_api" — fetch from The Odds API (requires Professional plan, 2022+)
    """
    if source == "odds_api":
        if not api_key:
            log.error("Odds API %d: --api-key / ODDS_API_KEY required for source=odds_api", season)
            return
        fetch_odds_api_historical_season(season, api_key, seasons_dir)
        return

    # --- SBRO path ---
    if seasons_dir is None:
        seasons_dir = SEASONS_DIR
    output_dir = seasons_dir / str(season)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / "closing_lines.parquet"
    if out_path.exists():
        log.info("SBRO %d: closing_lines.parquet exists — skipping", season)
        return

    games_path = output_dir / "games.parquet"
    if not games_path.exists():
        log.error("SBRO %d: games.parquet not found — run historical.py first", season)
        return

    xlsx_path = download_sbro_season(season, output_dir)
    if xlsx_path is None:
        log.error("SBRO %d: skipping season — no Excel downloaded", season)
        return

    sbro_df = parse_sbro_excel(xlsx_path, season)
    if sbro_df.empty:
        log.error("SBRO %d: no records parsed — skipping", season)
        return

    games_df = pd.read_parquet(games_path)
    _save_closing_lines_from_df(sbro_df, games_df, season, out_path, source="SBRO")


# ---------------------------------------------------------------------------
# The Odds API historical fetch (2022+)
# ---------------------------------------------------------------------------

def _parse_odds_api_event(event: dict, date: str) -> Optional[dict]:
    """Parse one Odds API event dict → closing lines record."""
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")

    bookmakers = event.get("bookmakers", [])
    bm_by_key  = {bm["key"]: bm for bm in bookmakers}

    bm = None
    for key in _PREFERRED_BOOKS:
        if key in bm_by_key:
            bm = bm_by_key[key]
            break
    if bm is None and bookmakers:
        bm = bookmakers[0]
    if bm is None:
        return None

    markets = {m["key"]: m for m in bm.get("markets", [])}

    home_ml = away_ml = None
    if "h2h" in markets:
        hn = _norm_team(home_team)
        an = _norm_team(away_team)
        for outcome in markets["h2h"].get("outcomes", []):
            on = _norm_team(outcome.get("name", ""))
            if on == hn:
                home_ml = outcome.get("price")
            elif on == an:
                away_ml = outcome.get("price")

    closing_total = over_price = under_price = None
    if "totals" in markets:
        for outcome in markets["totals"].get("outcomes", []):
            nm = outcome.get("name", "").lower()
            if nm == "over":
                closing_total = outcome.get("point")
                over_price    = outcome.get("price")
            elif nm == "under":
                under_price = outcome.get("price")

    return {
        "date":          date,
        "home_team":     home_team,
        "away_team":     away_team,
        "home_ml":       home_ml,
        "away_ml":       away_ml,
        "closing_total": closing_total,
        "over_price":    over_price  if over_price  else -110,
        "under_price":   under_price if under_price else -110,
    }


def fetch_odds_api_historical_season(
    season: int,
    api_key: str,
    seasons_dir: Optional[Path] = None,
) -> None:
    """Fetch one season of closing lines from The Odds API historical endpoint.

    Queries once per unique game date at {date}T22:00:00Z (6 PM ET snapshot,
    covers pre-game closing lines for ~90% of games).

    Progress is cached to odds_api_cache.json so interrupted runs resume
    without re-fetching already-completed dates.

    Requires The Odds API Professional plan ($29/month).
    Each season costs ~170 API calls (one per game date).
    """
    if seasons_dir is None:
        seasons_dir = SEASONS_DIR
    output_dir = seasons_dir / str(season)

    games_path = output_dir / "games.parquet"
    if not games_path.exists():
        log.error("Odds API %d: games.parquet not found — run historical.py first", season)
        return

    out_path = output_dir / "closing_lines.parquet"
    if out_path.exists():
        log.info("Odds API %d: closing_lines.parquet exists — skipping", season)
        return

    cache_path = output_dir / "odds_api_cache.json"
    if cache_path.exists():
        with open(cache_path) as f:
            cache: dict = json.load(f)
        log.info("Odds API %d: loaded cache — %d dates already fetched", season, len(cache))
    else:
        cache = {}

    games_df = pd.read_parquet(games_path)
    all_dates = sorted(games_df["date"].astype(str).unique())
    remaining = [d for d in all_dates if d not in cache]
    log.info(
        "Odds API %d: %d game dates total, %d cached, %d to fetch",
        season, len(all_dates), len(all_dates) - len(remaining), len(remaining),
    )

    url = f"{ODDS_API_BASE}/sports/baseball_mlb/odds-history/"

    for i, date in enumerate(remaining):
        params = {
            "apiKey":      api_key,
            "regions":     "us",
            "markets":     "h2h,totals",
            "oddsFormat":  "american",
            "date":        f"{date}T22:00:00Z",
        }
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            quota_left = r.headers.get("x-requests-remaining", "?")

            if r.status_code == 401:
                log.error(
                    "Odds API %d: 401 Unauthorized — verify ODDS_API_KEY and "
                    "that account is on Professional plan or higher", season,
                )
                break
            if r.status_code == 429:
                log.error("Odds API %d: 429 quota exhausted — %d dates remain unfetched", season, len(remaining) - i)
                break
            if r.status_code == 422:
                log.warning("Odds API %d: 422 for %s — date out of plan's history window", season, date)
                cache[date] = []
            elif r.status_code != 200:
                log.warning("Odds API %d: HTTP %d for %s — caching empty", season, r.status_code, date)
                cache[date] = []
            else:
                body   = r.json()
                events = body.get("data", []) if isinstance(body, dict) else body
                cache[date] = events if isinstance(events, list) else []
                log.info(
                    "Odds API %d: %s → %d events  (quota remaining: %s)",
                    season, date, len(cache[date]), quota_left,
                )
        except Exception as exc:
            log.warning("Odds API %d: request error for %s: %s", season, date, exc)
            cache[date] = []

        # Save progress after every date — allows resuming if interrupted
        with open(cache_path, "w") as f:
            json.dump(cache, f)

        if i < len(remaining) - 1:
            time.sleep(0.3)  # ~3 requests/sec — polite and well within rate limits

    # Build records DataFrame from cache
    records = []
    for date, events in cache.items():
        for event in (events or []):
            rec = _parse_odds_api_event(event, date)
            if rec:
                records.append(rec)

    if not records:
        log.error("Odds API %d: no records in cache — closing_lines.parquet not saved", season)
        return

    _save_closing_lines_from_df(pd.DataFrame(records), games_df, season, out_path, source="Odds API")


def _save_closing_lines_from_df(
    odds_df: pd.DataFrame,
    games_df: pd.DataFrame,
    season: int,
    out_path: Path,
    source: str = "SBRO",
) -> None:
    """Join an odds DataFrame to games_df and save closing_lines.parquet."""
    from pipeline.odds import no_vig_prob

    odds_df["_dn"] = odds_df["date"].astype(str)
    odds_df["_hn"] = odds_df["home_team"].apply(_norm_team)
    odds_df["_an"] = odds_df["away_team"].apply(_norm_team)

    games_df = games_df.copy()
    games_df["_dn"] = games_df["date"].astype(str)
    games_df["_hn"] = games_df["home_team"].apply(_norm_team)
    games_df["_an"] = games_df["away_team"].apply(_norm_team)

    odds_cols = ["_dn", "_hn", "_an", "home_ml", "away_ml", "closing_total",
                 "over_price", "under_price"]
    score_cols = [c for c in ["home_score", "away_score"] if c in odds_df.columns]
    rename_map = {c: f"_src_{c}" for c in score_cols}
    odds_sel   = odds_df[odds_cols + score_cols].rename(columns=rename_map)

    merged = games_df.merge(odds_sel, on=["_dn", "_hn", "_an"], how="left")

    # Doubleheader disambiguation: prefer the row whose score matches MLB API
    if merged.duplicated(subset=["game_pk"]).any():
        score_match = pd.Series(False, index=merged.index)
        if "_src_home_score" in merged.columns:
            score_match = (
                (merged["home_score"] == merged["_src_home_score"]) &
                (merged["away_score"] == merged["_src_away_score"])
            )
        dupes = merged.duplicated(subset=["game_pk"], keep=False)
        merged = pd.concat([
            merged[~dupes],
            merged[dupes & score_match],
            merged[dupes & ~score_match].drop_duplicates(subset=["game_pk"], keep="first"),
        ]).drop_duplicates(subset=["game_pk"], keep="first")

    # No-vig implied probabilities
    def _implied(row):
        hml, aml = row.get("home_ml"), row.get("away_ml")
        if pd.notna(hml) and pd.notna(aml):
            try:
                hp, ap = no_vig_prob(int(hml), int(aml))
                return round(hp, 4), round(ap, 4)
            except Exception:
                pass
        return None, None

    probs  = merged.apply(
        lambda r: pd.Series(_implied(r), index=["home_implied_prob", "away_implied_prob"]), axis=1
    )
    merged = pd.concat([merged, probs], axis=1)
    merged["over_price"]  = merged.get("over_price",  pd.Series(-110, index=merged.index)).fillna(-110)
    merged["under_price"] = merged.get("under_price", pd.Series(-110, index=merged.index)).fillna(-110)

    keep   = ["game_pk", "date", "home_team", "away_team",
              "home_score", "away_score",
              "home_ml", "away_ml", "closing_total",
              "over_price", "under_price",
              "home_implied_prob", "away_implied_prob"]
    out_df = merged[[c for c in keep if c in merged.columns]].drop_duplicates(subset=["game_pk"])

    n_matched = out_df["home_ml"].notna().sum()
    match_pct = n_matched / max(len(out_df), 1) * 100
    log.info(
        "%s %d: %d games, %d matched (%.1f%%)",
        source, season, len(out_df), n_matched, match_pct,
    )
    if match_pct < 50:
        log.warning(
            "%s %d: low match rate — sample unmatched home teams: %s",
            source, season,
            list(merged.loc[merged["home_ml"].isna(), "home_team"].unique()[:5]),
        )

    out_df.to_parquet(out_path, index=False)
    log.info("%s %d: saved closing_lines.parquet (%d rows)", source, season, len(out_df))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Build historical closing lines")
    parser.add_argument(
        "--seasons",
        default="2019,2020,2021",
        help="Comma-separated years (e.g. 2019,2020,2021)",
    )
    parser.add_argument(
        "--source",
        choices=["sbro", "odds_api"],
        default="sbro",
        help="Data source: sbro (free, 2019-2021) or odds_api (Professional plan, 2022+)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ODDS_API_KEY", ""),
        help="The Odds API key (or set ODDS_API_KEY env var)",
    )
    args = parser.parse_args()

    base = Path(__file__).parent.parent / "data" / "seasons"
    for s in args.seasons.split(","):
        season = int(s.strip())
        log.info("=== Building closing lines for %d [source=%s] ===", season, args.source)
        build_season_closing_lines(season, base, source=args.source, api_key=args.api_key or None)
