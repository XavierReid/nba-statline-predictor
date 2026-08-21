/**
 * LeagueView — full 30-team, 1230-game league sim UI (C-2).
 *
 * Flow:
 *   pick season/seed → create + start league sim → poll progress → provisional
 *   standings → complete standings → click team → team's 82 games → click game →
 *   existing game view (reused from SingleGameView / SeasonView drill-in).
 *
 * Deviates slightly from the design lock: entry point is a top-level tab rather
 * than a RunPicker button. Same functionality. Refactor into RunPicker is on
 * the UI-batch backlog (task #91). See project-session-c2-design-lock.
 */
import { useCallback, useEffect, useState } from "react";
import {
  cancelSimulation,
  createLeagueSimulation,
  getLeagueTeamGames,
  getSeasonGame,
  getSeasons,
  getSimulation,
  getStandings,
  listSimulations,
  startSimulation,
} from "../api";
import type {
  SeasonCoverage,
  SimulatedGameSummary,
  SimulateGameResponse,
  SimulationStatus,
  SimulationSummary,
  StandingsResponse,
} from "../types";
import LineScore from "./LineScore";
import BoxScore from "./BoxScore";
import PlayByPlay from "./PlayByPlay";
import TeamLogo from "./TeamLogo";

type View =
  | { kind: "picker" }
  | { kind: "creating" }
  | { kind: "running"; simId: number }
  | { kind: "standings"; simId: number; season: string }
  | { kind: "team"; simId: number; season: string; teamAbbr: string }
  | { kind: "game"; simId: number; season: string; teamAbbr: string; gameId: string };


export default function LeagueView() {
  const [view, setView] = useState<View>({ kind: "picker" });
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="league-view">
      {error && <div className="error">{error}</div>}
      {view.kind === "picker" && (
        <LeaguePicker
          onStart={(simId) => setView({ kind: "running", simId })}
          onSelect={(sim) =>
            sim.status === "complete"
              ? setView({ kind: "standings", simId: sim.id, season: sim.season })
              : setView({ kind: "running", simId: sim.id })
          }
          onError={setError}
        />
      )}
      {view.kind === "running" && (
        <LeagueRunning
          simId={view.simId}
          onComplete={(sim) =>
            setView({ kind: "standings", simId: sim.id, season: sim.season })
          }
          onCancel={() => setView({ kind: "picker" })}
          onError={setError}
        />
      )}
      {view.kind === "standings" && (
        <LeagueStandings
          simId={view.simId}
          onBack={() => setView({ kind: "picker" })}
          onTeamClick={(teamAbbr) =>
            setView({ kind: "team", simId: view.simId, season: view.season, teamAbbr })
          }
          onError={setError}
        />
      )}
      {view.kind === "team" && (
        <LeagueTeamGames
          simId={view.simId}
          season={view.season}
          teamAbbr={view.teamAbbr}
          onBack={() => setView({ kind: "standings", simId: view.simId, season: view.season })}
          onGameClick={(gameId) =>
            setView({ ...view, kind: "game", gameId })
          }
          onError={setError}
        />
      )}
      {view.kind === "game" && (
        <LeagueGameDetail
          simId={view.simId}
          gameId={view.gameId}
          onBack={() =>
            setView({ kind: "team", simId: view.simId, season: view.season, teamAbbr: view.teamAbbr })
          }
          onError={setError}
        />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Picker: pick season + start a new league sim, or pick a prior one
// ---------------------------------------------------------------------------

interface LeaguePickerProps {
  onStart: (simId: number) => void;
  onSelect: (sim: SimulationSummary) => void;
  onError: (msg: string) => void;
}

function LeaguePicker({ onStart, onSelect, onError }: LeaguePickerProps) {
  const [seasons, setSeasons] = useState<SeasonCoverage[]>([]);
  const [runs, setRuns] = useState<SimulationSummary[]>([]);
  const [season, setSeason] = useState("");
  const [seed, setSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([getSeasons(), listSimulations()]);
      setSeasons(s);
      setRuns(r.filter((x) => x.scope === "league"));
      if (s.length && !season) setSeason(s[0].season);
    } catch (e) {
      onError(String(e));
    }
  }, [onError, season]);

  useEffect(() => { load(); }, [load]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!season || submitting) return;
    setSubmitting(true);
    try {
      const created = await createLeagueSimulation({
        season,
        seed: seed === "" ? null : Number(seed),
      });
      await startSimulation(created.id);
      onStart(created.id);
    } catch (err) {
      onError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="league-picker">
      <h2>Simulate a full NBA season (all 30 teams, 1230 games)</h2>
      <form onSubmit={onSubmit} className="league-new-form">
        <label>
          Season
          <select value={season} onChange={(e) => setSeason(e.target.value)}>
            {seasons.map((s) => (
              <option key={s.season} value={s.season}>{s.season}</option>
            ))}
          </select>
        </label>
        <label>
          Seed <span style={{ opacity: 0.6, fontSize: "0.85em" }}>(optional)</span>
          <input
            type="text"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            placeholder="random"
            style={{ width: 100 }}
          />
        </label>
        <button type="submit" disabled={!season || submitting}>
          {submitting ? "Starting…" : "Simulate League Season"}
        </button>
      </form>

      {runs.length > 0 && (
        <div className="league-runs">
          <h3>Past league simulations</h3>
          <table className="league-runs-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Season</th>
                <th>Seed</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Created</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{r.season}</td>
                  <td>{r.seed}</td>
                  <td>{r.status}</td>
                  <td>{r.games_completed}/{r.total_games}</td>
                  <td>{new Date(r.created_at).toLocaleDateString()}</td>
                  <td>
                    <button onClick={() => onSelect(r)}>
                      {r.status === "complete" ? "View standings" : "Resume view"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Running: poll status until complete
// ---------------------------------------------------------------------------

interface LeagueRunningProps {
  simId: number;
  onComplete: (sim: SimulationStatus) => void;
  onCancel: () => void;
  onError: (msg: string) => void;
}

function LeagueRunning({ simId, onComplete, onCancel, onError }: LeagueRunningProps) {
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const s = await getSimulation(simId);
        if (cancelled) return;
        setStatus(s);
        if (s.status === "complete") { onComplete(s); return; }
        if (s.status === "failed" || s.status === "cancelled") return;
        setTimeout(tick, 1000);
      } catch (e) {
        if (!cancelled) onError(String(e));
      }
    }
    tick();
    return () => { cancelled = true; };
  }, [simId, onComplete, onError]);

  async function handleCancel() {
    setCancelling(true);
    try {
      await cancelSimulation(simId);
      onCancel();
    } catch (e) {
      onError(String(e));
    } finally {
      setCancelling(false);
    }
  }

  if (!status) return <div>Loading simulation…</div>;
  const pct = status.total_games > 0 ? (status.games_completed / status.total_games * 100) : 0;

  return (
    <div className="league-running">
      <h2>Simulating {status.season} — 1230 games</h2>
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <p>{status.games_completed} / {status.total_games} games ({pct.toFixed(1)}%)</p>
      <p>Status: <strong>{status.status}</strong></p>
      {status.status === "running" && (
        <button onClick={handleCancel} disabled={cancelling}>
          {cancelling ? "Cancelling…" : "Cancel"}
        </button>
      )}
      {status.status === "cancelled" && (
        <p>Simulation cancelled. <button onClick={onCancel}>Back</button></p>
      )}
      {status.status === "failed" && (
        <p>Simulation failed. <button onClick={onCancel}>Back</button></p>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Standings: 30 rows sortable by W desc / L asc / team_id
// ---------------------------------------------------------------------------

interface LeagueStandingsProps {
  simId: number;
  onBack: () => void;
  onTeamClick: (teamAbbr: string) => void;
  onError: (msg: string) => void;
}

function LeagueStandings({ simId, onBack, onTeamClick, onError }: LeagueStandingsProps) {
  const [data, setData] = useState<StandingsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const s = await getStandings(simId);
        if (cancelled) return;
        setData(s);
        if (!s.is_complete) setTimeout(tick, 2000);
      } catch (e) {
        if (!cancelled) onError(String(e));
      }
    }
    tick();
    return () => { cancelled = true; };
  }, [simId, onError]);

  if (!data) return <div>Loading standings…</div>;

  return (
    <div className="league-standings-view">
      <div className="league-standings-header">
        <button onClick={onBack}>← All league simulations</button>
        <h2>{data.season} Standings</h2>
        {!data.is_complete && (
          <span className="provisional-badge">
            Provisional — {data.games_completed} / {data.total_games} games
          </span>
        )}
      </div>
      <table className="league-standings-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Team</th>
            <th>W</th>
            <th>L</th>
            <th>PCT</th>
            <th>GB</th>
          </tr>
        </thead>
        <tbody>
          {data.standings.map((row) => (
            <tr
              key={row.team_id}
              className="standings-row"
              onClick={() => onTeamClick(row.team_abbr)}
              style={{ cursor: "pointer" }}
            >
              <td>{row.rank}</td>
              <td>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <TeamLogo abbr={row.team_abbr} size="sm" />
                  <strong>{row.team_abbr}</strong>
                </span>
              </td>
              <td>{row.wins}</td>
              <td>{row.losses}</td>
              <td>{row.pct.toFixed(3)}</td>
              <td>{row.gb === 0 ? "—" : row.gb.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Team-in-league: the team's 82 games from this league sim
// ---------------------------------------------------------------------------

interface LeagueTeamGamesProps {
  simId: number;
  season: string;
  teamAbbr: string;
  onBack: () => void;
  onGameClick: (gameId: string) => void;
  onError: (msg: string) => void;
}

function LeagueTeamGames({
  simId, season, teamAbbr, onBack, onGameClick, onError,
}: LeagueTeamGamesProps) {
  const [games, setGames] = useState<SimulatedGameSummary[] | null>(null);

  useEffect(() => {
    getLeagueTeamGames(simId, teamAbbr)
      .then(setGames)
      .catch((e) => onError(String(e)));
  }, [simId, teamAbbr, onError]);

  if (!games) return <div>Loading {teamAbbr} games…</div>;

  const wins = games.filter((g) => g.win).length;
  const losses = games.length - wins;

  return (
    <div className="league-team-games">
      <div className="league-team-header">
        <button onClick={onBack}>← Standings</button>
        <TeamLogo abbr={teamAbbr} size="lg" season={season} />
        <h2>{teamAbbr} — {season}</h2>
        <span className="record">{wins}-{losses}</span>
      </div>
      <table className="league-games-table">
        <thead>
          <tr><th>Date</th><th>Matchup</th><th>Score</th><th>Result</th></tr>
        </thead>
        <tbody>
          {games.map((g) => (
            <tr
              key={g.game_id}
              className={`game-row ${g.win ? "win" : "loss"}`}
              onClick={() => onGameClick(g.game_id)}
              style={{ cursor: "pointer" }}
            >
              <td>{g.game_date}</td>
              <td>
                <TeamLogo abbr={g.away_team} size="sm" season={season} /> {g.away_team}
                {" @ "}
                <TeamLogo abbr={g.home_team} size="sm" season={season} /> {g.home_team}
              </td>
              <td>{g.away_score}–{g.home_score}{g.went_to_ot ? " OT" : ""}</td>
              <td className={g.win ? "win" : "loss"}>{g.win ? "W" : "L"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Game detail: reuse existing components
// ---------------------------------------------------------------------------

interface LeagueGameDetailProps {
  simId: number;
  gameId: string;
  onBack: () => void;
  onError: (msg: string) => void;
}

function LeagueGameDetail({ simId, gameId, onBack, onError }: LeagueGameDetailProps) {
  const [game, setGame] = useState<SimulateGameResponse | null>(null);

  useEffect(() => {
    getSeasonGame(simId, gameId)
      .then(setGame)
      .catch((e) => onError(String(e)));
  }, [simId, gameId, onError]);

  if (!game) return <div>Loading game…</div>;

  return (
    <div className="league-game-detail">
      <button onClick={onBack}>← Back to team games</button>
      <LineScore game={game} />
      <div className="game-boxscores">
        <BoxScore
          title={game.away_team}
          players={game.away_box}
          abbr={game.away_team}
          season={game.season}
          sideLabel="Away"
          onSelectPlayer={() => {}}
        />
        <BoxScore
          title={game.home_team}
          players={game.home_box}
          abbr={game.home_team}
          season={game.season}
          sideLabel="Home"
          onSelectPlayer={() => {}}
        />
      </div>
      {game.events && game.events.length > 0 && (
        <PlayByPlay game={game} />
      )}
    </div>
  );
}
