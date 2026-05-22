"""Static park factor tables (updated annually).

Values are indexed by venue name as returned by the MLB Stats API.
100 = league average. Source: FanGraphs park factors (3-year regressed).
"""

# Run-scoring park factors
PARK_RUN_FACTORS: dict[str, int] = {
    "Coors Field": 118,
    "Great American Ball Park": 108,
    "Globe Life Field": 106,
    "Yankee Stadium": 104,
    "Truist Park": 103,
    "Fenway Park": 103,
    "Chase Field": 103,
    "Guaranteed Rate Field": 102,
    "Citizens Bank Park": 102,
    "American Family Field": 101,
    "Target Field": 101,
    "Comerica Park": 100,
    "Wrigley Field": 100,
    "Angel Stadium": 99,
    "Nationals Park": 99,
    "loanDepot park": 98,
    "Kauffman Stadium": 98,
    "Progressive Field": 98,
    "Rogers Centre": 98,
    "Oriole Park at Camden Yards": 97,
    "Minute Maid Park": 97,
    "Dodger Stadium": 97,
    "PNC Park": 96,
    "Busch Stadium": 96,
    "Citi Field": 95,
    "T-Mobile Park": 95,
    "Oakland Coliseum": 94,
    "Tropicana Field": 94,
    "Petco Park": 92,
    "Oracle Park": 93,
}

# Home-run park factors
PARK_HR_FACTORS: dict[str, int] = {
    "Yankee Stadium": 120,
    "Coors Field": 114,
    "Great American Ball Park": 112,
    "Globe Life Field": 109,
    "Citizens Bank Park": 107,
    "Guaranteed Rate Field": 106,
    "American Family Field": 105,
    "Truist Park": 104,
    "Chase Field": 103,
    "Oriole Park at Camden Yards": 102,
    "Comerica Park": 101,
    "Target Field": 100,
    "Wrigley Field": 100,
    "Fenway Park": 98,
    "loanDepot park": 97,
    "Nationals Park": 97,
    "Angel Stadium": 97,
    "Progressive Field": 96,
    "Rogers Centre": 96,
    "Kauffman Stadium": 95,
    "Minute Maid Park": 95,
    "Dodger Stadium": 95,
    "PNC Park": 94,
    "Busch Stadium": 94,
    "Citi Field": 93,
    "T-Mobile Park": 92,
    "Tropicana Field": 91,
    "Oakland Coliseum": 90,
    "Petco Park": 87,
    "Oracle Park": 84,
}


def get_run_factor(venue: str) -> int:
    return PARK_RUN_FACTORS.get(venue, 100)


def get_hr_factor(venue: str) -> int:
    return PARK_HR_FACTORS.get(venue, 100)
