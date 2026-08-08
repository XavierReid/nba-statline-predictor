import { useState } from "react";
import type { PlayerLine } from "../types";
import TeamLogo from "./TeamLogo";
import PlayerHeadshot from "./PlayerHeadshot";
import { franchiseFor } from "../data/franchises";

type SortKey = keyof Pick<
  PlayerLine,
  "minutes" | "points" | "rebounds" | "assists" | "steals" | "blocks" | "turnovers" | "personal_fouls" | "plus_minus"
>;

/**
 * Column order follows the modern ESPN NBA boxscore convention:
 *   Player | MIN | FG | 3PT | FT | REB | AST | STL | BLK | TO | PF | +/- | PTS
 * PTS goes LAST (headline number closes the row); shooting splits up front
 * next to MIN. Sortable columns carry a `key`; shooting splits are display-only.
 */
type Col =
  | { key: SortKey; label: string }
  | { label: string; render: (p: PlayerLine) => string };

const COLS: Col[] = [
  { key: "minutes", label: "MIN" },
  { label: "FG",   render: (p) => `${p.fgm}/${p.fga}` },
  { label: "3PT",  render: (p) => `${p.fg3m}/${p.fg3a}` },
  { label: "FT",   render: (p) => `${p.ftm}/${p.fta}` },
  { key: "rebounds", label: "REB" },
  { key: "assists", label: "AST" },
  { key: "steals", label: "STL" },
  { key: "blocks", label: "BLK" },
  { key: "turnovers", label: "TO" },
  { key: "personal_fouls", label: "PF" },
  { key: "plus_minus", label: "+/-" },
  { key: "points", label: "PTS" },
];

const NUM_COLS = COLS.length;

function cellValue(p: PlayerLine, c: Col): string {
  if ("render" in c) return c.render(p);
  const v = p[c.key];
  if (c.key === "minutes") return (v as number).toFixed(1);
  if (c.key === "plus_minus") return (v as number) > 0 ? `+${v}` : `${v}`;
  return String(v);
}

interface Props {
  title: string;
  players: PlayerLine[];
  onSelectPlayer: (p: PlayerLine) => void;
  abbr?: string;
  season?: string;
  sideLabel?: string;  // "Home" / "Away"
}

export default function BoxScore({ title, players, onSelectPlayer, abbr, season, sideLabel }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("points");

  const played = players.filter((p) => p.minutes >= 0.5);
  const dnp = players.filter((p) => p.minutes < 0.5);
  const sorted = [...played].sort((a, b) => (b[sortKey] as number) - (a[sortKey] as number));
  const fr = abbr ? franchiseFor(abbr, season) : null;

  return (
    <div className="box" style={fr ? { borderTop: `3px solid ${fr.primaryColor}` } : undefined}>
      <h3 className="box-title">
        {abbr && <TeamLogo abbr={abbr} season={season} size="sm" />}
        <span>{fr ? `${fr.city} ${fr.nickname}` : title}</span>
        {sideLabel && <span className="side-label">({sideLabel})</span>}
      </h3>
      <div className="box-scroll">
        <table>
          <thead>
            <tr>
              <th className="name">Player</th>
              {COLS.map((c) => {
                const sortable = "key" in c;
                return (
                  <th
                    key={c.label}
                    onClick={sortable ? () => setSortKey((c as { key: SortKey }).key) : undefined}
                    className={sortable ? "sortable" : undefined}
                    title={sortable ? "Sort" : undefined}
                  >
                    {c.label}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.player_id} className="clickable" onClick={() => onSelectPlayer(p)}>
                <td className="name">
                  <span className="player-cell">
                    <PlayerHeadshot playerId={p.player_id} name={p.name} size="small" />
                    <span className="player-name">{p.name}</span>
                    {p.fouled_out && <span className="fo">FO</span>}
                  </span>
                </td>
                {COLS.map((c) => (
                  <td key={c.label}>{cellValue(p, c)}</td>
                ))}
              </tr>
            ))}
            {dnp.map((p) => (
              <tr key={p.player_id} className="dnp clickable" onClick={() => onSelectPlayer(p)}>
                <td className="name">
                  <span className="player-cell">
                    <PlayerHeadshot playerId={p.player_id} name={p.name} size="small" />
                    <span className="player-name">{p.name}</span>
                  </span>
                </td>
                <td>DNP</td>
                <td colSpan={NUM_COLS - 1}></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
