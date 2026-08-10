import type { SeasonCoverage, Team } from "../types";

interface Props {
  seasons: SeasonCoverage[];
  teams: Team[];
  season: string;
  home: string;
  away: string;
  seed: string;
  preset: string;
  loading: boolean;
  onSeason: (s: string) => void;
  onHome: (a: string) => void;
  onAway: (a: string) => void;
  onSeed: (s: string) => void;
  onPreset: (p: string) => void;
  onSimulate: () => void;
}

// drama-m3-season (per-game availability) is a SEASON-path config — it sits players and the
// single-game API returns only the active ones, so a named matchup would show missing rows
// with no DNP. Single games dress everyone; availability belongs to the future season UI.
const PRESETS = ["drama-m3", "baseline"];

export default function GameControls(p: Props) {
  const teamOpts = p.teams.map((t) => (
    <option key={t.id} value={t.abbreviation}>
      {t.city} {t.nickname}
    </option>
  ));

  // Compact year label — "2007-08" → "'08". Keeps the timeline readable at
  // narrow widths without hiding the full label on hover.
  const shortYear = (s: string) => {
    const [, yy] = s.split("-");
    return yy ? `'${yy}` : s;
  };

  return (
    <div className="controls">
      <div className="field season-timeline-field">
        <label>Season</label>
        <div className="season-timeline" role="radiogroup" aria-label="Season">
          {p.seasons.map((s) => (
            <button
              key={s.season}
              type="button"
              role="radio"
              aria-checked={p.season === s.season}
              className={`season-pill ${p.season === s.season ? "on" : ""}`}
              onClick={() => p.onSeason(s.season)}
              title={s.season}
            >
              {shortYear(s.season)}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Away</label>
        <select value={p.away} onChange={(e) => p.onAway(e.target.value)}>
          {teamOpts}
        </select>
      </div>

      <span className="vs">@</span>

      <div className="field">
        <label>Home</label>
        <select value={p.home} onChange={(e) => p.onHome(e.target.value)}>
          {teamOpts}
        </select>
      </div>

      <div className="field seed">
        <label>Seed</label>
        <input
          type="number"
          placeholder="random"
          value={p.seed}
          onChange={(e) => p.onSeed(e.target.value)}
        />
      </div>

      <div className="field">
        <label>Preset</label>
        <select value={p.preset} onChange={(e) => p.onPreset(e.target.value)}>
          {PRESETS.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
      </div>

      <button className="sim" onClick={p.onSimulate} disabled={p.loading || p.home === p.away}>
        {p.loading ? "Simulating…" : "Simulate"}
      </button>
    </div>
  );
}
