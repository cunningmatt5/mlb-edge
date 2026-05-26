"""Download and parse SBRO historical MLB closing lines, join to games.parquet.

Sports Book Reviews Online (SBRO) provides free Excel archives of historical
MLB closing moneylines and totals. This module downloads those files, parses
the two-rows-per-game format (visitor row + home row), and joins to the
games.parquet produced by historical.py, outputting closing_lines.parquet.

Usage:
    python -m pipeline.odds_historical --seasons 2019,2020,2021,2022,2023,2024
"""
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

SBRO_BASE   = "https://www.sportsbookreviewsonline.com/scoresoddsarchives/mlb"
TIMEOUT     = 45
SEASONS_DIR = Path(__file__).parent.parent / "data" / "seasons"

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


def build_season_closing_lines(season: int, seasons_dir: Optional[Path] = None) -> None:
    """Download SBRO data for one season, join to games.parquet, save closing_lines.parquet."""
    if seasons_dir is None:
        seasons_dir = SEASONS_DIR
    output_dir = seasons_dir / str(season)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / "closing_lines.parquet"
    if out_path.exists():
        log.info("SBRO %d: closing_lines.parquet exists — skipping", season)
        return

    # Need games.parquet first
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

    # Normalize for join
    from pipeline.odds import no_vig_prob

    games_df["_dn"]  = games_df["date"].astype(str)
    games_df["_hn"]  = games_df["home_team"].apply(_norm_team)
    games_df["_an"]  = games_df["away_team"].apply(_norm_team)

    sbro_df["_dn"]   = sbro_df["date"].astype(str)
    sbro_df["_hn"]   = sbro_df["home_team"].apply(_norm_team)
    sbro_df["_an"]   = sbro_df["away_team"].apply(_norm_team)

    # Primary join: date + teams
    merged = games_df.merge(
        sbro_df[[
            "_dn", "_hn", "_an",
            "home_ml", "away_ml", "closing_total",
            "home_score", "away_score",
        ]].rename(columns={
            "home_score": "_sbro_home",
            "away_score": "_sbro_away",
        }),
        on=["_dn", "_hn", "_an"],
        how="left",
    )

    # Deduplicate doubleheaders: when multiple SBRO rows matched the same game,
    # prefer the row whose score matches the MLB API score
    if merged.duplicated(subset=["game_pk"]).any():
        score_match = (
            (merged["home_score"] == merged["_sbro_home"]) &
            (merged["away_score"] == merged["_sbro_away"])
        )
        # Keep score-matching rows; for non-duplicates keep as-is
        dupes = merged.duplicated(subset=["game_pk"], keep=False)
        merged = pd.concat([
            merged[~dupes],
            merged[dupes & score_match],
            merged[dupes & ~score_match].drop_duplicates(subset=["game_pk"], keep="first"),
        ]).drop_duplicates(subset=["game_pk"], keep="first")

    # Compute no-vig implied probabilities
    def _implied(row):
        hml = row["home_ml"]
        aml = row["away_ml"]
        if pd.notna(hml) and pd.notna(aml):
            try:
                hp, ap = no_vig_prob(int(hml), int(aml))
                return round(hp, 4), round(ap, 4)
            except Exception:
                pass
        return None, None

    probs = merged.apply(lambda r: pd.Series(_implied(r), index=["home_implied_prob", "away_implied_prob"]), axis=1)
    merged = pd.concat([merged, probs], axis=1)

    # Default over/under price to -110 (SBRO doesn't always include)
    merged["over_price"]  = -110
    merged["under_price"] = -110

    keep = [
        "game_pk", "date", "home_team", "away_team",
        "home_score", "away_score",
        "home_ml", "away_ml", "closing_total",
        "over_price", "under_price",
        "home_implied_prob", "away_implied_prob",
    ]
    out_df = merged[[c for c in keep if c in merged.columns]].copy()
    out_df = out_df.drop_duplicates(subset=["game_pk"])

    n_matched = out_df["home_ml"].notna().sum()
    match_pct = n_matched / max(len(out_df), 1) * 100
    log.info(
        "SBRO %d: %d games total, %d matched closing lines (%.1f%%)",
        season, len(out_df), n_matched, match_pct,
    )
    if match_pct < 50:
        log.warning(
            "SBRO %d: low match rate — team name mapping may need adjustment. "
            "Sample unmatched home teams: %s",
            season,
            list(merged.loc[merged["home_ml"].isna(), "home_team"].unique()[:5]),
        )

    out_df.to_parquet(out_path, index=False)
    log.info("SBRO %d: saved closing_lines.parquet (%d rows)", season, len(out_df))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Build historical closing lines from SBRO")
    parser.add_argument(
        "--seasons",
        default="2019,2020,2021,2022,2023,2024",
        help="Comma-separated years (e.g. 2019,2020,2021,2022,2023,2024)",
    )
    args = parser.parse_args()

    base = Path(__file__).parent.parent / "data" / "seasons"
    for s in args.seasons.split(","):
        season = int(s.strip())
        log.info("=== Building closing lines for %d ===", season)
        build_season_closing_lines(season, base)
