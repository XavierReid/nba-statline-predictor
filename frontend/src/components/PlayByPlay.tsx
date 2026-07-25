import { useState } from "react";
import type { SimEvent, SimulateGameResponse } from "../types";

function clock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Real NBA PBP shows a made shot + its assist as ONE row ("P1 makes a 3-pointer
// (P2 assists)"). Same for a missed shot + its block. Walk the granular event
// stream and mark AST/BLK events that immediately follow their parent SHOT in
// the same possession as "collated" — the renderer folds them inline and skips
// standalone rendering. Standalone REB/FT/FOUL rows still render, matching how
// NBA official PBP presents them.
function collate(events: SimEvent[]): { rows: SimEvent[]; suffix: Record<number, string> } {
  const rows: SimEvent[] = [];
  const suffix: Record<number, string> = {};
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if ((e.type === "AST" || e.type === "BLK") && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.type === "SHOT" && prev.possession === e.possession) {
        const prevIdx = rows.length - 1;
        const bit = e.type === "AST"
          ? `(assisted by ${extractName(e.description) ?? "teammate"})`
          : `(blocked by ${extractName(e.description) ?? "defender"})`;
        suffix[prevIdx] = (suffix[prevIdx] ? `${suffix[prevIdx]} ` : "") + bit;
        continue;
      }
    }
    rows.push(e);
  }
  return { rows, suffix };
}

// Descriptions come from describe_typed_event as "<Name> assist" / "<Name> blocks
// the shot". Pull the leading name out for inline collation.
function extractName(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const m = desc.match(/^(.+?) (assist|blocks the shot)$/);
  return m ? m[1] : null;
}

export default function PlayByPlay({ game }: { game: SimulateGameResponse }) {
  const [open, setOpen] = useState(false);
  const events = game.events ?? [];
  if (events.length === 0) return null;

  const { rows, suffix } = collate(events);

  return (
    <>
      <div className="pbp-toggle">
        <button onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Show"} play-by-play ({events.length} events)
        </button>
      </div>
      {open && (
        <div className="pbp">
          <table>
            <tbody>
              {rows
                .filter((e) => e.description)
                .map((e, i) => {
                  const period = e.quarter <= 4 ? `Q${e.quarter}` : `OT${e.quarter - 4}`;
                  const h = e.running_home_score ?? "";
                  const a = e.running_away_score ?? "";
                  const text = suffix[i] ? `${e.description} ${suffix[i]}` : e.description;
                  return (
                    <tr key={i} className={e.is_home ? "home" : "away"}>
                      <td className="clock">
                        {period} {clock(e.game_clock_seconds)}
                      </td>
                      <td className="score">{a}-{h}</td>
                      <td>{text}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
