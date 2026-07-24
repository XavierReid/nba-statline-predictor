import { useState } from "react";
import type { SimulateGameResponse } from "../types";

function clock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function PlayByPlay({ game }: { game: SimulateGameResponse }) {
  const [open, setOpen] = useState(false);
  const events = game.events ?? [];
  if (events.length === 0) return null;

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
              {events
                .filter((e) => e.description)
                .map((e, i) => {
                  const period = e.quarter <= 4 ? `Q${e.quarter}` : `OT${e.quarter - 4}`;
                  const h = e.running_home_score ?? "";
                  const a = e.running_away_score ?? "";
                  return (
                    <tr key={i} className={e.is_home ? "home" : "away"}>
                      <td className="clock">
                        {period} {clock(e.game_clock_seconds)}
                      </td>
                      <td className="score">{a}-{h}</td>
                      <td>{e.description}</td>
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
