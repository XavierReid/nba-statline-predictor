"""Season-aware franchise identity.

NBA franchise ids are stable across relocations and renames, so historical rosters
already load correctly (Seattle 2005-06 == franchise 1610612760, today's Thunder).
But the `teams` table stores only each franchise's CURRENT identity, so a historical
season reads with the wrong city/name ("2005-06 OKC") and can't be referenced by its
era abbreviation ("SEA"). This overlays the historical (city, nickname, abbreviation)
for the handful of franchises that changed since the mid-90s data floor; every other
franchise falls back to the teams table unchanged.

Each entry is (from_year, city, nickname, abbreviation): the franchise used that
identity from that season's start year until the next entry. `from_year` is the first
year of the season string, e.g. "2008-09" -> 2008.
"""
from typing import Optional, Tuple

# franchise_id -> ascending list of (from_year, city, nickname, abbreviation)
_FRANCHISE_HISTORY = {
    1610612763: [  # Grizzlies
        (1995, "Vancouver", "Grizzlies", "VAN"),
        (2001, "Memphis", "Grizzlies", "MEM"),
    ],
    1610612760: [  # SuperSonics -> Thunder
        (1967, "Seattle", "SuperSonics", "SEA"),
        (2008, "Oklahoma City", "Thunder", "OKC"),
    ],
    1610612751: [  # Nets
        (1977, "New Jersey", "Nets", "NJN"),
        (2012, "Brooklyn", "Nets", "BKN"),
    ],
    1610612764: [  # Bullets -> Wizards (abbreviation unchanged; nickname changed)
        (1974, "Washington", "Bullets", "WAS"),
        (1997, "Washington", "Wizards", "WAS"),
    ],
    1610612740: [  # Charlotte Hornets -> New Orleans -> Pelicans
        (1988, "Charlotte", "Hornets", "CHH"),
        (2002, "New Orleans", "Hornets", "NOH"),
        (2005, "New Orleans/Oklahoma City", "Hornets", "NOK"),  # Katrina years
        (2007, "New Orleans", "Hornets", "NOH"),
        (2013, "New Orleans", "Pelicans", "NOP"),
    ],
    1610612766: [  # Bobcats -> Hornets (expansion 2004)
        (2004, "Charlotte", "Bobcats", "CHA"),
        (2014, "Charlotte", "Hornets", "CHA"),
    ],
}


def _season_year(season: str) -> int:
    return int(season.split("-")[0])


def _active_entry(franchise_id: int, season: str):
    hist = _FRANCHISE_HISTORY.get(franchise_id)
    if not hist:
        return None
    year = _season_year(season)
    active = None
    for entry in hist:  # ascending; last one with from_year <= season wins
        if entry[0] <= year:
            active = entry
        else:
            break
    return active


def team_identity(franchise_id: int, season: str, current: Tuple[str, str, str]) -> Tuple[str, str, str]:
    """(city, nickname, abbreviation) for the franchise in `season`. Falls back to
    `current` (the teams-table identity) when the franchise has no historical overlay
    or the season predates its first recorded identity."""
    entry = _active_entry(franchise_id, season)
    if entry:
        return entry[1], entry[2], entry[3]
    return current


def resolve_abbreviation(abbr: str, season: str) -> Optional[int]:
    """The franchise_id whose ERA abbreviation matches `abbr` in `season`, or None so
    the caller can fall back to the current-abbreviation lookup. Lets a user reference
    a relocated team by the name it had that year (e.g. 'SEA' for a 2005-06 game)."""
    abbr = abbr.upper()
    for franchise_id in _FRANCHISE_HISTORY:
        entry = _active_entry(franchise_id, season)
        if entry and entry[3] == abbr:
            return franchise_id
    return None
