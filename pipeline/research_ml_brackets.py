"""MLB Edge research — moneyline odds-bracket / favorite-longshot bias.

Bands every game side by its American price (10-cent brackets) and, per bracket, compares the
ACTUAL win rate to the NO-VIG fair probability (de-vigged) and computes flat-bet ROI. Done across
three line sources so we can see where (if anywhere) the market is exploitably biased:
  - pinnacle_close : sharp closing line (docs/backtest.json + history.json)
  - soft_close     : multi-book consensus close (free SBR dataset)
  - soft_open      : multi-book consensus open  (free SBR dataset) — least efficient
$0 (no Odds API). Per-season + OOS stability baked in (don't trust one-season slices).

Usage:  python -m pipeline.research_ml_brackets
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.utils import american_to_decimal  # noqa: E402

ROOT = Path(__file__).parent.parent
RAW = ROOT / "data" / "external" / "mlb_odds_dataset.json"
MIN_BRACKET_N = 150


def _norm(name):
    n = re.sub(r"[^a-z0-9]", "", str(name).lower()); return "athletics" if n.endswith("athletics") else n


def _dec_to_american(dec: float) -> int:
    if dec <= 1: return 0
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def _bracket(american: int) -> str:
    """10-cent band label; extremes grouped for sample size."""
    a = american
    if a <= -250: return "-250+ (heavy fav)"
    if a < 0:
        lo = (abs(a) // 10) * 10
        return f"-{lo}s"
    if a >= 250: return "+250+ (longshot)"
    lo = (a // 10) * 10
    return f"+{lo}s"


def _bracket_sort_key(label: str) -> int:
    if "heavy fav" in label: return -10000
    if "longshot" in label: return 10000
    m = re.search(r"([+-]\d+)", label); return int(m.group(1)) if m else 0


# ---- outcomes ----
def _outcomes():
    out = {}
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    rows = list(bt)
    hp = ROOT / "docs" / "history.json"
    if hp.exists():
        raw = json.loads(hp.read_text(encoding="utf-8"))
        rows += raw if isinstance(raw, list) else raw.get("games", [])
    for g in rows:
        pk = g.get("gamePk")
        if pk and g.get("actual_winner") in ("home", "away"):
            out[str(pk)] = {"winner": g["actual_winner"], "season": g.get("season"),
                            "home_ml": g.get("home_ml"), "away_ml": g.get("away_ml")}
    return out


def _target_map(out):
    tgt = {}
    bt = json.loads((ROOT / "docs" / "backtest.json").read_text(encoding="utf-8"))["games"]
    for g in bt:
        d = (g.get("date") or "")[:10]
        if d and g.get("gamePk") and g.get("home_team") and g.get("away_team"):
            tgt[(d, _norm(g["home_team"]), _norm(g["away_team"]))] = str(g["gamePk"])
    return tgt


def _consensus(line_key, out, tgt):
    """{gamePk: (home_dec, away_dec)} consensus across books for openingLine/currentLine."""
    if not RAW.exists():
        return {}
    data = json.loads(RAW.read_text(encoding="utf-8"))
    res = {}
    for date, games in data.items():
        for g in games:
            gv = g.get("gameView", {})
            pk = tgt.get((date[:10], _norm((gv.get("homeTeam") or {}).get("fullName", "")),
                          _norm((gv.get("awayTeam") or {}).get("fullName", ""))))
            if not pk:
                continue
            hd, ad = [], []
            for bm in g.get("odds", {}).get("moneyline", []):
                ln = bm.get(line_key) or {}
                if ln.get("homeOdds") is not None and ln.get("awayOdds") is not None:
                    hd.append(american_to_decimal(ln["homeOdds"])); ad.append(american_to_decimal(ln["awayOdds"]))
            if hd:
                res[pk] = (sum(hd) / len(hd), sum(ad) / len(ad))
    return res


def _observations(source, out, tgt):
    """Yield per-side observations: (american, dec, fair_prob, won, season)."""
    obs = []
    if source == "pinnacle_close":
        for pk, o in out.items():
            if o["home_ml"] is None or o["away_ml"] is None:
                continue
            hd, ad = american_to_decimal(o["home_ml"]), american_to_decimal(o["away_ml"])
            _emit(obs, hd, ad, o["winner"] == "home", o["winner"] == "away", o["season"],
                  o["home_ml"], o["away_ml"])
    else:
        key = "currentLine" if source == "soft_close" else "openingLine"
        cons = _consensus(key, out, tgt)
        for pk, (hd, ad) in cons.items():
            o = out.get(pk)
            if not o:
                continue
            _emit(obs, hd, ad, o["winner"] == "home", o["winner"] == "away", o["season"],
                  _dec_to_american(hd), _dec_to_american(ad))
    return obs


def _emit(obs, hd, ad, home_won, away_won, season, ham, aam):
    ih, ia = 1 / hd, 1 / ad
    fair_h, fair_a = ih / (ih + ia), ia / (ih + ia)
    obs.append((ham, hd, fair_h, home_won, season))
    obs.append((aam, ad, fair_a, away_won, season))


def _report(source, obs):
    brackets = {}
    for american, dec, fair, won, season in obs:
        b = brackets.setdefault(_bracket(american), {"n": 0, "w": 0, "fair": 0.0, "units": 0.0, "imp": 0.0, "season": {}})
        b["n"] += 1; b["w"] += 1 if won else 0; b["fair"] += fair; b["imp"] += 1 / dec
        b["units"] += (dec - 1) if won else -1.0
        s = b["season"].setdefault(season, [0, 0.0]); s[0] += 1; s[1] += (dec - 1) if won else -1.0
    print(f"\n### {source}  ({len(obs)//2} games, {len(obs)} sides)")
    print(f"  {'bracket':<18} {'n':>5} {'impl%':>6} {'fair%':>6} {'act%':>6} {'act-fair':>9} {'ROI%':>8}  stability")
    for label in sorted(brackets, key=_bracket_sort_key):
        b = brackets[label]
        if b["n"] < MIN_BRACKET_N:
            continue
        impl, fair, act = b["imp"] / b["n"] * 100, b["fair"] / b["n"] * 100, b["w"] / b["n"] * 100
        roi = b["units"] / b["n"] * 100
        srois = [round(v[1] / v[0] * 100, 1) for v in b["season"].values() if v[0] >= 30]
        pos = sum(1 for r in srois if r > 0)
        stable = f"{pos}/{len(srois)} seasons+  {srois}" if srois else "thin"
        flag = "  <<<" if roi > 2 and len(srois) >= 3 and pos >= len(srois) - 1 else ""
        print(f"  {label:<18} {b['n']:>5} {impl:>6.1f} {fair:>6.1f} {act:>6.1f} {act-fair:>+9.1f} {roi:>+8.2f}  {stable}{flag}")


def run():
    out = _outcomes()
    tgt = _target_map(out)
    print("=" * 100)
    print("MONEYLINE ODDS-BRACKET / FAVORITE-LONGSHOT BIAS — actual win% vs NO-VIG fair, + flat-bet ROI")
    print("=" * 100)
    for source in ("pinnacle_close", "soft_close", "soft_open"):
        _report(source, _observations(source, out, tgt))
    print("\n'<<<' = ROI > +2%, >=3 seasons, <=1 losing season (worth a look). act-fair = bias vs de-vigged price.")


if __name__ == "__main__":
    run()
