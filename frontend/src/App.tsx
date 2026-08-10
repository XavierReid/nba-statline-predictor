import { useEffect, useState } from "react";
import { getSeasons, getTeams, simulateGame } from "./api";
import type { PlayerLine, SeasonCoverage, SimulateGameResponse, Team } from "./types";
import GameControls from "./components/GameControls";
import LineScore from "./components/LineScore";
import BoxScore from "./components/BoxScore";
import PlayByPlay from "./components/PlayByPlay";
import PlayerModal from "./components/PlayerModal";
import TeamLogo from "./components/TeamLogo";
import { franchiseFor } from "./data/franchises";

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
  const [selected, setSelected] = useState<PlayerLine | null>(null);

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
        <div className="empty-preview">
          {away && home ? (
            <>
              <div className="empty-matchup">
                <div className="empty-team">
                  <TeamLogo abbr={away} season={season} size="lg" />
                  <div className="empty-team-name">
                    {(() => {
                      const fr = franchiseFor(away, season);
                      return fr ? `${fr.city} ${fr.nickname}` : away;
                    })()}
                  </div>
                  <div className="empty-team-label">Away</div>
                </div>
                <div className="empty-vs">@</div>
                <div className="empty-team">
                  <TeamLogo abbr={home} season={season} size="lg" />
                  <div className="empty-team-name">
                    {(() => {
                      const fr = franchiseFor(home, season);
                      return fr ? `${fr.city} ${fr.nickname}` : home;
                    })()}
                  </div>
                  <div className="empty-team-label">Home</div>
                </div>
              </div>
              <p className="empty-hint">Hit Simulate to run a possession-by-possession game.</p>
            </>
          ) : (
            <p className="empty-hint">Loading seasons…</p>
          )}
        </div>
      )}

      {game && !loading && (
        <>
          <LineScore game={game} />
          <div className="boxes">
            <BoxScore title={game.away_team} abbr={game.away_team} season={game.season} sideLabel="Away" players={game.away_box} onSelectPlayer={setSelected} />
            <BoxScore title={game.home_team} abbr={game.home_team} season={game.season} sideLabel="Home" players={game.home_box} onSelectPlayer={setSelected} />
          </div>
          <PlayByPlay game={game} />
        </>
      )}

      {selected && game && (
        <PlayerModal
          key={selected.player_id}
          line={selected}
          season={game.season}
          events={game.events ?? []}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
