import { useEffect, useMemo, useState } from "react";
import { getSeasonAverages } from "../api";
import type { PlayerAveragesRow, SeasonAverages } from "../types";

// Sim season averages next to ingested real NBA anchors (B4).
// Design principle (Xavier 2026-08-11): validation/comparison surface first, polished
// stats page second. Sim is primary text, real is muted secondary. No derived values.

type SortKey =
  | "mpg" | "ppg" | "rpg" | "apg" | "spg" | "bpg" | "topg" | "pf_per_game"
  | "fg_pct" | "fg3_pct" | "ft_pct" | "gp";

interface CellProps {
  sim: number | null | undefined;
  real: number | null | undefined;
  digits?: number;
  pct?: boolean;
}

// Renders "sim / real" with sim primary, real muted. "—" for missing real.
function Cell({ sim, real, digits = 1, pct = false }: CellProps) {
  const fmt = (v: number | null | undefined): string => {
    if (v == null) return "—";
    if (pct) return `${(v * 100).toFixed(digits)}`;
    return v.toFixed(digits);
  };
  return (
    <span className="avg-cell">
      <span className="avg-sim">{fmt(sim)}</span>
      <span className="avg-sep"> / </span>
      <span className="avg-real">{fmt(real)}</span>
    </span>
  );
}

// Sim-only cell (used for GP where the raw counts are useful and real GP is separate).
function CellInt({ sim, real }: { sim: number | null | undefined; real: number | null | undefined }) {
  const fmt = (v: number | null | undefined): string => (v == null ? "—" : String(v));
  return (
    <span className="avg-cell">
      <span className="avg-sim">{fmt(sim)}</span>
      <span className="avg-sep"> / </span>
      <span className="avg-real">{fmt(real)}</span>
    </span>
  );
}

function TeamStrip({ data }: { data: SeasonAverages }) {
  const s = data.team_totals.sim;
  const r = data.team_totals.real;
  // Show what real has directly — no derived values from pace × ratings.
  const rows: Array<[string, number | undefined, number | undefined, number]> = [
    ["PPG",       s.ppg,       undefined,       1],
    ["OPP PPG",   s.opp_ppg,   undefined,       1],
    ["FGA",       s.fga,       undefined,       1],
    ["FTA",       s.fta,       undefined,       1],
    ["PF",        s.pf,        undefined,       1],
    ["TOV",       s.tov,       undefined,       1],
    ["Off Rtg",   undefined,   r.off_rating,    1],
    ["Def Rtg",   undefined,   r.def_rating,    1],
    ["Pace",      undefined,   r.pace,          1],
    ["OREB%",     undefined,   r.oreb_pct != null ? r.oreb_pct * 100 : undefined, 1],
  ];
  return (
    <div className="team-averages">
      <h4 className="ta-title">Team averages</h4>
      <div className="ta-grid">
        {rows.map(([label, sim, real, digits]) => (
          <div key={label} className="ta-cell">
            <div className="ta-label">{label}</div>
            <div className="ta-val">
              <Cell sim={sim ?? null} real={real ?? null} digits={digits as number} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface Props {
  simId: number;
}

export default function SeasonAveragesView({ simId }: Props) {
  const [data, setData] = useState<SeasonAverages | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("mpg");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    setData(null);
    setError(null);
    getSeasonAverages(simId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [simId]);

  const rows = data?.players ?? [];
  const sorted = useMemo(() => {
    const cmp = (a: PlayerAveragesRow, b: PlayerAveragesRow): number => {
      const av = (a.sim[sortKey] ?? 0) as number;
      const bv = (b.sim[sortKey] ?? 0) as number;
      return av - bv;
    };
    const out = [...rows].sort(cmp);
    return sortDir === "asc" ? out : out.reverse();
  }, [rows, sortKey, sortDir]);

  const toggle = (k: SortKey) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("desc"); }
  };
  const arrow = (k: SortKey) => (sortKey === k ? (sortDir === "asc" ? " ▲" : " ▼") : "");

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="empty-hint">Loading averages…</div>;

  return (
    <div className="season-averages">
      <TeamStrip data={data} />
      <div className="pa-legend">
        <span className="avg-sim">sim</span>
        <span className="avg-sep"> / </span>
        <span className="avg-real">real</span>
        <span className="pa-legend-note"> (real from ingested NBA season stats; "—" when unavailable)</span>
      </div>
      <div className="player-averages">
        <table>
          <thead>
            <tr>
              <th className="name">Player</th>
              <th className="sortable" onClick={() => toggle("gp")}>GP{arrow("gp")}</th>
              <th className="sortable" onClick={() => toggle("mpg")}>MPG{arrow("mpg")}</th>
              <th className="sortable" onClick={() => toggle("ppg")}>PPG{arrow("ppg")}</th>
              <th className="sortable" onClick={() => toggle("rpg")}>RPG{arrow("rpg")}</th>
              <th className="sortable" onClick={() => toggle("apg")}>APG{arrow("apg")}</th>
              <th className="sortable" onClick={() => toggle("spg")}>SPG{arrow("spg")}</th>
              <th className="sortable" onClick={() => toggle("bpg")}>BPG{arrow("bpg")}</th>
              <th className="sortable" onClick={() => toggle("topg")}>TOV{arrow("topg")}</th>
              <th className="sortable" onClick={() => toggle("fg_pct")}>FG%{arrow("fg_pct")}</th>
              <th className="sortable" onClick={() => toggle("fg3_pct")}>3P%{arrow("fg3_pct")}</th>
              <th className="sortable" onClick={() => toggle("ft_pct")}>FT%{arrow("ft_pct")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.player_id}>
                <td className="name">{p.name}</td>
                <td><CellInt sim={p.sim.gp} real={p.real?.gp} /></td>
                <td><Cell sim={p.sim.mpg} real={p.real?.mpg} /></td>
                <td><Cell sim={p.sim.ppg} real={p.real?.ppg} /></td>
                <td><Cell sim={p.sim.rpg} real={p.real?.rpg} /></td>
                <td><Cell sim={p.sim.apg} real={p.real?.apg} /></td>
                <td><Cell sim={p.sim.spg} real={p.real?.spg} /></td>
                <td><Cell sim={p.sim.bpg} real={p.real?.bpg} /></td>
                <td><Cell sim={p.sim.topg} real={p.real?.topg} /></td>
                <td><Cell sim={p.sim.fg_pct} real={p.real?.fg_pct} pct /></td>
                <td><Cell sim={p.sim.fg3_pct} real={p.real?.fg3_pct} pct /></td>
                <td><Cell sim={p.sim.ft_pct} real={p.real?.ft_pct} pct /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
