import { useState } from "react";
import type { PlayerLine } from "../types";
import TeamLogo from "./TeamLogo";
import PlayerHeadshot from "./PlayerHeadshot";
import { franchiseFor } from "../data/franchises";

type SortKey = keyof Pick<
  PlayerLine,
  "minutes" | "points" | "rebounds" | "assists" | "steals" | "blocks" | "turnovers" | "personal_fouls" | "plus_minus"
>;

const COLS: { key: SortKey; label: string }[] = [
  { key: "minutes", label: "MIN" },
  { key: "points", label: "PTS" },
  { key: "rebounds", label: "REB" },
  { key: "assists", label: "AST" },
  { key: "steals", label: "STL" },
  { key: "blocks", label: "BLK" },
  { key: "turnovers", label: "TOV" },
  { key: "personal_fouls", label: "PF" },
  { key: "plus_minus", label: "+/-" },
];

function pm(v: number): string {
  return v > 0 ? `+${v}` : `${v}`;
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
      <table>
        <thead>
          <tr>
            <th className="name">Player</th>
            {COLS.map((c) => (
              <th key={c.key} onClick={() => setSortKey(c.key)} title="Sort">
                {c.label}
              </th>
            ))}
            <th>FG</th>
            <th>3PT</th>
            <th>FT</th>
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
              <td>{p.minutes.toFixed(1)}</td>
              <td>{p.points}</td>
              <td>{p.rebounds}</td>
              <td>{p.assists}</td>
              <td>{p.steals}</td>
              <td>{p.blocks}</td>
              <td>{p.turnovers}</td>
              <td>{p.personal_fouls}</td>
              <td>{pm(p.plus_minus)}</td>
              <td>{p.fgm}/{p.fga}</td>
              <td>{p.fg3m}/{p.fg3a}</td>
              <td>{p.ftm}/{p.fta}</td>
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
              <td colSpan={11}></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
