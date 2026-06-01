"""One-shot analysis of docs/backtest.json -- read-only, prints findings."""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def _payout(american: int, stake: float = 1.0) -> float:
    """Net profit on a winning $1 flat-bet at given American odds."""
    if american > 0:
        return stake * american / 100
    return stake * 100 / abs(american)


def _ml_result(game: dict) -> tuple[float, bool] | None:
    """Return (payout_if_won, bet_won) for the model's ML pick, or None if no line."""
    bet_side = game.get("bet_side")
    bet_won  = game.get("bet_won")
    if bet_side is None or bet_won is None:
        return None
    odds = game.get("home_ml") if bet_side == "home" else game.get("away_ml")
    if odds is None:
        return None
    return _payout(odds), bool(bet_won)


def _total_result(game: dict) -> tuple[float, bool] | None:
    """Return (payout_if_won, bet_won) for the totals pick, or None."""
    closing = game.get("closing_total")
    actual  = game.get("actual_total")
    pred    = game.get("predicted_total")
    if closing is None or actual is None or pred is None:
        return None
    bet_over   = pred > closing
    actual_over = actual > closing
    if actual == closing:  # push
        return None
    price = game.get("over_price") if bet_over else game.get("under_price")
    if price is None:
        return None
    won = (bet_over == actual_over)
    return _payout(price), won


def hr(num: float | None, denom: float) -> str:
    if num is None or denom == 0:
        return "  n/a "
    return f"{num/denom:6.1%}"


def signed(v: float) -> str:
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


def main() -> None:
    path = Path(__file__).parent.parent / "docs" / "backtest.json"
    if not path.exists():
        print("docs/backtest.json not found -- run pipeline.backtest first")
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    games = data["games"]
    print(f"\nMLBEdge Backtest Analysis -- {len(games):,} games\n")
    print("=" * 70)

    # ──────────────────────────────────────────────────────────────
    # 1. YEAR-OVER-YEAR
    # ──────────────────────────────────────────────────────────────
    by_year: dict[int, dict] = defaultdict(lambda: {
        "n": 0, "decided": 0, "correct": 0,
        "ml_bets": 0, "ml_units": 0.0, "ml_wins": 0,
        "tot_bets": 0, "tot_units": 0.0, "tot_wins": 0,
        "high_conf": 0, "high_conf_correct": 0,
    })

    for g in games:
        yr = g.get("season") or int(g["date"][:4])
        y  = by_year[yr]
        y["n"] += 1

        aw = g.get("actual_winner")
        if aw and aw not in ("tie", None):
            y["decided"] += 1
            if g.get("correct"):
                y["correct"] += 1

        # High-confidence picks (model very confident either direction)
        hwp = g.get("home_win_pct", 0.5)
        if hwp >= 0.62 or hwp <= 0.38:
            y["high_conf"] += 1
            if g.get("correct"):
                y["high_conf_correct"] += 1

        # ML bet
        res = _ml_result(g)
        if res is not None:
            payout, won = res
            y["ml_bets"] += 1
            y["ml_wins"] += int(won)
            y["ml_units"] += payout if won else -1.0

        # Totals bet
        res = _total_result(g)
        if res is not None:
            payout, won = res
            y["tot_bets"] += 1
            y["tot_wins"] += int(won)
            y["tot_units"] += payout if won else -1.0

    print("\n[1] YEAR-OVER-YEAR PERFORMANCE\n")
    print(f"{'Year':<6} {'Games':>6} {'Win%':>7} {'ML Bets':>8} {'ML ROI%':>9} {'Tot Bets':>9} {'Tot ROI%':>9} {'Hi-Conf Win%':>13}")
    print("-" * 72)
    years_sorted = sorted(by_year.keys())
    for yr in years_sorted:
        y = by_year[yr]
        wr   = y["correct"] / y["decided"] if y["decided"] else 0
        ml_r = y["ml_units"] / y["ml_bets"] * 100 if y["ml_bets"] else 0
        t_r  = y["tot_units"] / y["tot_bets"] * 100 if y["tot_bets"] else 0
        hc_r = y["high_conf_correct"] / y["high_conf"] if y["high_conf"] else 0
        tag  = " *live*" if yr == 2026 else ""
        print(f"{yr:<6} {y['n']:>6,} {wr:>7.1%} {y['ml_bets']:>8,} {ml_r:>+9.1f} {y['tot_bets']:>9,} {t_r:>+9.1f} {hc_r:>12.1%}{tag}")

    totals_ml   = sum(y["ml_units"]  for y in by_year.values())
    totals_mlb  = sum(y["ml_bets"]   for y in by_year.values())
    totals_tot  = sum(y["tot_units"] for y in by_year.values())
    totals_totb = sum(y["tot_bets"]  for y in by_year.values())
    total_correct = sum(y["correct"] for y in by_year.values())
    total_decided = sum(y["decided"] for y in by_year.values())
    total_hc    = sum(y["high_conf"] for y in by_year.values())
    total_hcc   = sum(y["high_conf_correct"] for y in by_year.values())
    print("-" * 72)
    print(f"{'ALL':<6} {sum(y['n'] for y in by_year.values()):>6,} {total_correct/total_decided:>7.1%} "
          f"{totals_mlb:>8,} {totals_ml/totals_mlb*100:>+9.1f} "
          f"{totals_totb:>9,} {totals_tot/totals_totb*100:>+9.1f} "
          f"{total_hcc/total_hc if total_hc else 0:>12.1%}")

    # ──────────────────────────────────────────────────────────────
    # 2. CONFIDENCE TIER BREAKDOWN (with ROI)
    # ──────────────────────────────────────────────────────────────
    buckets = [
        ("<40%",  0.00, 0.40),
        ("40-45%",0.40, 0.45),
        ("45-50%",0.45, 0.50),
        ("50-55%",0.50, 0.55),
        ("55-60%",0.55, 0.60),
        ("60-65%",0.60, 0.65),
        ("65%+",  0.65, 1.01),
    ]
    tier: dict[str, dict] = {b[0]: {"n":0,"correct":0,"ml_bets":0,"ml_units":0.0} for b in buckets}

    for g in games:
        # Normalise: always express as "probability of the predicted team"
        hwp = g.get("home_win_pct", 0.5)
        conf = max(hwp, 1 - hwp)  # always the side the model prefers
        aw = g.get("actual_winner")
        correct = bool(g.get("correct"))

        for label, lo, hi in buckets:
            if lo <= conf < hi:
                tier[label]["n"] += 1
                if aw and aw not in ("tie",):
                    tier[label]["correct"] += 1 if correct else 0
                res = _ml_result(g)
                if res is not None:
                    payout, won = res
                    tier[label]["ml_bets"] += 1
                    tier[label]["ml_units"] += payout if won else -1.0
                break

    print("\n[2] CONFIDENCE TIER BREAKDOWN\n")
    print(f"{'Tier':<10} {'Games':>6} {'Win%':>7} {'ML Bets':>8} {'Units':>8} {'ROI%':>8}")
    print("-" * 52)
    for label, _, __ in buckets:
        t = tier[label]
        if t["n"] == 0:
            continue
        wr = t["correct"] / t["n"]
        roi_s = f"{t['ml_units']/t['ml_bets']*100:+.1f}" if t["ml_bets"] else "  n/a"
        units_s = f"{t['ml_units']:+.1f}" if t["ml_bets"] else "  n/a"
        print(f"{label:<10} {t['n']:>6,} {wr:>7.1%} {t['ml_bets']:>8,} {units_s:>8} {roi_s:>8}")

    # ──────────────────────────────────────────────────────────────
    # 3. HIGH-CONFIDENCE PICK ANATOMY (what drives >=62% picks?)
    # ──────────────────────────────────────────────────────────────
    print("\n[3] HIGH-CONFIDENCE PICKS (model >=62% or <=38%) -- ANATOMY\n")

    hc_games = [g for g in games if max(g.get("home_win_pct", 0.5), 1 - g.get("home_win_pct", 0.5)) >= 0.62]
    print(f"Total: {len(hc_games)} games")

    # Pitcher differential distribution
    diffs = []
    for g in hc_games:
        ph = g.get("pitcher_score_home", 0.5)
        pa = g.get("pitcher_score_away", 0.5)
        # Express as "predicted team's pitcher advantage"
        if g.get("predicted_winner") == "home":
            diffs.append(ph - pa)
        else:
            diffs.append(pa - ph)

    pos_diff = [d for d in diffs if d > 0.05]
    neg_diff = [d for d in diffs if d < -0.05]
    neut_diff= [d for d in diffs if -0.05 <= d <= 0.05]
    print(f"  Pitcher confirms prediction (diff >+0.05): {len(pos_diff)} ({len(pos_diff)/len(diffs):.0%})")
    print(f"  Pitcher neutral (|diff| <=0.05):             {len(neut_diff)} ({len(neut_diff)/len(diffs):.0%})")
    print(f"  Pitcher AGAINST prediction (diff <-0.05):  {len(neg_diff)} ({len(neg_diff)/len(diffs):.0%})")

    # Win rate when pitcher confirms vs. doesn't
    hc_with_pitcher, hc_with_pitcher_correct = 0, 0
    hc_no_pitcher,   hc_no_pitcher_correct   = 0, 0
    for g, d in zip(hc_games, diffs):
        aw = g.get("actual_winner")
        if not aw or aw == "tie":
            continue
        correct = bool(g.get("correct"))
        if d > 0.05:
            hc_with_pitcher += 1
            hc_with_pitcher_correct += int(correct)
        else:
            hc_no_pitcher += 1
            hc_no_pitcher_correct += int(correct)

    if hc_with_pitcher:
        print(f"\n  Win% when pitcher confirms model: {hc_with_pitcher_correct/hc_with_pitcher:.1%} ({hc_with_pitcher} games)")
    if hc_no_pitcher:
        print(f"  Win% when pitcher neutral/contra:  {hc_no_pitcher_correct/hc_no_pitcher:.1%} ({hc_no_pitcher} games)")

    # Year breakdown of high-confidence
    hc_by_year: dict[int, list] = defaultdict(list)
    for g in hc_games:
        yr = g.get("season") or int(g["date"][:4])
        hc_by_year[yr].append(g)
    print("\n  Year breakdown of high-confidence picks:")
    for yr in sorted(hc_by_year):
        gs = hc_by_year[yr]
        correct = sum(1 for g in gs if g.get("correct"))
        decided = sum(1 for g in gs if g.get("actual_winner") not in (None, "tie"))
        tag = " *live*" if yr == 2026 else ""
        print(f"    {yr}: {len(gs):4d} games, {correct/decided:.1%} win rate{tag}" if decided else f"    {yr}: {len(gs):4d} games, n/a{tag}")

    # ──────────────────────────────────────────────────────────────
    # 4. TOTALS MODEL ANALYSIS
    # ──────────────────────────────────────────────────────────────
    print("\n[4] TOTALS MODEL ANALYSIS\n")

    totals_games = [g for g in games if g.get("closing_total") and g.get("actual_total") is not None and g.get("predicted_total")]
    print(f"Games with all totals data: {len(totals_games)}")

    over_bias_n = 0
    over_correct = 0
    under_correct = 0

    # MAE and directional accuracy by year
    yr_totals: dict[int, dict] = defaultdict(lambda: {"n":0,"mae":0.0,"over_n":0,"over_correct":0,"under_n":0,"under_correct":0,"bias":0.0})
    for g in totals_games:
        closing = g["closing_total"]
        actual  = g["actual_total"]
        pred    = g["predicted_total"]
        yr = g.get("season") or int(g["date"][:4])
        t  = yr_totals[yr]

        error = pred - actual
        t["n"]    += 1
        t["mae"]  += abs(error)
        t["bias"] += error  # positive = we over-predicted runs

        if actual == closing:
            continue  # push
        if pred > closing:
            t["over_n"] += 1
            t["over_correct"] += int(actual > closing)
        else:
            t["under_n"] += 1
            t["under_correct"] += int(actual <= closing)

    print(f"\n{'Year':<6} {'Games':>6} {'MAE':>6} {'Bias':>7} {'Over Bets':>10} {'Over Acc':>9} {'Under Bets':>11} {'Under Acc':>10}")
    print("-" * 70)
    for yr in sorted(yr_totals.keys()):
        t = yr_totals[yr]
        if t["n"] == 0:
            continue
        mae  = t["mae"] / t["n"]
        bias = t["bias"] / t["n"]
        o_acc = t["over_correct"]  / t["over_n"]  if t["over_n"]  else 0
        u_acc = t["under_correct"] / t["under_n"] if t["under_n"] else 0
        tag = " *" if yr == 2026 else ""
        print(f"{yr:<6} {t['n']:>6,} {mae:>6.2f} {bias:>+7.2f} {t['over_n']:>10,} {o_acc:>9.1%} {t['under_n']:>11,} {u_acc:>10.1%}{tag}")

    # ──────────────────────────────────────────────────────────────
    # 5. PITCHER SIGNAL VALIDATION
    # ──────────────────────────────────────────────────────────────
    print("\n[5] PITCHER SIGNAL VALIDATION\n")

    diff_tiers = [
        ("Strong Away SP  (<-0.10)", lambda d: d < -0.10),
        ("Moderate Away   (-0.10--0.05)", lambda d: -0.10 <= d < -0.05),
        ("Neutral         (|diff|<0.05)", lambda d: abs(d) < 0.05),
        ("Moderate Home   (0.05-0.10)",   lambda d: 0.05 <= d < 0.10),
        ("Strong Home SP  (>0.10)",        lambda d: d >= 0.10),
    ]

    diff_buckets: dict[str, dict] = {b[0]: {"n":0,"correct":0,"ml_bets":0,"ml_units":0.0} for b in diff_tiers}

    for g in games:
        ph = g.get("pitcher_score_home", 0.5)
        pa = g.get("pitcher_score_away", 0.5)
        d  = ph - pa  # positive = home pitcher stronger

        aw = g.get("actual_winner")
        correct = bool(g.get("correct"))

        for label, cond in diff_tiers:
            if cond(d):
                diff_buckets[label]["n"] += 1
                if aw and aw not in ("tie",):
                    diff_buckets[label]["correct"] += int(correct)
                res = _ml_result(g)
                if res is not None:
                    payout, won = res
                    diff_buckets[label]["ml_bets"] += 1
                    diff_buckets[label]["ml_units"] += payout if won else -1.0
                break

    print(f"{'Bucket':<36} {'Games':>6} {'Win%':>7} {'ML Bets':>8} {'Units':>8} {'ROI%':>8}")
    print("-" * 76)
    for label, _ in diff_tiers:
        b = diff_buckets[label]
        if b["n"] == 0:
            continue
        wr = b["correct"] / b["n"]
        roi_s = f"{b['ml_units']/b['ml_bets']*100:+.1f}" if b["ml_bets"] else "  n/a"
        u_s   = f"{b['ml_units']:+.1f}" if b["ml_bets"] else "  n/a"
        print(f"{label:<36} {b['n']:>6,} {wr:>7.1%} {b['ml_bets']:>8,} {u_s:>8} {roi_s:>8}")

    # ──────────────────────────────────────────────────────────────
    # 6. MODEL EDGE vs. ACTUAL WIN RATE (calibration summary)
    # ──────────────────────────────────────────────────────────────
    print("\n[6] VEGAS EDGE CALIBRATION (does model edge predict ML bet wins?)\n")

    edge_tiers = [
        ("Strong Fade (edge <=-10%)", lambda e: e <= -0.10),
        ("Moderate Fade (-10%--5%)", lambda e: -0.10 < e <= -0.05),
        ("Slight Fade (-5%-0%)",     lambda e: -0.05 < e < 0),
        ("Slight Edge (0%-5%)",      lambda e: 0 <= e < 0.05),
        ("Moderate Edge (5%-10%)",   lambda e: 0.05 <= e < 0.10),
        ("Strong Edge (>=10%)",        lambda e: e >= 0.10),
    ]

    edge_bkts: dict[str, dict] = {b[0]: {"n":0,"wins":0,"ml_bets":0,"ml_units":0.0} for b in edge_tiers}

    games_with_edge = [g for g in games if g.get("model_edge_ml") is not None and g.get("bet_side")]
    for g in games_with_edge:
        edge = g["model_edge_ml"]
        won  = bool(g.get("bet_won"))
        res  = _ml_result(g)
        for label, cond in edge_tiers:
            if cond(edge):
                edge_bkts[label]["n"] += 1
                edge_bkts[label]["wins"] += int(won)
                if res is not None:
                    payout, w = res
                    edge_bkts[label]["ml_bets"] += 1
                    edge_bkts[label]["ml_units"] += payout if w else -1.0
                break

    print(f"{'Edge Bucket':<30} {'Bets':>6} {'Win%':>7} {'Units':>8} {'ROI%':>8}")
    print("-" * 62)
    for label, _ in edge_tiers:
        b = edge_bkts[label]
        if b["n"] == 0:
            continue
        wr = b["wins"] / b["n"]
        roi_s = f"{b['ml_units']/b['ml_bets']*100:+.1f}" if b["ml_bets"] else "  n/a"
        u_s   = f"{b['ml_units']:+.1f}" if b["ml_bets"] else "  n/a"
        print(f"{label:<30} {b['n']:>6,} {wr:>7.1%} {u_s:>8} {roi_s:>8}")

    # ──────────────────────────────────────────────────────────────
    # 7. AWAY EDGE TIER x PITCHER ADVANTAGE (the new breakdown)
    # ──────────────────────────────────────────────────────────────
    print("\n[7] AWAY EDGE TIERS x PITCHER ADVANTAGE\n")
    print("model_edge_ml < 0 means model favors away team over Vegas line.")
    print("pitcher_score_diff < -0.05 means away SP is meaningfully better.\n")

    away_tiers = [
        ("0% to -3%",   lambda e: -0.03 <= e < 0),
        ("-3% to -6%",  lambda e: -0.06 <= e < -0.03),
        ("-6% to -10%", lambda e: -0.10 <= e < -0.06),
        ("-10%+",       lambda e: e < -0.10),
    ]

    # pitcher_score_diff = home - away; < -0.05 = away pitcher advantage
    def away_pitcher_adv(g: dict) -> bool:
        ph = g.get("pitcher_score_home", 0.5)
        pa = g.get("pitcher_score_away", 0.5)
        return (ph - pa) < -0.05

    # Build cells: away_tier x pitcher_adv
    cells: dict[tuple, dict] = {}
    for tier_label, _ in away_tiers:
        for pitcher_label in ("SP Adv", "No SP Adv"):
            cells[(tier_label, pitcher_label)] = {"n": 0, "wins": 0, "ml_bets": 0, "ml_units": 0.0}

    away_games = [g for g in games if g.get("model_edge_ml") is not None and g["model_edge_ml"] < 0 and g.get("bet_side")]
    for g in away_games:
        edge = g["model_edge_ml"]
        sp_adv = away_pitcher_adv(g)
        pitcher_label = "SP Adv" if sp_adv else "No SP Adv"
        for tier_label, cond in away_tiers:
            if cond(edge):
                c = cells[(tier_label, pitcher_label)]
                c["n"] += 1
                c["wins"] += int(bool(g.get("bet_won")))
                res = _ml_result(g)
                if res is not None:
                    payout, won = res
                    c["ml_bets"] += 1
                    c["ml_units"] += payout if won else -1.0
                break

    # Print: two sub-tables side by side
    print(f"{'Away Edge Tier':<14}  {'--- WITH Away SP Advantage ---':^40}  {'--- WITHOUT Away SP Advantage ---':^40}")
    print(f"{'':14}  {'Bets':>5} {'Win%':>6} {'Units':>7} {'ROI%':>7}  {'Bets':>5} {'Win%':>6} {'Units':>7} {'ROI%':>7}")
    print("-" * 96)

    for tier_label, _ in away_tiers:
        a = cells[(tier_label, "SP Adv")]
        b = cells[(tier_label, "No SP Adv")]

        def fmt_cell(c: dict) -> str:
            if c["ml_bets"] == 0:
                return f"{'n/a':>5} {'n/a':>6} {'n/a':>7} {'n/a':>7}"
            wr  = c["wins"] / c["ml_bets"]
            roi = c["ml_units"] / c["ml_bets"] * 100
            u   = c["ml_units"]
            return f"{c['ml_bets']:>5} {wr:>6.1%} {u:>+7.1f} {roi:>+7.1f}"

        print(f"{tier_label:<14}  {fmt_cell(a)}  {fmt_cell(b)}")

    # Row totals per tier
    print("-" * 96)
    print(f"\n{'Away Edge Tier':<14}  {'--- ALL GAMES (SP adv + no adv) ---':^40}")
    print(f"{'':14}  {'Bets':>5} {'Win%':>6} {'Units':>7} {'ROI%':>7}")
    print("-" * 48)
    for tier_label, _ in away_tiers:
        tot = {"n": 0, "wins": 0, "ml_bets": 0, "ml_units": 0.0}
        for pk in ("SP Adv", "No SP Adv"):
            c = cells[(tier_label, pk)]
            tot["n"]       += c["n"]
            tot["wins"]    += c["wins"]
            tot["ml_bets"] += c["ml_bets"]
            tot["ml_units"]+= c["ml_units"]
        if tot["ml_bets"] == 0:
            print(f"{tier_label:<14}  {'n/a':>5} {'n/a':>6} {'n/a':>7} {'n/a':>7}")
        else:
            wr  = tot["wins"] / tot["ml_bets"]
            roi = tot["ml_units"] / tot["ml_bets"] * 100
            print(f"{tier_label:<14}  {tot['ml_bets']:>5} {wr:>6.1%} {tot['ml_units']:>+7.1f} {roi:>+7.1f}")

    print("\n" + "=" * 70)
    print("Analysis complete.\n")


if __name__ == "__main__":
    main()
