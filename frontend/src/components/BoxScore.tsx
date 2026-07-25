import { useState } from "react";
import type { PlayerLine } from "../types";

type SortKey = keyof Pick<
  PlayerLine,
  "minutes" | "points" | "rebounds" | "assists" | "steals" | "blocks" | "turnovers" | "personal_fouls"
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
];

export default function BoxScore({ title, players }: { title: string; players: PlayerLine[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("points");

  const played = players.filter((p) => p.minutes >= 0.5);
  const dnp = players.filter((p) => p.minutes < 0.5);
  const sorted = [...played].sort((a, b) => (b[sortKey] as number) - (a[sortKey] as number));

  return (
    <div className="box">
      <h3>{title}</h3>
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
            <tr key={p.player_id}>
              <td className="name">
                {p.name}
                {p.fouled_out && <span className="fo">FO</span>}
              </td>
              <td>{p.minutes.toFixed(1)}</td>
              <td>{p.points}</td>
              <td>{p.rebounds}</td>
              <td>{p.assists}</td>
              <td>{p.steals}</td>
              <td>{p.blocks}</td>
              <td>{p.turnovers}</td>
              <td>{p.personal_fouls}</td>
              <td>{p.fgm}/{p.fga}</td>
              <td>{p.fg3m}/{p.fg3a}</td>
              <td>{p.ftm}/{p.fta}</td>
            </tr>
          ))}
          {dnp.map((p) => (
            <tr key={p.player_id} className="dnp">
              <td className="name">{p.name}</td>
              <td>DNP</td>
              <td colSpan={10}></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
