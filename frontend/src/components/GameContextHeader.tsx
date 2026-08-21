/**
 * Compact schedule-context header shown above a game's LineScore in season /
 * league drill-in views. Displays date, matchup# (Nth of M meetings this
 * season), and each team's game# (1..82).
 *
 * All fields are optional — renders nothing if none are present (e.g. for
 * ad-hoc POST /simulations/game responses without schedule context).
 */
import type { SimulateGameResponse } from "../types";

function formatDate(iso: string): string {
  // Parse as local (YYYY-MM-DD) — avoid Date's UTC-shift on bare date strings.
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return dt.toLocaleDateString(undefined, {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
}

export default function GameContextHeader({ game }: { game: SimulateGameResponse }) {
  const hasAny =
    game.game_date || game.matchup_index || game.home_game_no || game.away_game_no;
  if (!hasAny) return null;

  const bits: string[] = [];
  if (game.game_date) bits.push(formatDate(game.game_date));
  bits.push(game.season);
  if (game.matchup_index && game.matchup_total) {
    const suffix = ordinal(game.matchup_index);
    bits.push(`${suffix} of ${game.matchup_total} meetings`);
  }

  return (
    <div className="game-context-header">
      <div className="game-context-line">{bits.join(" · ")}</div>
      {(game.home_game_no || game.away_game_no) && (
        <div className="game-context-line game-context-games">
          {game.away_game_no && (
            <span>{game.away_team} G{game.away_game_no}/82</span>
          )}
          {game.away_game_no && game.home_game_no && <span className="dot"> · </span>}
          {game.home_game_no && (
            <span>{game.home_team} G{game.home_game_no}/82</span>
          )}
        </div>
      )}
    </div>
  );
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
