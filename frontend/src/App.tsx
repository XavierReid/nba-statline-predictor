import { useEffect, useState } from "react";
import { getSeasons, getTeams, simulateGame } from "./api";
import type { SeasonCoverage, SimulateGameResponse, Team } from "./types";
import GameControls from "./components/GameControls";
import LineScore from "./components/LineScore";
import BoxScore from "./components/BoxScore";
import PlayByPlay from "./components/PlayByPlay";

export default function App() {
  const [seasons, setSeasons] = useState<SeasonCoverage[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [season, setSeason] = useState("");
  const [home, setHome] = useState("");
  const [away, setAway] = useState("");
  const [seed, setSeed] = useState("");
  const [preset, setPreset] = useState("drama-m3");
  const [game, setGame] = useState<SimulateGameResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSeasons()
      .then((s) => {
        setSeasons(s);
        if (s.length) setSeason(s[0].season);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!season) return;
    getTeams(season)
      .then((t) => {
        setTeams(t);
        if (t.length >= 2) {
          setAway(t[0].abbreviation);
          setHome(t[1].abbreviation);
        }
      })
      .catch((e) => setError(String(e)));
  }, [season]);

  async function onSimulate() {
    setLoading(true);
    setError(null);
    try {
      const result = await simulateGame({
        home_team: home,
        away_team: away,
        season,
        seed: seed === "" ? undefined : Number(seed),
        preset,
        include_pbp: true,
      });
      setGame(result);
    } catch (e) {
      setError(String(e));
      setGame(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <h1>NBA Franchise Simulator</h1>
      <p className="subtitle">Possession-based game engine · pick a matchup and simulate</p>

      <GameControls
        seasons={seasons}
        teams={teams}
        season={season}
        home={home}
        away={away}
        seed={seed}
        preset={preset}
        loading={loading}
        onSeason={setSeason}
        onHome={setHome}
        onAway={setAway}
        onSeed={setSeed}
        onPreset={setPreset}
        onSimulate={onSimulate}
      />

      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Running the simulation…</div>}

      {!game && !loading && !error && (
        <div className="empty">Pick two teams and hit Simulate.</div>
      )}

      {game && !loading && (
        <>
          <LineScore game={game} />
          <div className="boxes">
            <BoxScore title={`${game.away_team} (Away)`} players={game.away_box} />
            <BoxScore title={`${game.home_team} (Home)`} players={game.home_box} />
          </div>
          <PlayByPlay game={game} />
        </>
      )}
    </div>
  );
}
