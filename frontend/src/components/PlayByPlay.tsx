import { useMemo, useState } from "react";
import type { SimEvent, SimEventType, SimulateGameResponse } from "../types";

function clock(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const CHIP_ORDER: SimEventType[] = [
  "SHOT", "FT", "AST", "REB", "STL", "BLK", "TOV", "FOUL", "SUBSTITUTION",
];

// Short label for the chip UI (SUBSTITUTION → SUB); other types show their
// name unchanged. Kept as a lookup rather than a Record so unlisted types
// still fall through to their literal name.
const CHIP_LABEL: Partial<Record<SimEventType, string>> = {
  SUBSTITUTION: "SUB",
};

interface CollatedRow {
  parent: SimEvent;
  suffix: string;
  // Every event type contained in this collated row (parent + any folded children).
  // Chip filter operates on this so filtering to AST surfaces the parent SHOT row.
  types: Set<SimEventType>;
}

// Real NBA PBP collates related attribution events onto a single readable row:
//   - AST/BLK fold onto the parent SHOT     "P1 hits X (assisted by P2)"
//   - STL folds onto the parent TOV         "P1 turns it over (P2 steals)"
//   - Offensive foul: TOV(P) + FOUL(P)      one row reading as "P commits an offensive foul"
// The typed event stream is still complete underneath. Standalone REB/FT/FOUL rows and
// isolated STL/BLK/AST at possession boundaries all still render on their own.
function collate(events: SimEvent[]): CollatedRow[] {
  const rows: CollatedRow[] = [];
  for (let i = 0; i < events.length; i++) {
    const e = events[i];

    // Initial-lineup SUBs (player_out === null) come in per-team runs of 5
    // at the very start of the stream. Collate each run into a single
    // "STARTERS · [names]" row so the PBP doesn't open with 10 rows of
    // one-player-each announcements. Engine still emits 10 discrete SUBs
    // (correctness gate depends on that); this is display-only.
    if (
      e.type === "SUBSTITUTION" &&
      e.player_out == null &&
      e.player_in != null
    ) {
      const names: string[] = [];
      let j = i;
      while (
        j < events.length &&
        events[j].type === "SUBSTITUTION" &&
        events[j].player_out == null &&
        events[j].is_home === e.is_home
      ) {
        const n = extractStarterName(events[j].description);
        if (n) names.push(n);
        j++;
      }
      const synthetic: SimEvent = {
        ...e,
        description: `STARTERS: ${names.join(", ")}`,
      };
      rows.push({
        parent: synthetic,
        suffix: "",
        types: new Set<SimEventType>(["SUBSTITUTION"]),
      });
      i = j - 1; // outer for-loop will i++ past the last folded event
      continue;
    }

    // AST/BLK → fold onto parent SHOT from the same possession.
    if ((e.type === "AST" || e.type === "BLK") && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.parent.type === "SHOT" && prev.parent.possession === e.possession) {
        const name = extractName(e.description) ?? (e.type === "AST" ? "teammate" : "defender");
        const bit = e.type === "AST" ? `(assisted by ${name})` : `(blocked by ${name})`;
        prev.suffix = prev.suffix ? `${prev.suffix} ${bit}` : bit;
        prev.types.add(e.type);
        continue;
      }
    }

    // STL → fold onto parent TOV from the same possession.
    if (e.type === "STL" && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (prev.parent.type === "TOV" && prev.parent.possession === e.possession) {
        const name = extractName(e.description) ?? "opponent";
        prev.suffix = prev.suffix ? `${prev.suffix} (${name} steals)` : `(${name} steals)`;
        prev.types.add(e.type);
        continue;
      }
    }

    // Offensive foul: TOV(P) + FOUL(offensive, same P) → drop the TOV row and
    // keep the FOUL description ("P commits an offensive foul") as the one row.
    if (e.type === "FOUL" && e.foul_kind === "offensive" && rows.length > 0) {
      const prev = rows[rows.length - 1];
      if (
        prev.parent.type === "TOV" &&
        prev.parent.possession === e.possession &&
        prev.parent.player_id === e.player_id
      ) {
        rows.pop();
        rows.push({ parent: e, suffix: "", types: new Set<SimEventType>([e.type, "TOV"]) });
        continue;
      }
    }

    rows.push({ parent: e, suffix: "", types: new Set<SimEventType>([e.type]) });
  }
  return rows;
}

function extractName(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const m = desc.match(/^(.+?) (assists?|blocks?|steals?)\b/);
  return m ? m[1] : null;
}

function extractStarterName(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const m = desc.match(/^(.+?) starts$/);
  return m ? m[1] : null;
}

function periodLabel(quarter: number): string {
  return quarter <= 4 ? `Q${quarter}` : `OT${quarter - 4}`;
}

export default function PlayByPlay({ game }: { game: SimulateGameResponse }) {
  const [open, setOpen] = useState(false);
  const events = game.events ?? [];

  // Every hook that follows must run on every render even when we return null
  // — collate + filter memos are always live so React sees a stable order.
  const rows = useMemo(() => collate(events), [events]);
  const maxQuarter = useMemo(
    () => rows.reduce((m, r) => Math.max(m, r.parent.quarter), 1),
    [rows]
  );

  const [quarterFilter, setQuarterFilter] = useState<number | "all">("all");
  const [search, setSearch] = useState("");
  const [activeChips, setActiveChips] = useState<Set<SimEventType>>(new Set());

  const toggleChip = (t: SimEventType) =>
    setActiveChips((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  const searchLower = search.trim().toLowerCase();
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      if (!r.parent.description) return false;
      if (quarterFilter !== "all" && r.parent.quarter !== quarterFilter) return false;
      if (activeChips.size > 0) {
        let any = false;
        for (const t of activeChips) {
          if (r.types.has(t)) { any = true; break; }
        }
        if (!any) return false;
      }
      if (searchLower) {
        const desc = r.parent.description.toLowerCase();
        const suff = r.suffix.toLowerCase();
        if (!desc.includes(searchLower) && !suff.includes(searchLower)) return false;
      }
      return true;
    });
  }, [rows, quarterFilter, activeChips, searchLower]);

  if (events.length === 0) return null;

  const quarters: (number | "all")[] = ["all"];
  for (let q = 1; q <= maxQuarter; q++) quarters.push(q);

  return (
    <>
      <div className="pbp-toggle">
        <button onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Show"} play-by-play ({events.length} events)
        </button>
      </div>
      {open && (
        <div className="pbp">
          <div className="pbp-filters">
            <div className="pbp-quarters" role="group" aria-label="Quarter filter">
              {quarters.map((q) => (
                <button
                  key={q}
                  className={quarterFilter === q ? "pbp-quarter on" : "pbp-quarter"}
                  onClick={() => setQuarterFilter(q)}
                >
                  {q === "all" ? "All" : periodLabel(q)}
                </button>
              ))}
            </div>
            <input
              className="pbp-search"
              type="search"
              placeholder="Search descriptions…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search play-by-play"
            />
            <div className="pbp-chips" role="group" aria-label="Event type filter">
              {CHIP_ORDER.map((t) => (
                <button
                  key={t}
                  className={activeChips.has(t) ? "pbp-chip on" : "pbp-chip"}
                  onClick={() => toggleChip(t)}
                >
                  {CHIP_LABEL[t] ?? t}
                </button>
              ))}
              {activeChips.size > 0 && (
                <button
                  className="pbp-chip pbp-chip-clear"
                  onClick={() => setActiveChips(new Set())}
                  aria-label="Clear event type filters"
                >
                  ×
                </button>
              )}
            </div>
          </div>
          {filteredRows.length === 0 ? (
            <p className="pbp-empty">No events match the filter.</p>
          ) : (
            <table>
              <tbody>
                {(() => {
                  const out: React.ReactNode[] = [];
                  let lastQuarter: number | null = null;
                  filteredRows.forEach((r, i) => {
                    const e = r.parent;
                    if (e.quarter !== lastQuarter) {
                      out.push(
                        <tr key={`sep-${e.quarter}-${i}`} className="pbp-period-sep">
                          <td colSpan={3}>{periodLabel(e.quarter)}</td>
                        </tr>
                      );
                      lastQuarter = e.quarter;
                    }
                    const h = e.running_home_score ?? "";
                    const a = e.running_away_score ?? "";
                    const text = r.suffix ? `${e.description} ${r.suffix}` : e.description;
                    const isSub = e.type === "SUBSTITUTION";
                    const isStarters = isSub && e.description?.startsWith("STARTERS:");
                    const rowClass = [
                      e.is_home ? "home" : "away",
                      isSub ? "pbp-sub" : "",
                      isStarters ? "pbp-starters" : "",
                    ].filter(Boolean).join(" ");
                    out.push(
                      <tr key={i} className={rowClass}>
                        <td className="clock">
                          {periodLabel(e.quarter)} {clock(e.game_clock_seconds)}
                        </td>
                        <td className="score">{a}-{h}</td>
                        <td>{text}</td>
                      </tr>
                    );
                  });
                  return out;
                })()}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}
