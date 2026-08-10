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

/** CSS class for a cell — used to color-code +/- (green +, red -). */
function cellClass(p: PlayerLine, c: Col): string | undefined {
  if ("key" in c && c.key === "plus_minus") {
    if (p.plus_minus > 0) return "pm-plus";
    if (p.plus_minus < 0) return "pm-minus";
  }
  return undefined;
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
                const active = sortable && (c as { key: SortKey }).key === sortKey;
                return (
                  <th
                    key={c.label}
                    onClick={sortable ? () => setSortKey((c as { key: SortKey }).key) : undefined}
                    className={`${sortable ? "sortable" : ""} ${active ? "sort-active" : ""}`.trim() || undefined}
                    title={sortable ? "Sort" : undefined}
                  >
                    {c.label}
                    {active && <span className="sort-arrow" aria-hidden="true"> ▼</span>}
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
                  <td key={c.label} className={cellClass(p, c)}>{cellValue(p, c)}</td>
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
            {(() => {
              // ESPN-style team totals row at the bottom, summed across ALL players
              // (played + DNP so MIN totals to 240 in regulation, higher for OT).
              // Shooting splits render as sum-of-makes / sum-of-attempts; +/- is
              // meaningless as a sum so it stays blank.
              const tot = {
                minutes: 0, points: 0, rebounds: 0, assists: 0,
                steals: 0, blocks: 0, turnovers: 0, personal_fouls: 0,
                fgm: 0, fga: 0, fg3m: 0, fg3a: 0, ftm: 0, fta: 0,
              };
              for (const p of players) {
                tot.minutes += p.minutes; tot.points += p.points;
                tot.rebounds += p.rebounds; tot.assists += p.assists;
                tot.steals += p.steals; tot.blocks += p.blocks;
                tot.turnovers += p.turnovers; tot.personal_fouls += p.personal_fouls;
                tot.fgm += p.fgm; tot.fga += p.fga;
                tot.fg3m += p.fg3m; tot.fg3a += p.fg3a;
                tot.ftm += p.ftm; tot.fta += p.fta;
              }
              const fgPct = tot.fga > 0 ? ((tot.fgm / tot.fga) * 100).toFixed(1) : "—";
              const fg3Pct = tot.fg3a > 0 ? ((tot.fg3m / tot.fg3a) * 100).toFixed(1) : "—";
              const ftPct = tot.fta > 0 ? ((tot.ftm / tot.fta) * 100).toFixed(1) : "—";
              return (
                <>
                  <tr className="team-totals">
                    <td className="name">TOTALS</td>
                    <td></td>
                    <td>{tot.fgm}/{tot.fga}</td>
                    <td>{tot.fg3m}/{tot.fg3a}</td>
                    <td>{tot.ftm}/{tot.fta}</td>
                    <td>{tot.rebounds}</td>
                    <td>{tot.assists}</td>
                    <td>{tot.steals}</td>
                    <td>{tot.blocks}</td>
                    <td>{tot.turnovers}</td>
                    <td>{tot.personal_fouls}</td>
                    <td></td>
                    <td>{tot.points}</td>
                  </tr>
                  <tr className="team-pcts">
                    <td className="name"></td>
                    <td></td>
                    <td>{fgPct}%</td>
                    <td>{fg3Pct}%</td>
                    <td>{ftPct}%</td>
                    <td colSpan={NUM_COLS - 4}></td>
                  </tr>
                </>
              );
            })()}
          </tbody>
        </table>
      </div>
    </div>
  );
}
