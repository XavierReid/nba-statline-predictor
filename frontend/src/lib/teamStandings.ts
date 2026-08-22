/**
 * Team-standings helpers. Pure functions over a team's game list, used by
 * both the single-team season view and the team drill-in from a full-league
 * sim (where the same aggregates apply — the difference is only which
 * simulation the games came from).
 */
import type { SimulatedGameSummary } from "../types";

export interface RowExt extends SimulatedGameSummary {
  opponent: string;
  margin: number;
  teamScore: number;
  oppScore: number;
  isHome: boolean;
}

export function extendRow(g: SimulatedGameSummary, teamAbbr: string): RowExt {
  const isHome = g.home_team === teamAbbr;
  const teamScore = isHome ? g.home_score : g.away_score;
  const oppScore = isHome ? g.away_score : g.home_score;
  return {
    ...g,
    opponent: isHome ? g.away_team : g.home_team,
    teamScore,
    oppScore,
    margin: teamScore - oppScore,
    isHome,
  };
}

export interface TeamStandings {
  gp: number;
  w: number;
  l: number;
  wPct: number;
  homeW: number;
  homeL: number;
  awayW: number;
  awayL: number;
  ppgScored: number;
  ppgAllowed: number;
  blowoutRate: number;
  otRate: number;
}

export function computeStandings(rows: RowExt[]): TeamStandings {
  const n = rows.length || 1;
  const w = rows.filter((r) => r.win).length;
  const home = rows.filter((r) => r.isHome);
  const away = rows.filter((r) => !r.isHome);
  const scored = rows.reduce((s, r) => s + r.teamScore, 0) / n;
  const allowed = rows.reduce((s, r) => s + r.oppScore, 0) / n;
  const blowouts = rows.filter((r) => Math.abs(r.margin) >= 20).length;
  const otGames = rows.filter((r) => r.went_to_ot).length;
  return {
    gp: rows.length,
    w,
    l: rows.length - w,
    wPct: rows.length ? w / rows.length : 0,
    homeW: home.filter((r) => r.win).length,
    homeL: home.length - home.filter((r) => r.win).length,
    awayW: away.filter((r) => r.win).length,
    awayL: away.length - away.filter((r) => r.win).length,
    ppgScored: scored,
    ppgAllowed: allowed,
    blowoutRate: rows.length ? blowouts / rows.length : 0,
    otRate: rows.length ? otGames / rows.length : 0,
  };
}
