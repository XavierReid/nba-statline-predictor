import type { SimulateGameResponse } from "../types";
import TeamLogo from "./TeamLogo";
import { franchiseFor } from "../data/franchises";

export default function LineScore({ game }: { game: SimulateGameResponse }) {
  const periods = Math.max(game.quarter_scores.home.length, game.quarter_scores.away.length);
  const labels = Array.from({ length: periods }, (_, i) => (i < 4 ? `Q${i + 1}` : `OT${i - 3}`));
  const homeWin = game.home_score > game.away_score;

  const row = (side: "home" | "away", abbr: string, total: number, isWinner: boolean) => {
    const fr = franchiseFor(abbr, game.season);
    return (
      <tr className={isWinner ? "winner" : ""}>
        <td className="team">
          <span className="team-cell" style={fr ? { borderLeftColor: fr.primaryColor } : undefined}>
            <TeamLogo abbr={abbr} season={game.season} size="sm" />
            <span className="team-name">{fr ? `${fr.city} ${fr.nickname}` : abbr}</span>
          </span>
        </td>
        {labels.map((_, i) => (
          <td key={i}>{game.quarter_scores[side][i] ?? ""}</td>
        ))}
        <td className="total">{total}</td>
      </tr>
    );
  };

  return (
    <>
      <table className="linescore">
        <thead>
          <tr>
            <th className="team"></th>
            {labels.map((l) => (
              <th key={l}>{l}</th>
            ))}
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {row("away", game.away_team, game.away_score, !homeWin)}
          {row("home", game.home_team, game.home_score, homeWin)}
        </tbody>
      </table>
      {periods > 4 && (
        <p className="ot-note">Went to {periods - 4 === 1 ? "overtime" : `${periods - 4} overtimes`}.</p>
      )}
    </>
  );
}
