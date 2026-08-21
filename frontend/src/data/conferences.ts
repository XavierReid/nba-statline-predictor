/**
 * Current NBA conference alignment (30-team era, 2004-05 onward).
 * Used by LeagueView to split standings East/West.
 *
 * If we ever run league sims on pre-2004 seasons with an era-aware validator,
 * this will need to become era-aware too (Vancouver was West, expansion teams
 * shifted alignment). Not needed for current C-2 scope (2005-06+ only).
 */
export const EAST_TEAMS: ReadonlySet<string> = new Set([
  "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND",
  "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS",
]);

export const WEST_TEAMS: ReadonlySet<string> = new Set([
  "DAL", "DEN", "GSW", "HOU", "LAC", "LAL", "MEM", "MIN",
  "NOP", "OKC", "PHX", "POR", "SAC", "SAS", "UTA",
]);

export function conferenceOf(abbr: string): "East" | "West" | null {
  if (EAST_TEAMS.has(abbr)) return "East";
  if (WEST_TEAMS.has(abbr)) return "West";
  return null;
}
