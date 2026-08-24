/**
 * MyLeagueView — franchise-mode surface.
 *
 * Flow:
 *   picker (create new + list past runs) → dashboard
 *
 * Dashboard:
 *   - hero (controlled team logo/name/date/W-L/games played)
 *   - Advance one day button (disabled + "Season complete!" banner when done)
 *   - two-column: recent games (with controlled-team highlight) + standings
 *     (East/West split, controlled team highlighted with neutral accent)
 */
import { useEffect, useState } from "react";
import {
  advanceMyLeague,
  createMyLeague,
  deleteSimulation,
  getMyLeague,
  getSeasons,
  getTeams,
  listSimulations,
} from "../api";
import type {
  MyLeagueSummary,
  NextGamePreview,
  SeasonCoverage,
  SimulationSummary,
  StandingsRow,
  Team,
} from "../types";
import TeamLogo from "./TeamLogo";
import { franchiseFor } from "../data/franchises";
import { conferenceOf } from "../data/conferences";

type View =
  | { kind: "picker" }
  | { kind: "dashboard"; simId: number };

export default function MyLeagueView() {
  const [view, setView] = useState<View>({ kind: "picker" });
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="myleague-view">
      {error && <div className="error">{error}</div>}
      {view.kind === "picker" && (
        <MyLeaguePicker
          onOpened={(simId) => { setError(null); setView({ kind: "dashboard", simId }); }}
          onError={setError}
        />
      )}
      {view.kind === "dashboard" && (
        <MyLeagueDashboard
          simId={view.simId}
          onError={setError}
          onExit={() => setView({ kind: "picker" })}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Picker — create-new form + past-runs table
// ---------------------------------------------------------------------------

interface PickerProps {
  onOpened: (simId: number) => void;
  onError: (msg: string) => void;
}

function MyLeaguePicker({ onOpened, onError }: PickerProps) {
  const [seasons, setSeasons] = useState<SeasonCoverage[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [season, setSeason] = useState("");
  const [teamId, setTeamId] = useState<string>("");
  const [seed, setSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [runs, setRuns] = useState<SimulationSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);

  const refreshRuns = () => {
    setRunsLoading(true);
    listSimulations()
      .then((r) => setRuns(r.filter((x) => x.scope === "myleague")))
      .catch((e) => onError(String(e)))
      .finally(() => setRunsLoading(false));
  };

  useEffect(() => {
    getSeasons()
      .then((s) => {
        setSeasons(s);
        if (s.length) setSeason((cur) => cur || s[0].season);
      })
      .catch((e) => onError(String(e)));
    refreshRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!season) return;
    getTeams(season)
      .then((ts) => {
        setTeams(ts);
        setTeamId((cur) => (ts.some((t) => String(t.id) === cur) ? cur : ""));
      })
      .catch((e) => onError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [season]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!season || !teamId || submitting) return;
    setSubmitting(true);
    try {
      const created = await createMyLeague({
        season,
        seed: seed === "" ? null : Number(seed),
        controlled_team_id: Number(teamId),
      });
      onOpened(created.simulation_id);
    } catch (err) {
      onError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onDelete(simId: number) {
    if (!confirm(`Delete MyLeague run #${simId}? This cannot be undone.`)) return;
    try {
      await deleteSimulation(simId);
      refreshRuns();
    } catch (err) {
      onError(String(err));
    }
  }

  return (
    <div className="myleague-picker">
      <h2>Start a MyLeague run</h2>
      <p className="subtitle">
        Pick a team to manage and advance the season one day at a time.
        Between games you can (soon) manage rosters, availability, injuries,
        and trades. To just watch a season play out, use Season Sim →
        Full League instead.
      </p>
      <form onSubmit={onSubmit} className="myleague-form">
        <label>
          Season
          <select value={season} onChange={(e) => setSeason(e.target.value)}>
            {seasons.map((s) => (
              <option key={s.season} value={s.season}>{s.season}</option>
            ))}
          </select>
        </label>
        <label>
          Controlled team
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)} required>
            <option value="">— pick a team —</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>{t.abbreviation} — {t.city} {t.nickname}</option>
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
        <button type="submit" disabled={!season || !teamId || submitting}>
          {submitting ? "Creating…" : "Start MyLeague"}
        </button>
      </form>

      {runsLoading && (
        <div className="league-runs-loading">
          <span className="spinner" aria-hidden="true" />
          Loading past MyLeague runs…
        </div>
      )}
      {!runsLoading && runs.length > 0 && (
        <div className="league-runs">
          <h3>Past MyLeague runs</h3>
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
                  <td className="myleague-runs-actions">
                    <button onClick={() => onOpened(r.id)}>Continue</button>
                    <button className="danger" onClick={() => onDelete(r.id)}>Delete</button>
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
// Dashboard
// ---------------------------------------------------------------------------

interface DashboardProps {
  simId: number;
  onError: (msg: string) => void;
  onExit: () => void;
}

function MyLeagueDashboard({ simId, onError, onExit }: DashboardProps) {
  const [summary, setSummary] = useState<MyLeagueSummary | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const [lastAdvance, setLastAdvance] = useState<string | null>(null);

  const refresh = () => {
    getMyLeague(simId).then(setSummary).catch((e) => onError(String(e)));
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [simId]);

  async function advanceOneDay() {
    if (!summary || advancing) return;
    setAdvancing(true);
    const gamesBefore = summary.state.games_completed;
    try {
      const [y, m, d] = summary.state.current_calendar_date.split("-").map(Number);
      const next = new Date(y, m - 1, d);
      next.setDate(next.getDate() + 1);
      const nextIso = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
      const newState = await advanceMyLeague(simId, nextIso);
      const played = newState.games_completed - gamesBefore;
      setLastAdvance(
        played > 0
          ? `Advanced to ${nextIso} — ${played} game${played === 1 ? "" : "s"} played`
          : `Advanced to ${nextIso} — off-day, no games`
      );
      refresh();
    } catch (err) {
      onError(String(err));
    } finally {
      setAdvancing(false);
    }
  }

  if (!summary) return <div className="empty-hint">Loading MyLeague…</div>;

  const { state, standings, recent_games, upcoming_games, next_game_preview } = summary;
  const seasonComplete = state.status === "complete";
  const controlledAbbr = state.controlled_team_abbr;
  const controlledStanding = state.controlled_team_id
    ? standings.find((s) => s.team_id === state.controlled_team_id)
    : null;
  const wins = controlledStanding?.wins ?? 0;
  const losses = controlledStanding?.losses ?? 0;
  const fr = controlledAbbr ? franchiseFor(controlledAbbr, state.season) : null;

  // Recent games shown for the controlled team get a subtle highlight.
  const isControlledGame = (homeAbbr: string, awayAbbr: string) =>
    !!controlledAbbr && (homeAbbr === controlledAbbr || awayAbbr === controlledAbbr);

  return (
    <div className="myleague-dashboard">
      <div className="myleague-header">
        <button className="back-btn" onClick={onExit}>← Exit</button>
        <div className="myleague-hero"
          style={fr ? ({ ["--team-accent" as string]: fr.primaryColor } as React.CSSProperties) : undefined}>
          {controlledAbbr ? (
            <>
              <TeamLogo abbr={controlledAbbr} size="lg" season={state.season} />
              <div className="myleague-heading">
                <h2>{fr?.fullName || controlledAbbr}</h2>
                <div className="myleague-meta">
                  <span className="record">{wins}-{losses}</span>
                  <span className="dot">·</span>
                  <span>{state.season}</span>
                  <span className="dot">·</span>
                  <span>{state.current_calendar_date}</span>
                  <span className="dot">·</span>
                  <span>{state.games_completed} / {state.total_games} league games played</span>
                </div>
              </div>
            </>
          ) : (
            <div className="myleague-heading">
              <h2>MyLeague — {state.season}</h2>
              <div className="myleague-meta">
                <span>{state.current_calendar_date}</span>
                <span className="dot">·</span>
                <span>{state.games_completed} / {state.total_games} games played</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {seasonComplete && (
        <div className="myleague-season-complete">
          🏆 Season complete — {state.games_completed} games played.
          Final standings below.
        </div>
      )}

      {!seasonComplete && (
        <div className="myleague-advance">
          {lastAdvance && !advancing && (
            <span className="myleague-advance-toast">{lastAdvance}</span>
          )}
          <button className="new-sim-btn" onClick={advanceOneDay} disabled={advancing}>
            {advancing ? "Advancing…" : "Advance one day →"}
          </button>
        </div>
      )}

      {!seasonComplete && next_game_preview && (
        <NextGameCard preview={next_game_preview} controlledAbbr={controlledAbbr!} season={state.season} cursor={state.current_calendar_date} />
      )}

      <div className="myleague-columns">
        <div className="myleague-left-col">
        <section className="myleague-recent">
          <h3>{seasonComplete ? "Final games" : "Recent results"}</h3>
          {recent_games.length === 0 ? (
            <p className="empty-hint">No games completed yet. Advance a day to start.</p>
          ) : (
            <table className="myleague-recent-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Matchup</th>
                  <th className="col-num">Score</th>
                </tr>
              </thead>
              <tbody>
                {recent_games.map((g) => (
                  <tr
                    key={g.game_id}
                    className={isControlledGame(g.home_team, g.away_team) ? "controlled" : ""}
                  >
                    <td className="col-date">{g.game_date}</td>
                    <td className="col-matchup">
                      <span className="matchup-team">
                        <TeamLogo abbr={g.away_team} size="sm" season={state.season} />
                        <span>{g.away_team}</span>
                      </span>
                      <span className="at">@</span>
                      <span className="matchup-team">
                        <TeamLogo abbr={g.home_team} size="sm" season={state.season} />
                        <span>{g.home_team}</span>
                      </span>
                    </td>
                    <td className="col-num">
                      {g.away_score}–{g.home_score}
                      {g.went_to_ot && <span className="ot-chip">OT</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {!seasonComplete && controlledAbbr && (() => {
          // Drop the game promoted into NextGameCard so it doesn't appear twice.
          const promotedId = next_game_preview?.game_id;
          const rows = upcoming_games.filter((g) => g.game_id !== promotedId);
          if (rows.length === 0) return null;
          return (
          <section className="myleague-upcoming">
            <h3>{next_game_preview ? "After that" : "Upcoming"} — {controlledAbbr}</h3>
            <table className="myleague-recent-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Matchup</th>
                  <th className="col-num">In</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((g) => {
                  const dayDiff = daysBetween(state.current_calendar_date, g.game_date);
                  return (
                    <tr key={g.game_id}>
                      <td className="col-date">{g.game_date}</td>
                      <td className="col-matchup">
                        <span className="matchup-team">
                          <TeamLogo abbr={g.away_team} size="sm" season={state.season} />
                          <span>{g.away_team}</span>
                        </span>
                        <span className="at">@</span>
                        <span className="matchup-team">
                          <TeamLogo abbr={g.home_team} size="sm" season={state.season} />
                          <span>{g.home_team}</span>
                        </span>
                      </td>
                      <td className="col-num">
                        {dayDiff === 1 ? "tomorrow" : `${dayDiff}d`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          );
        })()}
        </div>

        <section className="myleague-standings">
          <h3>{seasonComplete ? "Final standings" : "Standings"}</h3>
          {standings.length === 0 ? (
            <p className="empty-hint">No standings yet.</p>
          ) : (
            <SplitStandings
              standings={standings}
              season={state.season}
              controlledTeamId={state.controlled_team_id}
            />
          )}
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SplitStandings — East/West conference split, stacked in the right column
// ---------------------------------------------------------------------------

function SplitStandings({
  standings, season, controlledTeamId,
}: {
  standings: StandingsRow[];
  season: string;
  controlledTeamId: number | null;
}) {
  const east: StandingsRow[] = [];
  const west: StandingsRow[] = [];
  const other: StandingsRow[] = [];
  for (const row of standings) {
    const conf = conferenceOf(row.team_abbr);
    if (conf === "East") east.push(row);
    else if (conf === "West") west.push(row);
    else other.push(row);
  }
  // Rerank per conference + recompute GB relative to the conf leader
  // (mirrors LeagueView's ConferenceTable logic).
  const rerank = (rows: StandingsRow[]): StandingsRow[] => {
    if (rows.length === 0) return rows;
    const leader = rows[0];
    return rows.map((r, i) => ({
      ...r,
      rank: i + 1,
      gb: Math.round(
        ((leader.wins - r.wins) + (r.losses - leader.losses)) / 2 * 10
      ) / 10,
    }));
  };
  return (
    <div className="myleague-standings-conferences">
      <MiniConferenceTable title="East" rows={rerank(east)} season={season} controlledTeamId={controlledTeamId} />
      <MiniConferenceTable title="West" rows={rerank(west)} season={season} controlledTeamId={controlledTeamId} />
      {other.length > 0 && (
        <MiniConferenceTable
          title="Other" rows={rerank(other)} season={season} controlledTeamId={controlledTeamId}
        />
      )}
    </div>
  );
}

function MiniConferenceTable({
  title, rows, season, controlledTeamId,
}: {
  title: string;
  rows: StandingsRow[];
  season: string;
  controlledTeamId: number | null;
}) {
  return (
    <div className="mini-conf">
      <h4>{title}</h4>
      <table className="league-standings-table">
        <thead>
          <tr>
            <th className="col-rank">#</th>
            <th className="col-team">Team</th>
            <th className="col-num">W</th>
            <th className="col-num">L</th>
            <th className="col-num">PCT</th>
            <th className="col-num">STRK</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const isControlled = controlledTeamId === row.team_id;
            return (
              <tr
                key={row.team_id}
                className={`standings-row ${i % 2 === 0 ? "even" : "odd"} ${isControlled ? "controlled-neutral" : ""}`}
              >
                <td className="col-rank">{row.rank}</td>
                <td className="col-team">
                  <span className="team-cell">
                    <TeamLogo abbr={row.team_abbr} size="sm" season={season} />
                    <strong className="team-abbr">{row.team_abbr}</strong>
                  </span>
                </td>
                <td className="col-num">{row.wins}</td>
                <td className="col-num">{row.losses}</td>
                <td className="col-num">{row.pct.toFixed(3)}</td>
                <td className={`col-num streak-${row.streak.charAt(0).toLowerCase()}`}>{row.streak}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// daysBetween(cursorISO, targetISO) — small helper for the upcoming-games
// "in Nd / tomorrow" hint. Positive integer for target > cursor; 0 for same
// day (shouldn't happen since upcoming filters game_date > cursor).
function daysBetween(fromIso: string, toIso: string): number {
  const [fy, fm, fd] = fromIso.split("-").map(Number);
  const [ty, tm, td] = toIso.split("-").map(Number);
  const from = new Date(fy, fm - 1, fd).getTime();
  const to = new Date(ty, tm - 1, td).getTime();
  return Math.round((to - from) / (1000 * 60 * 60 * 24));
}

// ---------------------------------------------------------------------------
// NextGameCard — full-width pre-game preview card for the controlled team's
// next scheduled game. Two side-by-side rotation panels (controlled + opponent)
// with matchup/series context in the header.
// ---------------------------------------------------------------------------

function NextGameCard({
  preview, controlledAbbr, season, cursor,
}: {
  preview: NextGamePreview;
  controlledAbbr: string;
  season: string;
  cursor: string;
}) {
  const days = daysBetween(cursor, preview.game_date);
  const relative =
    days === 0 ? "today"
    : days === 1 ? "tomorrow"
    : `in ${days} days`;
  const ctrlFr = franchiseFor(controlledAbbr, season);
  const oppFr = franchiseFor(preview.opponent_abbr, season);
  const location = preview.is_home ? "vs" : "@";
  const seriesLabel = preview.matchup_total > 1
    ? `${ordinal(preview.matchup_index)} of ${preview.matchup_total} meetings · series ${preview.series_wins_controlled}-${preview.series_wins_opponent}`
    : "First meeting";

  return (
    <div className="myleague-next-card">
      <div className="myleague-next-header">
        <span className="myleague-next-eyebrow">Next game · {relative}</span>
        <div className="myleague-next-matchup">
          <span
            className="myleague-next-team"
            style={ctrlFr ? ({ ["--team-accent" as string]: ctrlFr.primaryColor } as React.CSSProperties) : undefined}
          >
            <TeamLogo abbr={controlledAbbr} size="lg" season={season} />
            <span className="myleague-next-team-name">{ctrlFr?.fullName || controlledAbbr}</span>
          </span>
          <span className="myleague-next-vs">{location}</span>
          <span
            className="myleague-next-team"
            style={oppFr ? ({ ["--team-accent" as string]: oppFr.primaryColor } as React.CSSProperties) : undefined}
          >
            <TeamLogo abbr={preview.opponent_abbr} size="lg" season={season} />
            <span className="myleague-next-team-name">{oppFr?.fullName || preview.opponent_abbr}</span>
          </span>
        </div>
        <div className="myleague-next-meta">
          <span>{preview.game_date}</span>
          <span className="dot">·</span>
          <span>{seriesLabel}</span>
        </div>
      </div>
      <div className="myleague-next-rosters">
        <RosterList title={controlledAbbr} players={preview.controlled_roster} />
        <RosterList title={preview.opponent_abbr} players={preview.opponent_roster} />
      </div>
    </div>
  );
}

function RosterList({ title, players }: { title: string; players: NextGamePreview["controlled_roster"] }) {
  return (
    <div className="myleague-next-roster">
      <h4>{title} rotation</h4>
      <table>
        <tbody>
          {players.map((p) => (
            <tr key={p.player_id} className={p.is_starter ? "starter" : ""}>
              <td className="pos">{p.position}</td>
              <td className="name">{p.name}</td>
              <td className="mpg">{p.mpg.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
