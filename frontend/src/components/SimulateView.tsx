/**
 * SimulateView — single entry point for all multi-game simulations.
 *
 * Owns a scope toggle (Team season vs Full league) that mounts the underlying
 * SeasonView or LeagueView. Each view continues to own its own picker + past-
 * runs state — this wrapper only unifies the entry.
 *
 * Consolidates the previously-separate Season / League tabs into one, matching
 * the C-2 design-lock intent. Internal merge of the two views is deferred;
 * this landing gets the unified affordance without touching 1500 lines below.
 */
import { useState } from "react";
import SeasonView from "./SeasonView";
import LeagueView from "./LeagueView";

type Scope = "team" | "league";

export default function SimulateView() {
  const [scope, setScope] = useState<Scope>("team");

  return (
    <div className="simulate-view">
      <div className="scope-toggle" role="tablist" aria-label="Simulation scope">
        <button
          role="tab"
          className={`scope-btn ${scope === "team" ? "on" : ""}`}
          aria-selected={scope === "team"}
          onClick={() => setScope("team")}
        >
          Team
        </button>
        <button
          role="tab"
          className={`scope-btn ${scope === "league" ? "on" : ""}`}
          aria-selected={scope === "league"}
          onClick={() => setScope("league")}
        >
          Full League
        </button>
      </div>
      {scope === "team" ? <SeasonView /> : <LeagueView />}
    </div>
  );
}
