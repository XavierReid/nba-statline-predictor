/**
 * MyLeagueView (M-1c) — minimal franchise-mode surface.
 *
 * Flow:
 *   picker → dashboard
 *
 * Picker: pick season + (optional) controlled team + (optional) seed → POST
 * /myleague/. No "resume existing" list yet (no list endpoint — a follow-up
 * if the URL-copy workflow proves annoying).
 *
 * Dashboard: current-state header + Advance Day button + recent games
 * + standings (using existing ConferenceTable-style layout).
 *
 * Explicitly NOT this session:
 *   - Roster editing UI
 *   - Availability toggle UI (SET_UNAVAILABLE) — endpoint exists, no UI
 *   - Per-team drill-in
 *   - Injury / trade UI
 */
import { useEffect, useState } from "react";
import {
  advanceMyLeague,
  createMyLeague,
  getMyLeague,
  getSeasons,
  getTeams,
} from "../api";
import type {
  MyLeagueSummary,
  SeasonCoverage,
  Team,
} from "../types";
import TeamLogo from "./TeamLogo";
import { franchiseFor } from "../data/franchises";

type View =
  | { kind: "picker" }
  | { kind: "creating" }
  | { kind: "dashboard"; simId: number };

export default function MyLeagueView() {
  const [view, setView] = useState<View>({ kind: "picker" });
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="myleague-view">
      {error && <div className="error">{error}</div>}
      {view.kind === "picker" && (
        <MyLeaguePicker
          onCreated={(simId) => { setError(null); setView({ kind: "dashboard", simId }); }}
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
// Picker
// ---------------------------------------------------------------------------

interface PickerProps {
  onCreated: (simId: number) => void;
  onError: (msg: string) => void;
}

function MyLeaguePicker({ onCreated, onError }: PickerProps) {
  const [seasons, setSeasons] = useState<SeasonCoverage[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [season, setSeason] = useState("");
  const [teamId, setTeamId] = useState<string>("");   // "" = God mode
  const [seed, setSeed] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    getSeasons()
      .then((s) => {
        setSeasons(s);
        if (s.length) setSeason((cur) => cur || s[0].season);
      })
      .catch((e) => onError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!season) return;
    getTeams(season)
      .then(setTeams)
      .catch((e) => onError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [season]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!season || submitting) return;
    setSubmitting(true);
    try {
      const created = await createMyLeague({
        season,
        seed: seed === "" ? null : Number(seed),
        controlled_team_id: teamId === "" ? null : Number(teamId),
      });
      onCreated(created.simulation_id);
    } catch (err) {
      onError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="myleague-picker">
      <h2>Start a MyLeague run</h2>
      <p className="subtitle">
        Advance the season one day at a time. Between games you can (soon)
        manage rosters, availability, injuries, and trades — for now, it's
        watch-the-season-unfold.
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
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
            <option value="">— none (spectate) —</option>
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
        <button type="submit" disabled={!season || submitting}>
          {submitting ? "Creating…" : "Start MyLeague"}
        </button>
      </form>
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

  const refresh = () => {
    getMyLeague(simId).then(setSummary).catch((e) => onError(String(e)));
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [simId]);

  async function advanceOneDay() {
    if (!summary || advancing) return;
    setAdvancing(true);
    try {
      // Compute next-day ISO from the current cursor.
      const [y, m, d] = summary.state.current_calendar_date.split("-").map(Number);
      const next = new Date(y, m - 1, d);
      next.setDate(next.getDate() + 1);
      const nextIso = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
      await advanceMyLeague(simId, nextIso);
      refresh();
    } catch (err) {
      onError(String(err));
    } finally {
      setAdvancing(false);
    }
  }

  if (!summary) return <div className="empty-hint">Loading MyLeague…</div>;

  const { state, standings, recent_games } = summary;
  const controlledTeamRow = state.controlled_team_id
    ? standings.find((s) => s.team_id === state.controlled_team_id)
    : null;
  const fr = controlledTeamRow ? franchiseFor(controlledTeamRow.team_abbr, state.season) : null;

  return (
    <div className="myleague-dashboard">
      <div className="myleague-header">
        <button className="back-btn" onClick={onExit}>← Exit</button>
        <div className="myleague-hero"
          style={fr ? ({ ["--team-accent" as string]: fr.primaryColor } as React.CSSProperties) : undefined}>
          {controlledTeamRow ? (
            <>
              <TeamLogo abbr={controlledTeamRow.team_abbr} size="lg" season={state.season} />
              <div className="myleague-heading">
                <h2>{fr?.fullName || controlledTeamRow.team_abbr}</h2>
                <div className="myleague-meta">
                  <span className="record">{controlledTeamRow.wins}-{controlledTeamRow.losses}</span>
                  <span className="dot">·</span>
                  <span>{state.season}</span>
                  <span className="dot">·</span>
                  <span>{state.current_calendar_date}</span>
                  <span className="dot">·</span>
                  <span>{state.games_completed} team games played</span>
                </div>
              </div>
            </>
          ) : (
            <div className="myleague-heading">
              <h2>MyLeague — {state.season}</h2>
              <div className="myleague-meta">
                <span>Spectate mode</span>
                <span className="dot">·</span>
                <span>{state.current_calendar_date}</span>
                <span className="dot">·</span>
                <span>{state.games_completed} games played</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="myleague-advance">
        <button className="new-sim-btn" onClick={advanceOneDay} disabled={advancing}>
          {advancing ? "Advancing…" : "Advance one day →"}
        </button>
      </div>

      <div className="myleague-columns">
        <section className="myleague-recent">
          <h3>Recent results</h3>
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
                      {g.away_score}–{g.home_score}
                      {g.went_to_ot && <span className="ot-chip">OT</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="myleague-standings">
          <h3>Standings</h3>
          {standings.length === 0 ? (
            <p className="empty-hint">No standings yet.</p>
          ) : (
            <table className="league-standings-table">
              <thead>
                <tr>
                  <th className="col-rank">#</th>
                  <th className="col-team">Team</th>
                  <th className="col-num">W</th>
                  <th className="col-num">L</th>
                  <th className="col-num">PCT</th>
                </tr>
              </thead>
              <tbody>
                {standings.slice(0, 30).map((row, i) => {
                  const rowFr = franchiseFor(row.team_abbr, state.season);
                  const accent = rowFr?.primaryColor || "transparent";
                  const isControlled = state.controlled_team_id === row.team_id;
                  return (
                    <tr
                      key={row.team_id}
                      className={`standings-row ${i % 2 === 0 ? "even" : "odd"} ${isControlled ? "controlled" : ""}`}
                      style={{ ["--team-accent" as string]: accent } as React.CSSProperties}
                    >
                      <td className="col-rank">{row.rank}</td>
                      <td className="col-team">
                        <span className="team-cell">
                          <TeamLogo abbr={row.team_abbr} size="sm" season={state.season} />
                          <strong className="team-abbr">{row.team_abbr}</strong>
                        </span>
                      </td>
                      <td className="col-num">{row.wins}</td>
                      <td className="col-num">{row.losses}</td>
                      <td className="col-num">{row.pct.toFixed(3)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
