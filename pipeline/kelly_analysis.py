"""Kelly criterion bankroll analysis on proven MLB edge signals.

Simulates 4 strategies starting from $10,000 over 2022–2025 backtest data:
  - Flat $100 per bet  (~1% of starting bankroll)
  - ¼ Kelly
  - ½ Kelly
  - Full Kelly

Win probabilities are estimated via LOYO (leave-one-year-out): for each
season, the win rate is computed from the OTHER 3 seasons, avoiding
lookahead bias and correcting for model overconfidence on ML bets.

Usage:
    python -m pipeline.kelly_analysis
    python -m pipeline.kelly_analysis --out data/kelly_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
BACKTEST_PATH = ROOT / "docs" / "backtest.json"

STARTING_BANKROLL = 10_000.0
SEASONS = [2022, 2023, 2024, 2025, 2026]

STRATEGIES = [
    ("Flat $100",  None,  100.0),   # (name, kelly_fraction, flat_stake)
    ("¼ Kelly",   0.25,  None),
    ("½ Kelly",   0.50,  None),
    ("Full Kelly", 1.00,  None),
]


# ── Math helpers ──────────────────────────────────────────────────────────────

def _american_to_decimal(odds: float) -> float:
    if odds >= 0:
        return 1 + odds / 100
    return 1 - 100 / odds


def _kelly_stake(bankroll: float, win_prob: float, decimal_odds: float, fraction: float) -> float:
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    full_kelly = (b * win_prob - (1 - win_prob)) / b
    return max(0.0, bankroll * full_kelly * fraction)


# ── Simulation engine ─────────────────────────────────────────────────────────

def _simulate(bets: list[dict], fraction: float | None, flat_stake: float | None) -> dict:
    """
    Simulate bankroll evolution from STARTING_BANKROLL.
    bets: [{win_prob, decimal_odds, won, season}]
    """
    bankroll = STARTING_BANKROLL
    peak = STARTING_BANKROLL
    max_drawdown = 0.0
    by_season: dict[int, dict] = {}

    for season in SEASONS:
        season_bets = [b for b in bets if b["season"] == season]
        season_start = bankroll
        n_placed = 0

        for bet in season_bets:
            if flat_stake is not None:
                stake = flat_stake
            else:
                stake = _kelly_stake(bankroll, bet["win_prob"], bet["decimal_odds"], fraction or 0.0)
            stake = min(stake, bankroll)
            if stake < 0.01:
                continue
            n_placed += 1
            if bet["won"]:
                bankroll += stake * (bet["decimal_odds"] - 1)
            else:
                bankroll -= stake
            peak = max(peak, bankroll)
            max_drawdown = max(max_drawdown, peak - bankroll)

        by_season[season] = {
            "start":   round(season_start, 2),
            "end":     round(bankroll, 2),
            "profit":  round(bankroll - season_start, 2),
            "n_bets":  n_placed,
        }

    seasons_profitable = sum(1 for s in by_season.values() if s["profit"] > 0)
    return {
        "final":              round(bankroll, 2),
        "roi":                round((bankroll - STARTING_BANKROLL) / STARTING_BANKROLL * 100, 1),
        "max_drawdown":       round(max_drawdown, 2),
        "seasons_profitable": seasons_profitable,
        "by_season":          by_season,
    }


def _print_filter(label: str, bets: list[dict], loyo_probs: dict) -> dict:
    """Run all 4 strategies, print results, return {strategy_name: result}."""
    per_yr = {yr: sum(1 for b in bets if b["season"] == yr) for yr in SEASONS}
    win_rates = {yr: (sum(1 for b in bets if b["season"] == yr and b["won"]) / n if n else 0)
                 for yr, n in per_yr.items()}

    print(f"\n{'='*74}")
    print(f"  {label}")
    print(f"  Total bets: {len(bets):,}   " + "  ".join(f"{yr}: {per_yr[yr]}" for yr in SEASONS))
    print(f"  LOYO win probs:  " + "  ".join(f"{yr}: {loyo_probs[yr]:.3f}" for yr in SEASONS))
    print(f"  Actual win rates:" + "  ".join(f"{yr}: {win_rates[yr]:.3f}" for yr in SEASONS))
    print(f"{'='*74}")
    print(f"  {'Strategy':<13}  {'Final Bank':>11}  {'Total ROI':>9}  {'Max DD':>10}  {'Profitable':>10}")
    print(f"  {'-'*60}")

    results: dict[str, dict] = {}
    for name, fraction, flat in STRATEGIES:
        r = _simulate(bets, fraction=fraction, flat_stake=flat)
        results[name] = r
        final_str = f"${r['final']:>10,.0f}"
        print(f"  {name:<13}  {final_str}  {r['roi']:>+8.1f}%  "
              f"${r['max_drawdown']:>9,.0f}  {r['seasons_profitable']}/4")

    half = results["½ Kelly"]
    print(f"\n  Season detail (½ Kelly, start=${STARTING_BANKROLL:,.0f}):")
    print(f"  {'Season':>7}  {'Start':>11}  {'End':>11}  {'Profit':>10}  {'Bets':>5}")
    print(f"  {'-'*48}")
    for yr in SEASONS:
        s = half["by_season"][yr]
        sign = "+" if s["profit"] >= 0 else ""
        print(f"  {yr:>7}  ${s['start']:>10,.0f}  ${s['end']:>10,.0f}  "
              f"{sign}${abs(s['profit']):>9,.0f}  {s['n_bets']:>5}")

    return results


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_priced(bt: list[dict]) -> list[dict]:
    """Games with valid ML odds and model edge."""
    return [
        g for g in bt
        if g.get("season") in SEASONS
        and g.get("actual_winner") in ("home", "away")
        and g.get("home_ml") is not None
        and g.get("away_ml") is not None
        and g.get("model_edge_ml") is not None
        and g.get("home_win_pct") is not None
        and abs(g["home_ml"]) <= 600
    ]


def _load_under_resolved(bt: list[dict]) -> list[dict]:
    """Games with valid totals and UNDER price."""
    return [
        g for g in bt
        if g.get("season") in SEASONS
        and g.get("actual_winner") in ("home", "away")
        and g.get("closing_total") is not None
        and g.get("total_went_over") is not None
        and g.get("under_price") is not None
    ]


# ── LOYO probability estimators ───────────────────────────────────────────────

def _loyo_ml_win_rates(games: list[dict], edge_filter, home_bet: bool) -> dict[int, float]:
    """LOYO win rate for a directional ML bet in [edge_filter] games."""
    target = "home" if home_bet else "away"
    result: dict[int, float] = {}
    for yr in SEASONS:
        pool = [g for g in games if g["season"] != yr and edge_filter(g)]
        result[yr] = (sum(1 for g in pool if g["actual_winner"] == target) / len(pool)
                      if pool else 0.52)
    return result


def _loyo_under_probs(resolved: list[dict], min_t: float, max_t: float) -> dict[int, float]:
    """LOYO UNDER win probability for totals in [min_t, max_t)."""
    result: dict[int, float] = {}
    for yr in SEASONS:
        pool = [g for g in resolved if g["season"] != yr and min_t <= g["closing_total"] < max_t]
        result[yr] = (sum(1 for g in pool if not g["total_went_over"]) / len(pool)
                      if pool else 0.52)
    return result


# ── Bet builders ──────────────────────────────────────────────────────────────

def build_ml_bets(games: list[dict], min_abs_edge: float,
                  home_loyo: dict[int, float], away_loyo: dict[int, float]) -> list[dict]:
    """Directional ML bets using LOYO-calibrated win probabilities."""
    bets = []
    for g in games:
        edge = g["model_edge_ml"]
        if abs(edge) < min_abs_edge:
            continue
        bet_home = edge > 0
        loyo = home_loyo if bet_home else away_loyo
        win_prob = loyo[g["season"]]
        ml = g["home_ml"] if bet_home else g["away_ml"]
        bets.append({
            "win_prob":     win_prob,
            "decimal_odds": _american_to_decimal(ml),
            "won":          g["actual_winner"] == ("home" if bet_home else "away"),
            "season":       g["season"],
        })
    return bets


def build_away_only_bets(games: list[dict], max_edge: float,
                         loyo: dict[int, float]) -> list[dict]:
    """Away-only bets on games where model_edge_ml <= max_edge."""
    bets = []
    for g in games:
        if g["model_edge_ml"] > max_edge:
            continue
        bets.append({
            "win_prob":     loyo[g["season"]],
            "decimal_odds": _american_to_decimal(g["away_ml"]),
            "won":          g["actual_winner"] == "away",
            "season":       g["season"],
        })
    return bets


def build_under_bets(resolved: list[dict], min_t: float, max_t: float,
                     loyo_probs: dict[int, float]) -> list[dict]:
    """UNDER bets on games where closing_total is in [min_t, max_t)."""
    bets = []
    for g in resolved:
        if not (min_t <= g["closing_total"] < max_t):
            continue
        bets.append({
            "win_prob":     loyo_probs[g["season"]],
            "decimal_odds": _american_to_decimal(g["under_price"]),
            "won":          not g["total_went_over"],
            "season":       g["season"],
        })
    return bets


# ── Main ──────────────────────────────────────────────────────────────────────

def run(out: str | None = None) -> None:
    print("\n" + "=" * 74)
    print("  MLB EDGE  —  KELLY CRITERION BANKROLL ANALYSIS")
    print(f"  Starting bankroll: ${STARTING_BANKROLL:,.0f}  |  Flat $100 = {100/STARTING_BANKROLL*100:.1f}% per bet")
    print(f"  Win probs: LOYO-calibrated (leave-one-year-out from backtest outcomes)")
    print("=" * 74)

    raw = json.loads(BACKTEST_PATH.read_text(encoding="utf-8"))
    bt = raw["games"] if isinstance(raw, dict) else raw

    priced   = _load_priced(bt)
    resolved = _load_under_resolved(bt)
    print(f"\n  Priced games (ML analysis):  {len(priced):,}")
    print(f"  Resolved totals (UNDER):     {len(resolved):,}")

    report: dict[str, dict] = {}

    # ── ML edge > 3% (both directions, LOYO calibrated) ──────────────────
    edge_003_fn = lambda g: abs(g["model_edge_ml"]) >= 0.03
    h_loyo_003 = _loyo_ml_win_rates(priced, edge_003_fn, home_bet=True)
    a_loyo_003 = _loyo_ml_win_rates(priced, edge_003_fn, home_bet=False)
    combined_loyo_003 = {yr: (h_loyo_003[yr] + a_loyo_003[yr]) / 2 for yr in SEASONS}
    bets = build_ml_bets(priced, 0.03, h_loyo_003, a_loyo_003)
    report["ml_edge_003"] = _print_filter(
        "ML EDGE ≥ 3%  (any direction, LOYO win rates)", bets, combined_loyo_003)

    # ── Strong away: model_edge_ml <= -0.10 ──────────────────────────────
    away_fn = lambda g: g["model_edge_ml"] <= -0.10
    away_loyo = _loyo_ml_win_rates(priced, away_fn, home_bet=False)
    bets = build_away_only_bets(priced, -0.10, away_loyo)
    report["strong_away"] = _print_filter(
        "STRONG AWAY  (model_edge_ml ≤ -10%, bet away ML)", bets, away_loyo)

    # ── UNDER 8.0–8.5 ────────────────────────────────────────────────────
    loyo_85 = _loyo_under_probs(resolved, 8.0, 8.5)
    bets = build_under_bets(resolved, 8.0, 8.5, loyo_85)
    report["under_8_85"] = _print_filter(
        "UNDER 8.0–8.5  (low-total unders)", bets, loyo_85)

    # ── UNDER 9.0–9.5 ────────────────────────────────────────────────────
    loyo_95 = _loyo_under_probs(resolved, 9.0, 9.5)
    bets = build_under_bets(resolved, 9.0, 9.5, loyo_95)
    report["under_9_95"] = _print_filter(
        "UNDER 9.0–9.5  (mid-total unders)", bets, loyo_95)

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print(f"  SUMMARY  —  ½ Kelly  (start = ${STARTING_BANKROLL:,.0f})")
    print("=" * 74)
    print(f"  {'Filter':<42}  {'Final Bank':>11}  {'ROI':>8}  {'Profitable':>10}")
    print(f"  {'-'*74}")
    for key, label in [
        ("ml_edge_003", "ML edge ≥ 3% (both directions)"),
        ("strong_away", "Strong away (edge ≤ -10%)"),
        ("under_8_85",  "UNDER 8.0–8.5"),
        ("under_9_95",  "UNDER 9.0–9.5"),
    ]:
        r = report[key]["½ Kelly"]
        print(f"  {label:<42}  ${r['final']:>10,.0f}  {r['roi']:>+7.1f}%  {r['seasons_profitable']}/4")

    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n  Report saved to: {out}")

    print("\n" + "=" * 74 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kelly criterion bankroll simulation")
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    args = parser.parse_args()
    run(out=args.out)
