import { useState } from "react";
import type { SimEvent, SimulateGameResponse } from "../types";

function clock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Real NBA PBP collates related attribution events onto a single readable row:
//   - AST/BLK fold onto the parent SHOT     "P1 hits X (assisted by P2)"
//   - STL folds onto the parent TOV         "P1 turns it over (P2 steals)"
//   - Offensive foul: TOV(P) + FOUL(P)      one row reading as "P commits an offensive foul"
// The typed event stream is still complete underneath (chips filter each event on
// its own type). This is display collation only. Standalone REB/FT/FOUL rows and
// isolated STL/BLK/AST at possession boundaries all still render on their own.
function collate(events: SimEvent[]): { rows: SimEvent[]; suffix: Record<number, string> } {
  const rows: SimEvent[] = [];
  const suffix: Record<number, string> = {};
  for (let i = 0; i < events.length; i++) {
    const e = events[i];

    // AST/BLK → fold onto parent SHOT from the same possession.
    if ((e.type === "AST" || e.type === "BLK") && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.type === "SHOT" && prev.possession === e.possession) {
        const prevIdx = rows.length - 1;
        const name = extractName(e.description) ?? (e.type === "AST" ? "teammate" : "defender");
        const bit = e.type === "AST" ? `(assisted by ${name})` : `(blocked by ${name})`;
        suffix[prevIdx] = (suffix[prevIdx] ? `${suffix[prevIdx]} ` : "") + bit;
        continue;
      }
    }

    // STL → fold onto parent TOV from the same possession.
    if (e.type === "STL" && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.type === "TOV" && prev.possession === e.possession) {
        const prevIdx = rows.length - 1;
        const name = extractName(e.description) ?? "opponent";
        suffix[prevIdx] = (suffix[prevIdx] ? `${suffix[prevIdx]} ` : "") + `(${name} steals)`;
        continue;
      }
    }

    // Offensive foul: TOV(P) + FOUL(offensive, same P) → drop the TOV row and
    // keep the FOUL description ("P commits an offensive foul") as the one row.
    if (e.type === "FOUL" && e.foul_kind === "offensive" && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.type === "TOV" && prev.possession === e.possession && prev.player_id === e.player_id) {
        const removedIdx = rows.length - 1;
        rows.pop();
        // Any suffix on the removed TOV (shouldn't happen — STL doesn't co-occur
        // with an offensive foul) would be lost; drop it explicitly.
        delete suffix[removedIdx];
        rows.push(e);
        continue;
      }
    }

    rows.push(e);
  }
  return { rows, suffix };
}

// Descriptions come from describe_typed_event. Pull the leading player name for
// inline collation. Matches both the legacy shape ("P assist" / "P blocks the
// shot") and the enriched form ("P assists Q's mid-range jumper" / "P blocks
// Q's layup"), plus the STL form ("P steal").
function extractName(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const m = desc.match(/^(.+?) (assists?|blocks?|steal)\b/);
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
