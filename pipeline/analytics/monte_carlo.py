"""Monte Carlo game simulator — Statcast-pure win probability and run total estimation.

This runs independently of the Vegas-anchored primary model (predictor.py).
The two are NOT expected to agree: the primary model starts from the market's
consensus probability; this simulation starts from raw Statcast only. A large
gap between them means the market has information (injuries, sharp money, etc.)
that the simulation cannot see. Show both; flag large divergence.

Improvements over test_mc4.py:
  1. Unified K%/BB% blend (always-on, not just when misaligned) — bridges
     barrel%, Stuff+, whiff%, chase%, xERA into plate-appearance math via
     pitcher_score composite.
  2. Platoon splits — batter BABIP/HR multiplier uses xwoba_vs_l / xwoba_vs_r
     when opponent SP handedness is known.
  3. SP→BP threshold raised to median 27 PA (range 22–32) to match real starter
     average of ~5.5 IP.
"""
from __future__ import annotations

import logging
import math
import random
from typing import Optional

log = logging.getLogger(__name__)

# ── League-average constants ──────────────────────────────────────────────────
_LC = dict(
    k_pct          = 0.224,
    bb_pct         = 0.084,
    pitcher_k_pct  = 0.224,
    pitcher_bb_pct = 0.084,
    babip          = 0.299,
    single_of_hit  = 0.535,
    double_of_hit  = 0.285,
    triple_of_hit  = 0.018,
    xwoba          = 0.318,
)

# Alpha for K%/BB% blend toward pitcher_score-implied values.
# 0.50 = 50% observed season K% + 50% what pitcher_score implies.
# Increasing this makes the MC respect the composite scorer more.
_BLEND_ALPHA = 0.50

# BABIP alpha: how much pitcher_score shifts baseline BABIP.
# 0.3 → elite pitcher (ps=0.80) → BABIP mult = 1 + 0.3*(0.5-0.80) = 0.91
_BABIP_ALPHA = 0.30


# ── Math helpers ──────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _log5(b: float, p: float, L: float) -> float:
    """Bill James log5: combine batter rate, pitcher rate, league average."""
    num = b * p / L
    den = num + (1 - b) * (1 - p) / (1 - L)
    return _clamp(num / den, 0.01, 0.98)


def _randn(mu: float, sd: float) -> float:
    import math as _m
    u1 = max(random.random(), 1e-10)
    z = _m.sqrt(-2 * _m.log(u1)) * _m.cos(2 * _m.pi * random.random())
    return mu + z * sd


# ── Rate blending & multipliers ───────────────────────────────────────────────

def _blend_pitcher_stats(
    k_raw: float,
    bb_raw: float,
    pitcher_score: Optional[float],
    alpha: float = _BLEND_ALPHA,
) -> tuple[float, float]:
    """Always-on blend: pull K%/BB% toward what pitcher_score implies.

    pitcher_score encodes barrel% against, Stuff+, whiff%, chase%, xERA, etc.
    By blending, those 8 dimensions enter the plate-appearance simulation
    even though the MC only directly models K%/BB%.

    alpha=0.50: equal weight to observed K% and composite-implied K%.
    """
    if pitcher_score is None:
        return k_raw, bb_raw
    ps = pitcher_score
    implied_k  = _LC['pitcher_k_pct']  * (ps / 0.5)
    implied_bb = _LC['pitcher_bb_pct'] * (0.5 / max(ps, 0.15))
    eff_k  = k_raw  * (1 - alpha) + implied_k  * alpha
    eff_bb = bb_raw * (1 - alpha) + implied_bb * alpha
    return _clamp(eff_k, 0.08, 0.42), _clamp(eff_bb, 0.02, 0.18)


def _hr_rate(batter: dict, park_factor: float) -> float:
    pf = park_factor / 100
    # Dynamic cap: 118 → 0.078, 100 → 0.066, 82 → 0.054
    hr_cap = min(0.09, 0.066 * pf)
    if batter.get('barrel_pct') is not None:
        return _clamp(batter['barrel_pct'] * 0.25 * pf, 0.005, hr_cap)
    if batter.get('hard_hit_pct') is not None:
        return _clamp(batter['hard_hit_pct'] * 0.06 * pf, 0.005, hr_cap)
    return 0.032 * pf


def _batt_mult(batter: dict, sp_throws: Optional[str] = None) -> float:
    """BABIP/contact multiplier for a batter; uses platoon split when available."""
    if sp_throws in ('L', 'R'):
        suffix = f"xwoba_vs_{sp_throws.lower()}"
        xwoba = batter.get(suffix) or batter.get('xwoba')
    else:
        xwoba = batter.get('xwoba')
    if xwoba is None:
        return 1.0
    return _clamp(1 + 0.4 * (xwoba / _LC['xwoba'] - 1), 0.80, 1.20)


def _babip_mult(pitcher_score: Optional[float], alpha: float = _BABIP_ALPHA) -> float:
    if pitcher_score is None:
        return 1.0
    return _clamp(1 + alpha * (0.5 - pitcher_score), 0.80, 1.20)


def _bp_xera_mult(xera: Optional[float]) -> float:
    if xera is None:
        return 1.0
    return _clamp(1 + 0.3 * (xera - 4.15) / 4.15, 0.80, 1.20)


# ── Base-state advance table ──────────────────────────────────────────────────

def _advance(state: int, outs: int, outcome: str) -> tuple[int, int, int]:
    """Update runner state (bitmask 1B=bit0, 2B=bit1, 3B=bit2), outs, and runs scored."""
    if outcome in ('K', 'OUT'):
        return state, outs + 1, 0
    b1 = bool(state & 1); b2 = bool(state & 2); b3 = bool(state & 4)
    if outcome == 'HR':
        return 0, outs, 1 + b1 + b2 + b3
    if outcome == 'BB':
        if b1:
            if b2:
                if b3: return state, outs, 1
                return (state | 4), outs, 0
            return (state | 2), outs, 0
        return (state | 1), outs, 0
    if outcome == 'S':
        return ((b2 << 2) | (b1 << 1) | 1), outs, int(b3)
    if outcome == 'D':
        return ((b1 << 2) | 2), outs, int(b3) + int(b2)
    if outcome == 'T':
        return 4, outs, int(b3) + int(b2) + int(b1)
    return state, outs + 1, 0


# ── Half-inning simulation ────────────────────────────────────────────────────

def _half_inning(
    lineup: list[dict],
    pos: int,
    pitcher: dict,
    park_factor: float,
    sp_throws: Optional[str] = None,
) -> tuple[int, int]:
    """Simulate one half-inning. Returns (runs_scored, new_lineup_pos)."""
    outs = runs = state = 0
    while outs < 3:
        b = lineup[pos % len(lineup)]
        pos += 1

        bK  = _clamp((b.get('k_pct')  or _LC['k_pct'])  * b['_noise'], 0.05, 0.55)
        bBB = _clamp((b.get('bb_pct') or _LC['bb_pct'])  * b['_noise'], 0.02, 0.30)
        pK  = _clamp(pitcher['_eff_k']  * pitcher['_noise'], 0.05, 0.55)
        pBB = _clamp(pitcher['_eff_bb'] * pitcher['_noise'], 0.02, 0.30)

        p_so  = _log5(bK, pK, _LC['k_pct'])
        p_bb  = _log5(bBB, pBB, _LC['bb_pct'])
        hr_r  = _hr_rate(b, park_factor) * pitcher['_noise']

        pm = pitcher.get('_babip_mult', 1.0)
        bm = _batt_mult(b, sp_throws)
        # Park BABIP boost: Coors (118) → +4.5%; pitchers' parks (82) → -4.5%
        park_babip_mult = 1 + (park_factor - 100) / 200
        eff_babip = _LC['babip'] * pm * bm * park_babip_mult

        roll = random.random()
        if roll < p_so:
            outcome = 'K'
        elif roll < p_so + p_bb:
            outcome = 'BB'
        else:
            bip = random.random()
            hr_thresh  = hr_r / (1 - p_so - p_bb + 0.001)
            hit_thresh = hr_thresh + eff_babip * (1 - hr_thresh)
            if bip < hr_thresh:
                outcome = 'HR'
            elif bip < hit_thresh:
                r2 = random.random()
                if   r2 < _LC['single_of_hit']:
                    outcome = 'S'
                elif r2 < _LC['single_of_hit'] + _LC['double_of_hit']:
                    outcome = 'D'
                elif r2 < _LC['single_of_hit'] + _LC['double_of_hit'] + _LC['triple_of_hit']:
                    outcome = 'T'
                else:
                    outcome = 'S'
            else:
                outcome = 'OUT'

        state, outs, r = _advance(state, outs, outcome)
        runs += r

    return runs, pos


# ── Pitcher builder ───────────────────────────────────────────────────────────

def _make_pitcher(
    k_raw: Optional[float],
    bb_raw: Optional[float],
    pitcher_score: Optional[float],
) -> dict:
    k  = k_raw  or _LC['pitcher_k_pct']
    bb = bb_raw or _LC['pitcher_bb_pct']
    eff_k, eff_bb = _blend_pitcher_stats(k, bb, pitcher_score)
    return {
        '_eff_k':      eff_k,
        '_eff_bb':     eff_bb,
        '_babip_mult': _babip_mult(pitcher_score),
    }


# ── Main simulation entry point ───────────────────────────────────────────────

def simulate_game(g: dict, n: int = 5000, seed: Optional[int] = None) -> dict:
    """Run n-iteration MC simulation for game g.

    Args:
        g:    Game dict from games.json (has home_sp, away_sp, home_lineup,
              away_lineup, prediction.model_signals, park_run_factor).
        n:    Iterations. 5000 ≈ 0.3 s; use 25000 for research.
        seed: Optional RNG seed for reproducibility.

    Returns dict with keys:
        mc_win_pct    — home win probability (adjusted for weather/rest)
        mc_home_runs  — expected home runs per game
        mc_away_runs  — expected away runs per game
        mc_total      — expected combined runs per game
        mc_n          — iterations run
    """
    if seed is not None:
        random.seed(seed)

    park = g.get('park_run_factor', 100)
    sig  = (g.get('prediction') or {}).get('model_signals') or {}
    hs   = g.get('home_sp') or {}
    aws  = g.get('away_sp') or {}

    # SP handedness for platoon splits
    hs_throws  = sig.get('sp_throws_home') or hs.get('throws')   # home SP throws
    aws_throws = sig.get('sp_throws_away') or aws.get('throws')  # away SP throws

    # Build lineups — fall back to league average placeholder if data is thin
    _league_batter = {'xwoba': _LC['xwoba'], 'k_pct': _LC['k_pct'], 'bb_pct': _LC['bb_pct']}
    hl = [b for b in (g.get('home_lineup') or []) if b.get('xwoba') or b.get('hard_hit_pct')]
    al = [b for b in (g.get('away_lineup') or []) if b.get('xwoba') or b.get('hard_hit_pct')]
    if len(hl) < 3: hl = [_league_batter] * 9
    if len(al) < 3: al = [_league_batter] * 9

    ps_h = sig.get('pitcher_score_home')
    ps_a = sig.get('pitcher_score_away')

    # Starters
    sp_h = _make_pitcher(
        hs.get('season', {}).get('k_pct'),
        hs.get('season', {}).get('bb_pct'),
        ps_h,
    )
    sp_a = _make_pitcher(
        aws.get('season', {}).get('k_pct'),
        aws.get('season', {}).get('bb_pct'),
        ps_a,
    )

    # Bullpens — use signal data when available, fall back to SP-derived estimates
    bp_h = {
        '_eff_k':      sig.get('bullpen_k_pct_home') or sp_h['_eff_k'] * 0.9,
        '_eff_bb':     sig.get('bullpen_bb_pct_home') or sp_h['_eff_bb'] * 1.1,
        '_babip_mult': _bp_xera_mult(sig.get('bullpen_xera_home')),
    }
    bp_a = {
        '_eff_k':      sig.get('bullpen_k_pct_away') or sp_a['_eff_k'] * 0.9,
        '_eff_bb':     sig.get('bullpen_bb_pct_away') or sp_a['_eff_bb'] * 1.1,
        '_babip_mult': _bp_xera_mult(sig.get('bullpen_xera_away')),
    }

    home_wins  = 0
    home_runs_list: list[float] = []
    away_runs_list: list[float] = []

    for _ in range(n):
        # SP→BP threshold: total_pa increments by 6/inning (3 per half-inning).
        # Median 30 → SP pitches 5 innings (modern MLB avg ~5.1 IP). Range 4-6 innings.
        sp_thresh = round(30 + (random.random() - 0.5) * 12)

        # Per-iteration noise injection
        hl2 = [{**b, '_noise': _clamp(_randn(1, 0.15), 0.5, 1.5)} for b in hl]
        al2 = [{**b, '_noise': _clamp(_randn(1, 0.15), 0.5, 1.5)} for b in al]
        sp_h2 = {**sp_h, '_noise': _clamp(_randn(1, 0.20), 0.5, 1.6)}
        sp_a2 = {**sp_a, '_noise': _clamp(_randn(1, 0.20), 0.5, 1.6)}
        bp_h2 = {**bp_h, '_noise': _clamp(_randn(1, 0.20), 0.5, 1.6)}
        bp_a2 = {**bp_a, '_noise': _clamp(_randn(1, 0.20), 0.5, 1.6)}

        h_runs = a_runs = pos_h = pos_a = total_pa = 0

        for _inn in range(9):
            # Away team bats: faces home SP (sp_h / bp_h); platoon = home SP throws
            pitcher_vs_away = sp_h2 if total_pa < sp_thresh else bp_h2
            r, pos_a = _half_inning(al2, pos_a, pitcher_vs_away, park, sp_throws=hs_throws)
            a_runs += r
            total_pa += 3  # increment after each half-inning

            # Home team bats: faces away SP (sp_a / bp_a); platoon = away SP throws
            pitcher_vs_home = sp_a2 if total_pa < sp_thresh else bp_a2
            r, pos_h = _half_inning(hl2, pos_h, pitcher_vs_home, park, sp_throws=aws_throws)
            h_runs += r
            total_pa += 3  # increment after each half-inning

        if h_runs > a_runs:
            home_wins += 1
        home_runs_list.append(h_runs)
        away_runs_list.append(a_runs)

    raw_win_pct = home_wins / n

    # Small post-hoc adjustments for weather and rest (same weights as primary model)
    weather_adj = (sig.get('weather_modifier') or 0) * 0.2 * 0.25
    rest_adj    = (sig.get('rest_modifier')    or 0) * 0.25
    mc_win_pct  = _clamp(raw_win_pct + weather_adj + rest_adj, 0.05, 0.95)

    avg_h = sum(home_runs_list) / n
    avg_a = sum(away_runs_list) / n

    return {
        'mc_win_pct':   round(mc_win_pct, 4),
        'mc_home_runs': round(avg_h, 2),
        'mc_away_runs': round(avg_a, 2),
        'mc_total':     round(avg_h + avg_a, 2),
        'mc_n':         n,
    }
