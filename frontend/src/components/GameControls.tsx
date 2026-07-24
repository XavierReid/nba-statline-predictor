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

const PRESETS = ["drama-m3", "drama-m3-season", "baseline"];

export default function GameControls(p: Props) {
  const teamOpts = p.teams.map((t) => (
    <option key={t.id} value={t.abbreviation}>
      {t.city} {t.nickname}
    </option>
  ));

  return (
    <div className="controls">
      <div className="field">
        <label>Season</label>
        <select value={p.season} onChange={(e) => p.onSeason(e.target.value)}>
          {p.seasons.map((s) => (
            <option key={s.season} value={s.season}>
              {s.season}
            </option>
          ))}
        </select>
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
