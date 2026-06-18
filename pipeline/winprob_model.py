"""Fitted, data-driven win-probability model (curated Statcast logistic).

Single source of truth for feature extraction (shared by trainer and live predictor) plus a
dependency-free predict (no sklearn at runtime — loads coefficients from data/winprob_model.json).

The model is the model's OWN input-based win-prob (no odds). It backs the no-odds fallback in
predictor._win_probability and is surfaced in the UI as a transparent "model win-prob" alongside
the Vegas-anchored number. Validated OOS at AUC ~0.596 (research_winprob_features.py); the curated
15-feature set is the AUC/calibration sweet spot vs the full 51.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
MODEL_PATH = ROOT / "data" / "winprob_model.json"

# Feature order is the contract between trainer and predictor — do not reorder without retraining.
CURATED_FEATURES = [
    "off_h_xwoba", "off_a_xwoba", "off_h_wrc_plus", "off_a_wrc_plus",
    "sp_h_xera", "sp_a_xera", "sp_h_xfip", "sp_a_xfip", "sp_h_k_pct", "sp_a_k_pct",
    "bp_h_xera", "bp_a_xera", "bp_h_k_pct", "bp_a_k_pct", "park",
]


def _agg(players: list[dict], field: str) -> Optional[float]:
    vals = [p[field] for p in (players or []) if p.get(field) is not None]
    return sum(vals) / len(vals) if vals else None


def extract_features(
    home_players: list[dict], away_players: list[dict],
    home_sp: Optional[dict], away_sp: Optional[dict],
    home_bp: Optional[dict], away_bp: Optional[dict],
    park_run_factor: float,
) -> dict:
    """Return {feature_name: value or None}. Identical logic for training and live prediction."""
    h_sp, a_sp = home_sp or {}, away_sp or {}
    h_bp, a_bp = home_bp or {}, away_bp or {}
    return {
        "off_h_xwoba":    _agg(home_players, "xwoba"),
        "off_a_xwoba":    _agg(away_players, "xwoba"),
        "off_h_wrc_plus": _agg(home_players, "wrc_plus"),
        "off_a_wrc_plus": _agg(away_players, "wrc_plus"),
        "sp_h_xera":      h_sp.get("xera"),
        "sp_a_xera":      a_sp.get("xera"),
        "sp_h_xfip":      h_sp.get("xfip"),
        "sp_a_xfip":      a_sp.get("xfip"),
        "sp_h_k_pct":     h_sp.get("k_pct"),
        "sp_a_k_pct":     a_sp.get("k_pct"),
        "bp_h_xera":      h_bp.get("xera"),
        "bp_a_xera":      a_bp.get("xera"),
        "bp_h_k_pct":     h_bp.get("k_pct"),
        "bp_a_k_pct":     a_bp.get("k_pct"),
        "park":           (park_run_factor / 100.0) if park_run_factor else 1.0,
    }


_CACHE: Optional[dict] = None


def load_model() -> Optional[dict]:
    global _CACHE
    if _CACHE is None:
        if not MODEL_PATH.exists():
            return None
        _CACHE = json.loads(MODEL_PATH.read_text())
    return _CACHE


def predict_home_prob(features: dict, model: Optional[dict] = None) -> Optional[float]:
    """Pure-Python standardized logistic. Returns home win prob in [0,1], or None if no model."""
    model = model or load_model()
    if not model:
        return None
    z = model["intercept"]
    for i, name in enumerate(model["features"]):
        v = features.get(name)
        if v is None:
            v = model["impute_means"][i]
        std = model["scaler_scale"][i] or 1.0
        z += model["coef"][i] * ((v - model["scaler_mean"][i]) / std)
    z = max(-20.0, min(20.0, z))
    return round(1.0 / (1.0 + math.exp(-z)), 4)
