import { useState } from "react";
import SingleGameView from "./components/SingleGameView";
import SimulateView from "./components/SimulateView";

type Tab = "single" | "simulate";

export default function App() {
  const [tab, setTab] = useState<Tab>("single");
  return (
    <div className="app">
      <div className="app-header">
        <div>
          <h1>NBA Franchise Simulator</h1>
          <p className="subtitle">Possession-based game engine · pick a matchup and simulate</p>
        </div>
        <div className="app-tabs" role="tablist">
          <button
            role="tab"
            className={`app-tab ${tab === "single" ? "on" : ""}`}
            aria-selected={tab === "single"}
            onClick={() => setTab("single")}
          >
            Single Game
          </button>
          <button
            role="tab"
            className={`app-tab ${tab === "simulate" ? "on" : ""}`}
            aria-selected={tab === "simulate"}
            onClick={() => setTab("simulate")}
          >
            Simulate
          </button>
        </div>
      </div>
      {tab === "single" ? <SingleGameView /> : <SimulateView />}
    </div>
  );
}
