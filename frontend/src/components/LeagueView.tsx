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
import { useEffect, useState } from "react";
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
  StandingsRow,
} from "../types";
import LineScore from "./LineScore";
import BoxScore from "./BoxScore";
import PlayByPlay from "./PlayByPlay";
import TeamLogo from "./TeamLogo";
import GameContextHeader from "./GameContextHeader";
import TeamStandingsBlock from "./TeamStandingsBlock";
import PlayerModal from "./PlayerModal";
import { conferenceOf } from "../data/conferences";
import { franchiseFor } from "../data/franchises";
import { computeStandings, extendRow } from "../lib/teamStandings";
import type { PlayerLine } from "../types";

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
  const [runsLoading, setRunsLoading] = useState(true);
  const [season, setSeason] = useState("");
  const [seed, setSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Load seasons FIRST so the form is usable ASAP. Past-runs table loads
  // asynchronously — no blocking on the potentially-larger sim list.
  useEffect(() => {
    getSeasons()
      .then((s) => {
        setSeasons(s);
        if (s.length) setSeason((cur) => cur || s[0].season);
      })
      .catch((e) => onError(String(e)));
    listSimulations()
      .then((r) => setRuns(r.filter((x) => x.scope === "league")))
      .catch((e) => onError(String(e)))
      .finally(() => setRunsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

      {runsLoading && (
        <div className="league-runs-loading">
          <span className="spinner" aria-hidden="true" />
          Loading past simulations…
        </div>
      )}
      {!runsLoading && runs.length > 0 && (
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

  if (!status) return (
    <div className="league-running">
      <div className="league-running-loading">
        <span className="spinner" aria-hidden="true" />
        Loading simulation…
      </div>
    </div>
  );
  const pct = status.total_games > 0 ? (status.games_completed / status.total_games * 100) : 0;
  const eta = status.status === "running" && status.games_completed > 0
    ? Math.round((status.total_games - status.games_completed) * 0.02)
    : null;

  return (
    <div className="league-running">
      <div className="league-running-card">
        <div className="league-running-header">
          <h2>Simulating {status.season}</h2>
          <span className="league-running-status" data-status={status.status}>
            {status.status}
          </span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="league-running-meta">
          <span className="lrm-primary">
            {status.games_completed.toLocaleString()} / {status.total_games.toLocaleString()} games
          </span>
          <span className="lrm-pct">{pct.toFixed(1)}%</span>
          {eta !== null && (
            <span className="lrm-eta">~{eta}s remaining</span>
          )}
        </div>
        {status.status === "running" && (
          <button className="cancel-btn" onClick={handleCancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel simulation"}
          </button>
        )}
        {status.status === "cancelled" && (
          <div className="league-running-done">
            <p>Simulation cancelled.</p>
            <button className="back-btn" onClick={onCancel}>← Back</button>
          </div>
        )}
        {status.status === "failed" && (
          <div className="league-running-done">
            <p>Simulation failed.</p>
            <button className="back-btn" onClick={onCancel}>← Back</button>
          </div>
        )}
      </div>
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

  // Split by conference. Recompute per-conference rank + GB (each conference
  // has its own leader). Unknown-conference teams fall through to an "Other"
  // section — only relevant if the DB ever contains a team not in the current
  // East/West alignment.
  const conferences = ["East", "West"] as const;
  const byConf: Record<string, StandingsRow[]> = { East: [], West: [], Other: [] };
  for (const row of data.standings) {
    const conf = conferenceOf(row.team_abbr) ?? "Other";
    byConf[conf].push(row);
  }
  // Rerank within each conference and recompute GB relative to conf leader.
  function rerank(rows: StandingsRow[]): StandingsRow[] {
    if (rows.length === 0) return rows;
    // Already ordered by the backend's global sort; splitting preserves order.
    const leader = rows[0];
    return rows.map((r, i) => ({
      ...r,
      rank: i + 1,
      gb: Math.round(
        ((leader.wins - r.wins) + (r.losses - leader.losses)) / 2 * 10
      ) / 10,
    }));
  }
  const eastRows = rerank(byConf.East);
  const westRows = rerank(byConf.West);

  return (
    <div className="league-standings-view">
      <div className="league-standings-header">
        <button className="back-btn" onClick={onBack}>← All league simulations</button>
        <h2>{data.season} Standings</h2>
        {!data.is_complete && (
          <span className="provisional-badge">
            Provisional — {data.games_completed} / {data.total_games} games
          </span>
        )}
      </div>

      <div className="league-standings-conferences" style={{
        display: "grid", gap: "1.5rem",
        gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
      }}>
        {conferences.map((conf) => (
          <ConferenceTable
            key={conf}
            title={`${conf}ern Conference`}
            rows={conf === "East" ? eastRows : westRows}
            onTeamClick={onTeamClick}
            season={data.season}
          />
        ))}
      </div>

      {byConf.Other.length > 0 && (
        <ConferenceTable
          title="Other / Unaligned"
          rows={rerank(byConf.Other)}
          onTeamClick={onTeamClick}
          season={data.season}
        />
      )}
    </div>
  );
}


function ConferenceTable({
  title, rows, onTeamClick, season,
}: {
  title: string;
  rows: StandingsRow[];
  onTeamClick: (abbr: string) => void;
  season?: string;
}) {
  return (
    <div className="conference-panel">
      <h3 className="conference-title">{title}</h3>
      <table className="league-standings-table">
        <thead>
          <tr>
            <th className="col-rank">#</th>
            <th className="col-team">Team</th>
            <th className="col-num">W</th>
            <th className="col-num">L</th>
            <th className="col-num">PCT</th>
            <th className="col-num">GB</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const fr = franchiseFor(row.team_abbr, season);
            const accent = fr?.primaryColor || "transparent";
            return (
              <tr
                key={row.team_id}
                className={`standings-row ${i % 2 === 0 ? "even" : "odd"}`}
                onClick={() => onTeamClick(row.team_abbr)}
                style={{ ["--team-accent" as string]: accent } as React.CSSProperties}
              >
                <td className="col-rank">{row.rank}</td>
                <td className="col-team">
                  <span className="team-cell">
                    <TeamLogo abbr={row.team_abbr} size="sm" season={season} />
                    <span className="team-names">
                      <strong className="team-abbr">{row.team_abbr}</strong>
                      {fr?.fullName && (
                        <span className="team-full">{fr.fullName}</span>
                      )}
                    </span>
                  </span>
                </td>
                <td className="col-num">{row.wins}</td>
                <td className="col-num">{row.losses}</td>
                <td className="col-num">{row.pct.toFixed(3)}</td>
                <td className="col-num">{row.gb === 0 ? "—" : row.gb.toFixed(1)}</td>
              </tr>
            );
          })}
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

  const rows = games.map((g) => extendRow(g, teamAbbr));
  const standings = computeStandings(rows);
  const wins = standings.w;
  const losses = standings.l;
  const fr = franchiseFor(teamAbbr, season);
  const accent = fr?.primaryColor || "transparent";

  return (
    <div
      className="league-team-games"
      style={{ ["--team-accent" as string]: accent } as React.CSSProperties}
    >
      <div className="league-team-header">
        <button className="back-btn" onClick={onBack}>← Standings</button>
        <div className="league-team-hero">
          <TeamLogo abbr={teamAbbr} size="lg" season={season} />
          <div className="league-team-heading">
            <h2>{fr?.fullName || teamAbbr}</h2>
            <div className="league-team-meta">
              <span className="record">{wins}-{losses}</span>
              <span className="dot">·</span>
              <span>{season}</span>
            </div>
          </div>
        </div>
      </div>
      <TeamStandingsBlock standings={standings} />
      <table className="league-games-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Matchup</th>
            <th className="col-num">Score</th>
            <th className="col-result">Result</th>
          </tr>
        </thead>
        <tbody>
          {games.map((g) => (
            <tr
              key={g.game_id}
              className={`game-row ${g.win ? "win" : "loss"}`}
              onClick={() => onGameClick(g.game_id)}
            >
              <td className="col-date">{g.game_date}</td>
              <td className="col-matchup">
                <span className="matchup-team">
                  <TeamLogo abbr={g.away_team} size="sm" season={season} />
                  <span>{g.away_team}</span>
                </span>
                <span className="at">@</span>
                <span className="matchup-team">
                  <TeamLogo abbr={g.home_team} size="sm" season={season} />
                  <span>{g.home_team}</span>
                </span>
              </td>
              <td className="col-num">
                {g.away_score}–{g.home_score}
                {g.went_to_ot && <span className="ot-chip">OT</span>}
              </td>
              <td className="col-result">
                <span className={`wl-chip ${g.win ? "win" : "loss"}`}>
                  {g.win ? "W" : "L"}
                </span>
              </td>
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
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerLine | null>(null);

  useEffect(() => {
    getSeasonGame(simId, gameId)
      .then(setGame)
      .catch((e) => onError(String(e)));
  }, [simId, gameId, onError]);

  if (!game) return <div>Loading game…</div>;

  return (
    <div className="league-game-detail">
      <button className="back-btn" onClick={onBack}>← Back to team games</button>
      <GameContextHeader game={game} />
      <LineScore game={game} />
      <div className="boxes">
        <BoxScore
          title={game.away_team}
          players={game.away_box}
          abbr={game.away_team}
          season={game.season}
          sideLabel="Away"
          onSelectPlayer={setSelectedPlayer}
        />
        <BoxScore
          title={game.home_team}
          players={game.home_box}
          abbr={game.home_team}
          season={game.season}
          sideLabel="Home"
          onSelectPlayer={setSelectedPlayer}
        />
      </div>
      {game.events && game.events.length > 0 && (
        <PlayByPlay game={game} />
      )}
      {selectedPlayer && (
        <PlayerModal
          key={selectedPlayer.player_id}
          line={selectedPlayer}
          season={game.season}
          events={game.events ?? []}
          onClose={() => setSelectedPlayer(null)}
        />
      )}
    </div>
  );
}
