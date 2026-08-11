import { useCallback, useEffect, useMemo, useState } from "react";
import {
  cancelSimulation,
  createSimulation,
  deleteSimulation,
  getSeasonGame,
  getSeasons,
  getSimulation,
  getTeams,
  listSimulations,
  startSimulation,
} from "../api";
import type {
  PlayerLine,
  SeasonCoverage,
  SimulateGameResponse,
  SimulatedGameSummary,
  SimulationStatus,
  SimulationSummary,
  Team,
} from "../types";
import { franchiseFor } from "../data/franchises";
import { readableOnDark } from "../data/color";
import LineScore from "./LineScore";
import BoxScore from "./BoxScore";
import PlayByPlay from "./PlayByPlay";
import PlayerModal from "./PlayerModal";
import TeamLogo from "./TeamLogo";

const POLL_INTERVAL_MS = 1000;
const PRESETS = ["drama-m3", "drama-m3-season", "drama-m3-no-subtypes", "baseline"];
type SortKey = "date" | "opponent" | "score" | "margin" | "result";
type SortDir = "asc" | "desc";

interface RowExt extends SimulatedGameSummary {
  opponent: string;
  margin: number;
  teamScore: number;
  oppScore: number;
  isHome: boolean;
}

function extendRow(g: SimulatedGameSummary, teamAbbr: string): RowExt {
  const isHome = g.home_team === teamAbbr;
  const teamScore = isHome ? g.home_score : g.away_score;
  const oppScore = isHome ? g.away_score : g.home_score;
  return {
    ...g,
    opponent: isHome ? g.away_team : g.home_team,
    teamScore,
    oppScore,
    margin: teamScore - oppScore,
    isHome,
  };
}

function computeStandings(rows: RowExt[]) {
  const n = rows.length || 1;
  const w = rows.filter((r) => r.win).length;
  const home = rows.filter((r) => r.isHome);
  const away = rows.filter((r) => !r.isHome);
  const scored = rows.reduce((s, r) => s + r.teamScore, 0) / n;
  const allowed = rows.reduce((s, r) => s + r.oppScore, 0) / n;
  const blowouts = rows.filter((r) => Math.abs(r.margin) >= 20).length;
  const otGames = rows.filter((r) => r.went_to_ot).length;
  return {
    gp: rows.length,
    w,
    l: rows.length - w,
    wPct: rows.length ? w / rows.length : 0,
    homeW: home.filter((r) => r.win).length,
    homeL: home.length - home.filter((r) => r.win).length,
    awayW: away.filter((r) => r.win).length,
    awayL: away.length - away.filter((r) => r.win).length,
    ppgScored: scored,
    ppgAllowed: allowed,
    blowoutRate: rows.length ? blowouts / rows.length : 0,
    otRate: rows.length ? otGames / rows.length : 0,
  };
}

// --- New-sim form ---------------------------------------------------------

interface FormPrefill {
  team?: string;
  season?: string;
  seed?: number | null;
  preset?: string;
}

interface NewSeasonFormProps {
  onCreated: (id: number) => void;
  onError: (msg: string) => void;
  onDetectedActive: (id: number) => void;    // 409 recovery: jump into the active run
  prefill?: FormPrefill;
}

function NewSeasonForm({ onCreated, onError, onDetectedActive, prefill }: NewSeasonFormProps) {
  const [seasons, setSeasons] = useState<SeasonCoverage[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [season, setSeason] = useState(prefill?.season ?? "");
  const [team, setTeam] = useState(prefill?.team ?? "");
  const [seed, setSeed] = useState(prefill?.seed != null ? String(prefill.seed) : "");
  const [preset, setPreset] = useState(prefill?.preset ?? "drama-m3");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    getSeasons()
      .then((s) => {
        setSeasons(s);
        // Only default to the newest season if the user hasn't pre-selected one
        // (prefill from a re-run action).
        if (s.length && !season) setSeason(s[0].season);
      })
      .catch((e) => onError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onError]);

  useEffect(() => {
    if (!season) return;
    getTeams(season)
      .then((t) => {
        setTeams(t);
        if (t.length && !t.some((x) => x.abbreviation === team)) {
          setTeam(t[0].abbreviation);
        }
      })
      .catch((e) => onError(String(e)));
  }, [season, team, onError]);

  const short = (s: string) => (s.split("-")[1] ? `'${s.split("-")[1]}` : s);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!team || !season || submitting) return;
    setSubmitting(true);
    try {
      const created = await createSimulation({
        team,
        season,
        seed: seed === "" ? null : Number(seed),
        config: { preset },
      });
      await startSimulation(created.id);
      onCreated(created.id);
    } catch (err) {
      const e = err as Error & { status?: number };
      if (e.status === 409) {
        // Another run is already active — recover by finding it.
        try {
          const runs = await listSimulations();
          const active = runs.find((r) => r.status === "running" || r.status === "pending");
          if (active) {
            onDetectedActive(active.id);
            return;
          }
        } catch { /* fall through to error */ }
      }
      onError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = team && season && !submitting;

  return (
    <form className="new-season-form" onSubmit={onSubmit}>
      <div className="field season-timeline-field">
        <label>Season</label>
        <div className="season-timeline" role="radiogroup" aria-label="Season">
          {seasons.map((s) => (
            <button
              key={s.season}
              type="button"
              role="radio"
              aria-checked={season === s.season}
              className={`season-pill ${season === s.season ? "on" : ""}`}
              title={s.season}
              onClick={(ev) => {
                setSeason(s.season);
                (ev.currentTarget as HTMLElement).scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
              }}
            >
              {short(s.season)}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>Team</label>
        <select value={team} onChange={(e) => setTeam(e.target.value)}>
          {teams.map((t) => (
            <option key={t.id} value={t.abbreviation}>{t.city} {t.nickname}</option>
          ))}
        </select>
      </div>

      <div className="field seed">
        <label>Seed</label>
        <input
          type="number"
          placeholder="random"
          value={seed}
          onChange={(e) => setSeed(e.target.value)}
        />
      </div>

      <div className="field">
        <label>Preset</label>
        <select value={preset} onChange={(e) => setPreset(e.target.value)}>
          {PRESETS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      <button className="sim" type="submit" disabled={!canSubmit}>
        {submitting ? "Starting…" : "Simulate Season"}
      </button>
    </form>
  );
}

// --- Running state -------------------------------------------------------

interface RunningStateProps {
  sim: SimulationStatus;
  onCancel: () => void;
  cancelling: boolean;
  elapsedMs: number;
}
function SeasonRunningState({ sim, onCancel, cancelling, elapsedMs }: RunningStateProps) {
  const fr = franchiseFor(sim.team, sim.season);
  const pct = sim.total_games ? Math.min(100, (sim.games_completed / sim.total_games) * 100) : 0;
  const rate = elapsedMs > 0 && sim.games_completed > 0 ? sim.games_completed / (elapsedMs / 1000) : 0;
  const remaining = rate > 0 ? Math.max(0, (sim.total_games - sim.games_completed) / rate) : null;
  const elapsedSec = Math.floor(elapsedMs / 1000);

  return (
    <div className="season-running" style={fr ? { borderTop: `3px solid ${fr.primaryColor}` } : undefined}>
      <div className="sr-identity">
        <TeamLogo abbr={sim.team} season={sim.season} size="lg" />
        <div>
          <div className="sh-team">{fr ? `${fr.city} ${fr.nickname}` : sim.team}</div>
          <div className="sh-meta">{sim.season} · seed {sim.seed} · run #{sim.id} · {sim.status}</div>
        </div>
      </div>
      <div className="sr-progress">
        <div className="progress-bar" role="progressbar" aria-valuenow={sim.games_completed} aria-valuemin={0} aria-valuemax={sim.total_games}>
          <div
            className="progress-fill"
            style={{
              width: `${pct}%`,
              background: fr ? fr.primaryColor : "var(--accent)",
            }}
          />
        </div>
        <div className="sr-progress-meta">
          <span><b>{sim.games_completed}</b> / {sim.total_games} games</span>
          <span>elapsed <b>{elapsedSec}s</b></span>
          {remaining != null && <span>ETA <b>{Math.ceil(remaining)}s</b></span>}
        </div>
      </div>
      <button className="cancel-btn" onClick={onCancel} disabled={cancelling}>
        {cancelling ? "Cancelling…" : "Cancel"}
      </button>
    </div>
  );
}

// --- Standings + game list (unchanged from B1) ---------------------------

function SeasonStandings({ standings }: { standings: ReturnType<typeof computeStandings> }) {
  const s = standings;
  return (
    <div className="season-standings">
      <div className="ss-cell"><span className="ss-k">Record</span><span className="ss-v">{s.w}-{s.l} <em>({(s.wPct * 100).toFixed(1)}%)</em></span></div>
      <div className="ss-cell"><span className="ss-k">Home</span><span className="ss-v">{s.homeW}-{s.homeL}</span></div>
      <div className="ss-cell"><span className="ss-k">Away</span><span className="ss-v">{s.awayW}-{s.awayL}</span></div>
      <div className="ss-cell"><span className="ss-k">PPG</span><span className="ss-v">{s.ppgScored.toFixed(1)}</span></div>
      <div className="ss-cell"><span className="ss-k">Opp PPG</span><span className="ss-v">{s.ppgAllowed.toFixed(1)}</span></div>
      <div className="ss-cell"><span className="ss-k">Blowout%</span><span className="ss-v">{(s.blowoutRate * 100).toFixed(1)}%</span></div>
      <div className="ss-cell"><span className="ss-k">OT%</span><span className="ss-v">{(s.otRate * 100).toFixed(1)}%</span></div>
    </div>
  );
}

interface GameListProps {
  rows: RowExt[];
  season: string;
  onSelect: (g: RowExt) => void;
}
function SeasonGameList({ rows, season, onSelect }: GameListProps) {
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const sorted = useMemo(() => {
    const cmp = (a: RowExt, b: RowExt): number => {
      switch (sortKey) {
        case "date":     return a.game_date.localeCompare(b.game_date);
        case "opponent": return a.opponent.localeCompare(b.opponent);
        case "score":    return a.teamScore - b.teamScore;
        case "margin":   return a.margin - b.margin;
        case "result":   return Number(a.win) - Number(b.win);
      }
    };
    const out = [...rows].sort(cmp);
    return sortDir === "asc" ? out : out.reverse();
  }, [rows, sortKey, sortDir]);

  const toggle = (k: SortKey) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir(k === "date" ? "asc" : "desc"); }
  };
  const arrow = (k: SortKey) =>
    sortKey === k ? (sortDir === "asc" ? " ▲" : " ▼") : "";

  return (
    <div className="season-games">
      <table>
        <thead>
          <tr>
            <th className="sortable" onClick={() => toggle("date")}>Date{arrow("date")}</th>
            <th className="sortable" onClick={() => toggle("opponent")}>Opponent{arrow("opponent")}</th>
            <th className="sortable" onClick={() => toggle("score")}>Score{arrow("score")}</th>
            <th className="sortable" onClick={() => toggle("margin")}>Margin{arrow("margin")}</th>
            <th className="sortable" onClick={() => toggle("result")}>Result{arrow("result")}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const fr = franchiseFor(r.opponent, season);
            const teamColor = fr ? readableOnDark(fr.primaryColor) : undefined;
            return (
              <tr key={r.game_id} className="clickable" onClick={() => onSelect(r)}>
                <td className="date">{r.game_date}</td>
                <td className="opp">
                  <span className="game-opp-cell">
                    <span className="loc">{r.isHome ? "vs" : "@"}</span>
                    <TeamLogo abbr={r.opponent} season={season} size="sm" />
                    <span className="opp-name" style={teamColor ? { color: teamColor } : undefined}>
                      {fr ? `${fr.city} ${fr.nickname}` : r.opponent}
                    </span>
                  </span>
                </td>
                <td className="score">
                  {r.teamScore}-{r.oppScore}
                  {r.went_to_ot && <span className="ot-badge">OT</span>}
                </td>
                <td className={`margin ${r.margin > 0 ? "pm-plus" : "pm-minus"}`}>
                  {r.margin > 0 ? `+${r.margin}` : r.margin}
                </td>
                <td className={r.win ? "pm-plus" : "pm-minus"}>{r.win ? "W" : "L"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Compact status dot for the run picker rows.
function StatusDot({ status }: { status: SimulationSummary["status"] }) {
  return <span className={`status-dot status-${status}`} title={status} />;
}

interface RunPickerProps {
  runs: SimulationSummary[];
  currentId: number;
  onSwitch: (id: number) => void;
  onDelete: (id: number) => void;
  onRerun: (r: SimulationSummary) => void;
}
function RunPicker({ runs, currentId, onSwitch, onDelete, onRerun }: RunPickerProps) {
  const [open, setOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<number | null>(null);

  if (runs.length === 0) return null;

  const label = `Runs (${runs.length})`;

  return (
    <div className="run-picker">
      <button className="run-picker-toggle" onClick={() => setOpen((o) => !o)}>
        {label} {open ? "▲" : "▼"}
      </button>
      {open && (
        <div className="run-picker-menu" role="menu">
          {runs.map((r) => {
            const isCurrent = r.id === currentId;
            const rec = r.wins != null && r.losses != null ? `${r.wins}-${r.losses}` : null;
            const confirming = confirmingDelete === r.id;
            return (
              <div key={r.id} className={`run-picker-row ${isCurrent ? "on" : ""}`}>
                <button
                  className="run-picker-label"
                  onClick={() => { onSwitch(r.id); setOpen(false); }}
                >
                  <StatusDot status={r.status} />
                  <span className="rp-team">{r.team}</span>
                  <span className="rp-season">{r.season}</span>
                  {rec && <span className="rp-record">{rec}</span>}
                  <span className="rp-ts">#{r.id}</span>
                </button>
                <div className="run-picker-actions">
                  {confirming ? (
                    <>
                      <button
                        className="rp-confirm"
                        onClick={() => { onDelete(r.id); setConfirmingDelete(null); }}
                      >
                        Yes
                      </button>
                      <button className="rp-cancel" onClick={() => setConfirmingDelete(null)}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="rp-btn"
                        title="Re-run with same team + season"
                        onClick={() => { onRerun(r); setOpen(false); }}
                      >
                        Re-run
                      </button>
                      <button
                        className="rp-btn rp-danger"
                        title={r.status === "running" ? "Cancel before deleting" : "Delete"}
                        disabled={r.status === "running"}
                        onClick={() => setConfirmingDelete(r.id)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface SeasonHeaderProps {
  sim: SimulationStatus;
  standings: ReturnType<typeof computeStandings>;
  cancelledAt: number | null;
  onNewSim: () => void;
  runs: SimulationSummary[];
  onSwitchRun: (id: number) => void;
  onDeleteRun: (id: number) => void;
  onRerunRun: (r: SimulationSummary) => void;
}

function SeasonHeader({
  sim, standings, cancelledAt, onNewSim,
  runs, onSwitchRun, onDeleteRun, onRerunRun,
}: SeasonHeaderProps) {
  const fr = franchiseFor(sim.team, sim.season);
  const style = fr ? { borderTop: `3px solid ${fr.primaryColor}` } : undefined;
  return (
    <div className="season-header" style={style}>
      <div className="sh-identity">
        <TeamLogo abbr={sim.team} season={sim.season} size="lg" />
        <div className="sh-text">
          <div className="sh-team">{fr ? `${fr.city} ${fr.nickname}` : sim.team}</div>
          <div className="sh-meta">
            {sim.season} · seed {sim.seed} · run #{sim.id} · {sim.games_completed}/{sim.total_games} games
            {cancelledAt != null && <span className="sh-cancelled"> · cancelled at game {cancelledAt}</span>}
          </div>
        </div>
      </div>
      <div className="sh-right">
        <RunPicker
          runs={runs}
          currentId={sim.id}
          onSwitch={onSwitchRun}
          onDelete={onDeleteRun}
          onRerun={onRerunRun}
        />
        <div className="sh-record">
          <div className="sh-record-big">{standings.w}-{standings.l}</div>
          <div className="sh-record-sub">{(standings.wPct * 100).toFixed(1)}%</div>
        </div>
        <button className="new-sim-btn" onClick={onNewSim}>New Sim</button>
      </div>
    </div>
  );
}

// --- Top-level view ------------------------------------------------------

type Mode = "loading" | "form" | "active" | "browse";

export default function SeasonView() {
  const [mode, setMode] = useState<Mode>("loading");
  const [sim, setSim] = useState<SimulationStatus | null>(null);
  const [runs, setRuns] = useState<SimulationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedGame, setSelectedGame] = useState<RowExt | null>(null);
  const [gameDetail, setGameDetail] = useState<SimulateGameResponse | null>(null);
  const [gameLoading, setGameLoading] = useState(false);
  const [gameError, setGameError] = useState<string | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerLine | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [runStartedAt, setRunStartedAt] = useState<number>(0);
  // Bumped once per poll tick to trigger a re-render with fresh elapsed time.
  const [tick, setTick] = useState(0);
  const [formPrefill, setFormPrefill] = useState<FormPrefill | undefined>(undefined);

  // Refetch the run list — called at init and after any create/delete/complete.
  const refreshRuns = useCallback(async () => {
    const rs = await listSimulations();
    setRuns(rs);
    return rs;
  }, []);

  // Initial load: pick the most recent active OR completed run.
  useEffect(() => {
    refreshRuns()
      .then((rs) => {
        const active = rs.find((r) => r.status === "running" || r.status === "pending");
        if (active) {
          setRunStartedAt(Date.parse(active.created_at) || Date.now());
          return getSimulation(active.id).then((s) => { setSim(s); setMode("active"); });
        }
        const done = rs.find((r) => r.status === "complete" || r.status === "cancelled");
        if (done) {
          return getSimulation(done.id).then((s) => { setSim(s); setMode("browse"); });
        }
        setMode("form");
      })
      .catch((e) => setError(String(e)));
  }, [refreshRuns]);

  // Poll while in active mode + tab is visible + season tab is mounted (mount-scoped).
  const pollActive = mode === "active";
  useEffect(() => {
    if (!pollActive || !sim) return;
    let cancelled = false;

    const tick = async () => {
      if (document.visibilityState === "hidden") return;   // pause when tab hidden
      try {
        const s = await getSimulation(sim.id);
        if (cancelled) return;
        setSim(s);
        setTick((t) => t + 1);
        if (s.status === "complete" || s.status === "cancelled") {
          setMode("browse");
          refreshRuns().catch(() => {});
        } else if (s.status === "failed") {
          setError("Simulation failed. Try again.");
          setMode("form");
          refreshRuns().catch(() => {});
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };

    const interval = window.setInterval(tick, POLL_INTERVAL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") tick(); };
    document.addEventListener("visibilitychange", onVisible);
    tick(); // first tick immediately so counters update

    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [pollActive, sim?.id, refreshRuns]);

  const cancelledAt: number | null = useMemo(() => {
    if (!sim || sim.status !== "cancelled") return null;
    return sim.games_completed;
  }, [sim]);

  const onCancel = useCallback(async () => {
    if (!sim) return;
    setCancelling(true);
    try {
      await cancelSimulation(sim.id);
      // polling will pick up the status change on next tick
    } catch (e) {
      setError(String(e));
    } finally {
      setCancelling(false);
    }
  }, [sim]);

  const onCreated = useCallback(async (id: number) => {
    setError(null);
    setRunStartedAt(Date.now());
    setFormPrefill(undefined);
    try {
      const s = await getSimulation(id);
      setSim(s);
      setMode("active");
      // Best-effort refresh so the picker knows about the new run.
      refreshRuns().catch(() => {});
    } catch (e) {
      setError(String(e));
    }
  }, [refreshRuns]);

  const onDetectedActive = useCallback(async (id: number) => {
    setError(null);
    setRunStartedAt(Date.now());  // approximation — real created_at fetched next
    try {
      const s = await getSimulation(id);
      setSim(s);
      setRunStartedAt(Date.parse(s.created_at) || Date.now());
      setMode("active");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const openForm = useCallback(() => {
    setMode("form");
    setError(null);
    setFormPrefill(undefined);   // "New Sim" starts blank; re-run pre-populates below
  }, []);

  const switchToRun = useCallback(async (id: number) => {
    setError(null);
    try {
      const s = await getSimulation(id);
      setSim(s);
      setSelectedGame(null);
      setGameDetail(null);
      if (s.status === "running" || s.status === "pending") {
        setRunStartedAt(Date.parse(s.created_at) || Date.now());
        setMode("active");
      } else {
        setMode("browse");
      }
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const deleteRun = useCallback(async (id: number) => {
    try {
      await deleteSimulation(id);
      const rs = await refreshRuns();
      if (sim && sim.id === id) {
        // Currently-viewed run was deleted — jump to the next most recent complete/cancelled.
        const next = rs.find((r) => r.status === "complete" || r.status === "cancelled");
        if (next) {
          const s = await getSimulation(next.id);
          setSim(s);
          setMode("browse");
        } else {
          setSim(null);
          setMode("form");
        }
      }
    } catch (e) {
      setError(String(e));
    }
  }, [refreshRuns, sim]);

  const rerunRun = useCallback((r: SimulationSummary) => {
    setFormPrefill({ team: r.team, season: r.season, preset: "drama-m3" });
    setMode("form");
    setError(null);
  }, []);

  // Fetch full detail when a game is selected.
  useEffect(() => {
    if (!selectedGame || !sim) return;
    setGameDetail(null);
    setGameError(null);
    setGameLoading(true);
    getSeasonGame(sim.id, selectedGame.game_id)
      .then(setGameDetail)
      .catch((e) => setGameError(String(e)))
      .finally(() => setGameLoading(false));
  }, [selectedGame, sim]);

  if (error) return (
    <>
      <div className="error">{error}</div>
      <button className="back-btn" onClick={() => { setError(null); setMode("form"); }}>Try again</button>
    </>
  );

  if (mode === "loading") return <div className="empty-hint">Loading season runs…</div>;

  if (mode === "form") {
    return (
      <div className="season-form-wrap">
        <h3 className="season-form-title">
          {formPrefill ? `Re-run ${formPrefill.team} · ${formPrefill.season}` : "Start a new season simulation"}
        </h3>
        <NewSeasonForm
          onCreated={onCreated}
          onError={(m) => setError(m)}
          onDetectedActive={onDetectedActive}
          prefill={formPrefill}
        />
      </div>
    );
  }

  if (mode === "active" && sim) {
    // Reference `tick` so React re-renders each poll and elapsed stays live.
    void tick;
    return (
      <SeasonRunningState
        sim={sim}
        onCancel={onCancel}
        cancelling={cancelling}
        elapsedMs={Date.now() - runStartedAt}
      />
    );
  }

  // browse
  if (!sim) return <div className="empty-hint">No completed run.</div>;
  const rows = (sim.games ?? []).map((g) => extendRow(g, sim.team));
  const standings = computeStandings(rows);

  if (selectedGame) {
    return (
      <div className="season-game-detail">
        <button className="back-btn" onClick={() => { setSelectedGame(null); setGameDetail(null); }}>
          ← Back to season
        </button>
        {gameLoading && <div className="empty-hint">Loading game…</div>}
        {gameError && <div className="error">{gameError}</div>}
        {gameDetail && (
          <>
            <LineScore game={gameDetail} />
            <div className="boxes">
              <BoxScore
                title={gameDetail.away_team}
                abbr={gameDetail.away_team}
                season={gameDetail.season}
                sideLabel="Away"
                players={gameDetail.away_box}
                onSelectPlayer={setSelectedPlayer}
              />
              <BoxScore
                title={gameDetail.home_team}
                abbr={gameDetail.home_team}
                season={gameDetail.season}
                sideLabel="Home"
                players={gameDetail.home_box}
                onSelectPlayer={setSelectedPlayer}
              />
            </div>
            <PlayByPlay game={gameDetail} />
            {selectedPlayer && (
              <PlayerModal
                key={selectedPlayer.player_id}
                line={selectedPlayer}
                season={gameDetail.season}
                events={gameDetail.events ?? []}
                onClose={() => setSelectedPlayer(null)}
              />
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <>
      <SeasonHeader
        sim={sim}
        standings={standings}
        cancelledAt={cancelledAt}
        onNewSim={openForm}
        runs={runs}
        onSwitchRun={switchToRun}
        onDeleteRun={deleteRun}
        onRerunRun={rerunRun}
      />
      <SeasonStandings standings={standings} />
      <SeasonGameList rows={rows} season={sim.season} onSelect={setSelectedGame} />
    </>
  );
}

// Exports for testing.
export { computeStandings, extendRow };
