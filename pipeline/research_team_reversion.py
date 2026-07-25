"""Team-level mean-reversion research — does a "both offenses running hot" signal improve the
UNDER 8.0 / 9.0 edges?

Hypothesis: a team scoring above its OWN recent baseline (hot bats) is due to regress down, so a
game where BOTH offenses are hot should go UNDER more often than the market prices. We test
whether that isolates a higher-ROI, robust subset of the validated 8.0/9.0 UNDER bets.

Signal (disciplined two-metric core, LOOKAHEAD-SAFE — every window excludes the current game):
  ops_hot  = team OPS over trailing 5 games  − trailing 20 games   (hot vs its own norm)
  runs_hot = team runs/g over trailing 10    − season-to-date       (own baseline, not league avg)
  rev_score(game) = z(mean of both offenses' ops_hot) + z(mean of both offenses' runs_hot)

Two-step read: (1) broad — does high rev_score predict UNDER on ALL totals? (2) overlay — split
the 8.0 std-vig / 9.0 UNDER universes by rev_score, season by season, push-corrected.

Run:  python -m pipeline.research_team_reversion
Dev-only; reads local parquets (2021-2025). 2026 has no player_game_logs (live).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SEASONS = [2021, 2022, 2023, 2024, 2025]
VIG_LO, VIG_HI = -110, -106          # 8.0 std-vig window (the only +EV price zone)
MIN_PRIOR = 20                        # need this many prior team-games before metrics are stable


# ── Team-game table: runs + OPS per team per game ─────────────────────────────
def _team_game_ops(season: int) -> dict:
    """(game_pk, side) -> team OPS, aggregated from batter game logs."""
    logs = pd.read_parquet(ROOT / f"data/seasons/{season}/player_game_logs.parquet")
    bat = logs[(~logs["is_pitcher"]) & (logs["AB"] > 0)]
    tg = bat.groupby(["game_pk", "side"]).agg(
        H=("H", "sum"), TB=("TB", "sum"), BB=("B_BB", "sum"), AB=("AB", "sum")
    ).reset_index()
    obp = (tg["H"] + tg["BB"]) / (tg["AB"] + tg["BB"])
    slg = tg["TB"] / tg["AB"]
    tg["OPS"] = obp + slg
    return {(int(r.game_pk), r.side): float(r.OPS) for r in tg.itertuples()}


def _team_games(season: int) -> pd.DataFrame:
    """One row per team-game: game_pk, date, team, side, runs, ops, season."""
    g = pd.read_parquet(ROOT / f"data/seasons/{season}/games.parquet").drop_duplicates("game_pk")
    ops = _team_game_ops(season)
    rows = []
    for r in g.itertuples():
        rows.append(dict(game_pk=int(r.game_pk), date=str(r.date), team=r.home_team, side="home",
                         runs=r.home_score, ops=ops.get((int(r.game_pk), "home"))))
        rows.append(dict(game_pk=int(r.game_pk), date=str(r.date), team=r.away_team, side="away",
                         runs=r.away_score, ops=ops.get((int(r.game_pk), "away"))))
    df = pd.DataFrame(rows)
    df["season"] = season
    return df


def _add_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Trailing metrics ENTERING each game (shift(1) => current game excluded = no lookahead)."""
    parts = []
    for _team, grp in df.groupby("team"):
        grp = grp.sort_values(["date", "game_pk"]).copy()
        prior_ops = grp["ops"].shift(1)
        grp["ops_l5"] = prior_ops.rolling(5, min_periods=4).mean()
        grp["ops_l20"] = prior_ops.rolling(20, min_periods=12).mean()
        grp["ops_hot"] = grp["ops_l5"] - grp["ops_l20"]
        prior_runs = grp["runs"].shift(1)
        grp["runs_l10"] = prior_runs.rolling(10, min_periods=6).mean()
        grp["runs_std"] = prior_runs.expanding(min_periods=MIN_PRIOR).mean()   # season-to-date
        grp["runs_hot"] = grp["runs_l10"] - grp["runs_std"]
        grp["n_prior"] = np.arange(len(grp))
        parts.append(grp)
    return pd.concat(parts, ignore_index=True)


# ── Assemble game-level signal + join lines ───────────────────────────────────
def build() -> pd.DataFrame:
    tg = pd.concat([_add_rolling(_team_games(s)) for s in SEASONS], ignore_index=True)
    keep = ["game_pk", "ops_hot", "runs_hot", "n_prior"]
    home = tg[tg.side == "home"][keep].rename(columns={c: "h_" + c for c in keep[1:]})
    away = tg[tg.side == "away"][keep].rename(columns={c: "a_" + c for c in keep[1:]})
    game = home.merge(away, on="game_pk")
    game["game_ops_hot"] = (game["h_ops_hot"] + game["a_ops_hot"]) / 2
    game["game_runs_hot"] = (game["h_runs_hot"] + game["a_runs_hot"]) / 2
    game = game[(game["h_n_prior"] >= MIN_PRIOR) & (game["a_n_prior"] >= MIN_PRIOR)]
    game = game.dropna(subset=["game_ops_hot", "game_runs_hot"])

    # z-score each component across the full sample, sum -> rev_score (higher = both hotter)
    for col in ["game_ops_hot", "game_runs_hot"]:
        game["z_" + col.split("_")[1]] = (game[col] - game[col].mean()) / game[col].std()
    game["rev_score"] = game["z_ops"] + game["z_runs"]

    # join closing lines (all seasons)
    lines = pd.concat([
        pd.read_parquet(ROOT / f"data/seasons/{s}/closing_lines.parquet").assign(season=s)
        for s in SEASONS
    ], ignore_index=True).drop_duplicates("game_pk")
    lines["game_pk"] = lines["game_pk"].astype(int)
    lines["actual_total"] = lines["home_score"] + lines["away_score"]
    out = game.merge(
        lines[["game_pk", "season", "closing_total", "under_price", "actual_total"]],
        on="game_pk", how="inner",
    )
    return out


# ── ROI helpers (push-void UNDER) ─────────────────────────────────────────────
def _under_roi(rows: pd.DataFrame) -> tuple[int, float | None, float | None]:
    n = w = 0
    u = 0.0
    for r in rows.itertuples():
        ct, up, at = r.closing_total, r.under_price, r.actual_total
        if pd.isna(ct) or pd.isna(up) or pd.isna(at) or abs(at - ct) < 1e-9:
            continue
        won = at < ct
        dec = 1 + up / 100 if up >= 0 else 1 - 100 / up
        n += 1
        if won:
            w += 1
            u += dec - 1
        else:
            u -= 1
    return n, (100 * w / n if n else None), (100 * u / n if n else None)


def _bucket_report(df: pd.DataFrame, label: str) -> None:
    """Split by rev_score tercile; UNDER ROI overall + season by season."""
    if df.empty:
        print(f"\n{label}: no games")
        return
    q1, q2 = df["rev_score"].quantile([1 / 3, 2 / 3])
    def bkt(s):
        return "HOT (both offenses ↑)" if s >= q2 else ("cold" if s <= q1 else "mid")
    df = df.assign(bucket=df["rev_score"].map(bkt))
    print(f"\n=== {label} — UNDER ROI by reversion bucket ===")
    print(f"{'bucket':<24}{'n':>6}{'win%':>8}{'ROI':>9}   by season (ROI%)")
    for b in ["HOT (both offenses ↑)", "mid", "cold"]:
        sub = df[df.bucket == b]
        n, wr, roi = _under_roi(sub)
        seas = []
        for s in SEASONS:
            sn, _, sr = _under_roi(sub[sub.season == s])
            seas.append(f"{s}:{('%+.0f' % sr) if sr is not None else '-'}({sn})")
        print(f"{b:<24}{n:>6}{('%.1f' % wr) if wr else '-':>8}"
              f"{('%+.1f%%' % roi) if roi is not None else '-':>9}   {'  '.join(seas)}")


def main() -> None:
    df = build()
    print(f"games with signal + line: {len(df)}  (2021–2025)")
    print(f"league team-game OPS check via runs: mean actual_total/2 = "
          f"{df['actual_total'].mean() / 2:.2f} runs/team/g")

    # (1) BROAD: does high reversion predict UNDER across ALL totals?
    _bucket_report(df, "ALL totals (broad signal check)")

    # (2) OVERLAY: the validated edges
    e80 = df[(df.closing_total >= 8.0) & (df.closing_total < 8.5)
             & (df.under_price >= VIG_LO) & (df.under_price <= VIG_HI)]
    e90 = df[(df.closing_total >= 9.0) & (df.closing_total < 9.5)]
    _bucket_report(e80, "UNDER 8.0 (std-vig)")
    _bucket_report(e90, "UNDER 9.0")

    # base rates for comparison
    print("\n=== base UNDER ROI (no reversion filter) ===")
    for lbl, sub in [("8.0 std-vig", e80), ("9.0", e90)]:
        n, wr, roi = _under_roi(sub)
        print(f"  {lbl:<12} n={n}  win={wr:.1f}%  ROI={roi:+.1f}%" if roi is not None else f"  {lbl}: n={n}")


if __name__ == "__main__":
    main()
