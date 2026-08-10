import type { SimulateGameResponse } from "../types";
import TeamLogo from "./TeamLogo";
import { franchiseFor } from "../data/franchises";
import { readableOnDark } from "../data/color";

export default function LineScore({ game }: { game: SimulateGameResponse }) {
  const periods = Math.max(game.quarter_scores.home.length, game.quarter_scores.away.length);
  const labels = Array.from({ length: periods }, (_, i) => (i < 4 ? `Q${i + 1}` : `OT${i - 3}`));
  const homeWin = game.home_score > game.away_score;

  const row = (side: "home" | "away", abbr: string, total: number, isWinner: boolean) => {
    const fr = franchiseFor(abbr, game.season);
    // Winner's total renders in that team's primary color — connects the
    // brand identity to the win rather than the generic --win accent.
    // Low-luminance colors (e.g. Denver navy #0E2240) are lightened for
    // readability on the dark background.
    const totalStyle = isWinner && fr ? { color: readableOnDark(fr.primaryColor) } : undefined;
    return (
      <tr className={isWinner ? "winner" : ""}>
        <td className="team">
          <span className="team-cell" style={fr ? { borderLeftColor: fr.primaryColor } : undefined}>
            <TeamLogo abbr={abbr} season={game.season} size="sm" />
            <span className="team-name">{fr ? `${fr.city} ${fr.nickname}` : abbr}</span>
          </span>
        </td>
        {labels.map((_, i) => {
          const my = game.quarter_scores[side][i] ?? 0;
          const opp = game.quarter_scores[side === "home" ? "away" : "home"][i] ?? 0;
          // Bold the higher score per quarter — small ESPN-style visual
          // narrative of who won each period.
          const wonQuarter = my > opp;
          return (
            <td key={i} className={wonQuarter ? "q-win" : undefined}>
              {game.quarter_scores[side][i] ?? ""}
            </td>
          );
        })}
        <td className="total" style={totalStyle}>{total}</td>
      </tr>
    );
  };

  // Winning franchise's primary color drives the card's top-border stripe,
  // matching BoxScore's treatment.
  const winnerFr = franchiseFor(homeWin ? game.home_team : game.away_team, game.season);
  const stripeStyle = winnerFr ? { borderTop: `3px solid ${winnerFr.primaryColor}` } : undefined;

  return (
    <div className="linescore-wrap" style={stripeStyle}>
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
    </div>
  );
}
