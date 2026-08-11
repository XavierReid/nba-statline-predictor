import { useEffect, useMemo, useState } from "react";
import { getSeasonGame, getSimulation, listSimulations } from "../api";
import type {
  PlayerLine,
  SimulateGameResponse,
  SimulatedGameSummary,
  SimulationStatus,
  SimulationSummary,
} from "../types";
import { franchiseFor } from "../data/franchises";
import { readableOnDark } from "../data/color";
import LineScore from "./LineScore";
import BoxScore from "./BoxScore";
import PlayByPlay from "./PlayByPlay";
import PlayerModal from "./PlayerModal";
import TeamLogo from "./TeamLogo";

// Season sim read-only browse (B1). Auto-loads the most recent completed run,
// shows a standings-line + game list, and drills into any game using the same
// LineScore / BoxScore / PlayByPlay components as the single-game view.

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

// --- Aggregation for the standings-line -----------------------------------

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

// --- Standings row --------------------------------------------------------

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

// --- Game list -----------------------------------------------------------

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

// --- Header --------------------------------------------------------------

function SeasonHeader({ sim, standings }: { sim: SimulationStatus; standings: ReturnType<typeof computeStandings> }) {
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
          </div>
        </div>
      </div>
      <div className="sh-record">
        <div className="sh-record-big">{standings.w}-{standings.l}</div>
        <div className="sh-record-sub">{(standings.wPct * 100).toFixed(1)}%</div>
      </div>
    </div>
  );
}

// --- Top-level view ------------------------------------------------------

export default function SeasonView() {
  const [runs, setRuns] = useState<SimulationSummary[] | null>(null);
  const [sim, setSim] = useState<SimulationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedGame, setSelectedGame] = useState<RowExt | null>(null);
  const [gameDetail, setGameDetail] = useState<SimulateGameResponse | null>(null);
  const [gameLoading, setGameLoading] = useState(false);
  const [gameError, setGameError] = useState<string | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerLine | null>(null);

  // Initial load: pick the most recent completed run.
  useEffect(() => {
    listSimulations()
      .then((rs) => {
        setRuns(rs);
        const done = rs.find((r) => r.status === "complete");
        if (done) {
          return getSimulation(done.id).then(setSim);
        }
        return undefined;
      })
      .catch((e) => setError(String(e)));
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

  if (error) return <div className="error">{error}</div>;
  if (runs === null) return <div className="empty-hint">Loading season runs…</div>;

  if (!sim) {
    return (
      <div className="empty-preview">
        <p className="empty-hint">
          No completed season simulations yet. Kick one off via the API
          (POST /simulations/, then /start) — a UI to create runs will land
          in the next session.
        </p>
      </div>
    );
  }

  const rows = (sim.games ?? []).map((g) => extendRow(g, sim.team));
  const standings = computeStandings(rows);

  // Game-detail view
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
      <SeasonHeader sim={sim} standings={standings} />
      <SeasonStandings standings={standings} />
      <SeasonGameList rows={rows} season={sim.season} onSelect={setSelectedGame} />
    </>
  );
}

// Exports for testing.
export { computeStandings, extendRow };
