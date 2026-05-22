"""HP umpire zone tendency lookup and signal modifiers.

Zone score: positive = expands zone (pitcher-friendly), negative = contracts (hitter-friendly).
Scale: -2 (very small zone) to +2 (very large zone). 0 = neutral.

Data aggregated from public umpire tendency sources (UmpScorecards, Retrosheet).
These are career-level tendencies — individual game variation is high.
"""

from __future__ import annotations

_ZONE_SCORES: dict[str, float] = {
    # Pitcher-friendly (expand zone)
    "Hunter Wendelstedt":   +1.5,
    "Jerry Meals":          +1.0,
    "Gerry Davis":          +1.0,
    "Jeff Nelson":          +1.0,
    "Dan Iassogna":         +1.0,
    "Marvin Hudson":        +1.0,
    "Tripp Gibson":         +1.0,
    "Brian Gorman":         +0.8,
    "Bill Miller":          +0.8,
    "John Hirschbeck":      +0.8,
    "Mark Carlson":         +0.5,
    "Lance Barksdale":      +0.5,
    "Doug Eddings":         +0.5,
    "Jim Wolf":             +0.5,
    "Junior Valentine":     +0.5,
    "James Hoye":           +0.5,
    "Tom Hallion":          +0.5,
    "Ted Barrett":          +0.5,
    "Tim Welke":            +0.5,
    "Mike Winters":         +0.5,
    "Vic Carapazza":        +0.5,
    "Nick Mahrley":         +0.5,
    "D.J. Reyburn":         +0.5,
    "Brian Walsh":          +0.5,
    "Carlos Torres":        +0.3,
    "Alfonso Marquez":      +0.3,
    "Adam Hamari":          +0.3,
    "Stu Scheurwater":      +0.3,
    "David Rackley":        +0.3,
    "Nate Tomlinson":       +0.3,
    "Ryan Blakney":         +0.3,
    "Erich Bacchus":        +0.3,
    "Quinn Wolcott":        +0.3,
    "Adrian Johnson":       +0.3,
    "Mike Muchlinski":      +0.3,
    "Andy Fletcher":        +0.3,
    "Lance Barrett":        +0.3,
    # Hitter-friendly (contract zone)
    "CB Bucknor":           -0.5,
    "Angel Hernandez":      -0.5,
    "Chris Guccione":       -0.5,
    "Chad Fairchild":       -0.5,
    "Roberto Ortiz":        -0.8,
    "Sean Barber":          -0.5,
    "Dan Bellino":          -0.3,
    "Jansen Visconti":      -0.3,
    "Shane Livensparger":   -0.3,
    "Pat Hoberg":           -0.5,
    "Nic Lentz":            -0.3,
    "Alex Tosi":            -0.3,
}


def get_zone_score(umpire_name: str) -> float:
    """Return zone tendency score for the named HP umpire. 0.0 if unknown."""
    if not umpire_name:
        return 0.0
    score = _ZONE_SCORES.get(umpire_name)
    if score is not None:
        return score
    lower = umpire_name.lower()
    for name, val in _ZONE_SCORES.items():
        if name.lower() == lower:
            return val
    return 0.0


def compute_umpire_modifier(umpire_name: str, bet_type: str, direction: str) -> tuple[float, str | None]:
    """Return (signal_modifier, reason_str) for umpire zone tendency.

    Expanded zone (positive score):
      K_PROP OVER        → +modifier (more called strikes)
      WALK_PROP UNDER    → +modifier (pitcher gets borderline calls)
      WALK_PROP OVER     → -modifier

    Contracted zone (negative score):
      K_PROP OVER        → -modifier
      WALK_PROP UNDER    → -modifier
      WALK_PROP OVER     → +modifier
    """
    zone_score = get_zone_score(umpire_name)
    if abs(zone_score) < 0.2:
        return 0.0, None

    modifier = 0.0
    reason: str | None = None
    tendency = "expands" if zone_score > 0 else "contracts"

    if bet_type == "K_PROP" and direction == "OVER":
        modifier = zone_score * 0.35
        if abs(zone_score) >= 0.8:
            reason = f"HP umpire {umpire_name} {tendency} zone — {'more' if zone_score > 0 else 'fewer'} called strikes"

    elif bet_type == "WALK_PROP":
        if direction == "UNDER":
            modifier = zone_score * 0.35
        else:
            modifier = -zone_score * 0.35
        if abs(zone_score) >= 0.8:
            reason = f"HP umpire {umpire_name} {tendency} zone — {'fewer' if zone_score > 0 else 'more'} walks expected"

    return max(-1.0, min(1.0, modifier)), reason
