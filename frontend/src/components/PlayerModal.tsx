import { useEffect, useState, type ReactNode } from "react";
import { getMyLeaguePlayerStats, getPlayerProfile } from "../api";
import type {
  MyLeaguePlayerStats,
  PlayerLine,
  PlayerProfile,
  PossessionEvent,
} from "../types";
import PlayerHeadshot from "./PlayerHeadshot";
import TeamLogo from "./TeamLogo";
import { franchiseFor } from "../data/franchises";

// A titled block. Sections are composed in App-visible order so adding future ones
// (recent games, shot chart, career, matchup history) is a one-line insertion — not a
// rewrite of a tightly-packed layout.
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pm-section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function clock(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

// Which ways this player is involved in a typed event. Under RFC.md "Event-Sourced
// PBP" every event has exactly one primary actor (player_id) so involvement is
// (type, player_id) → tag. Made SHOTs get both SCORE and SHOT chips.
function involvement(ev: PossessionEvent, id: number): string[] {
  if (ev.player_id !== id) return [];
  const tags: string[] = [];
  switch (ev.type) {
    case "SHOT":
      // Mutually exclusive: SCORE = the shooter made it, SHOT = they missed. Lets
      // the user isolate "attempts that missed" vs "scoring plays" without one
      // chip dominating the other. Matches the pre-refactor semantic.
      tags.push(ev.made ? "SCORE" : "SHOT");
      break;
    case "FT":
      tags.push("FT");
      break;
    case "FOUL":
      tags.push("FOUL");
      break;
    case "REB":
      tags.push("REB");
      break;
    case "AST":
      tags.push("AST");
      break;
    case "STL":
      tags.push("STL");
      break;
    case "BLK":
      tags.push("BLK");
      break;
    case "TOV":
      tags.push("TOV");
      break;
  }
  return tags;
}

// Order in which filter chips appear.
const TAG_ORDER = ["SCORE", "SHOT", "FT", "AST", "REB", "STL", "BLK", "TOV", "FOUL"];

// Running stat badge shown next to each event in the modal PBP. Returns the
// category and the player's cumulative total AFTER this event, or null when the
// event doesn't increment any counting stat (e.g. missed shot / missed FT).
// State lives in `run`, mutated in place as we walk the player's events in order.
interface RunState {
  pts: number; reb: number; ast: number; stl: number; blk: number;
  tov: number; pf: number;
}
function runningBadge(ev: PossessionEvent, run: RunState): { label: string } | null {
  switch (ev.type) {
    case "SHOT":
      if (ev.made) {
        const three = ev.shot_type === "corner_three" || ev.shot_type === "above_break_three" || ev.shot_type === "three";
        run.pts += three ? 3 : 2;
        return { label: `PTS ${run.pts}` };
      }
      return null;
    case "FT":
      if (ev.made) {
        run.pts += 1;
        return { label: `PTS ${run.pts}` };
      }
      return null;
    case "REB": run.reb += 1; return { label: `REB ${run.reb}` };
    case "AST": run.ast += 1; return { label: `AST ${run.ast}` };
    case "STL": run.stl += 1; return { label: `STL ${run.stl}` };
    case "BLK": run.blk += 1; return { label: `BLK ${run.blk}` };
    case "TOV": run.tov += 1; return { label: `TOV ${run.tov}` };
    case "FOUL": run.pf += 1; return { label: `PF ${run.pf}` };
  }
  return null;
}

function pct(x: number | null): string {
  return x == null ? "—" : `${(x * 100).toFixed(1)}%`;
}

// Only ratings actually consumed by the possession engine are surfaced. `overall`,
// `ball_handle`, and `clutch` are display-only in the current model (grep-verified
// against possession.py) and read wrong for stars vs role players — hidden until
// their derivation is revamped.
const RATING_ORDER = [
  "three_point", "mid_range", "layup", "passing",
  "perimeter_defense", "interior_defense", "offensive_rebound", "defensive_rebound",
];
const RATING_LABEL: Record<string, string> = {
  overall: "Overall", three_point: "3PT", mid_range: "Mid", layup: "Finishing",
  passing: "Passing", ball_handle: "Handle", perimeter_defense: "Perim D",
  interior_defense: "Interior D", offensive_rebound: "OReb", defensive_rebound: "DReb",
  clutch: "Clutch",
};

interface Props {
  line: PlayerLine;
  season: string;
  events: PossessionEvent[];
  onClose: () => void;
  /** MyLeague context — when set, fetches sim-vs-real running averages
   * for this sim and renders them in place of the real-only baseline
   * "Season averages" section. Sim is primary, real is reference. */
  myleagueSimId?: number;
}

export default function PlayerModal({ line, season, events, onClose, myleagueSimId }: Props) {
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mlStats, setMlStats] = useState<MyLeaguePlayerStats | null>(null);
  const [mlError, setMlError] = useState<string | null>(null);

  useEffect(() => {
    setProfile(null);
    setError(null);
    getPlayerProfile(line.player_id, season)
      .then(setProfile)
      .catch((e) => setError(String(e)));
  }, [line.player_id, season]);

  useEffect(() => {
    setMlStats(null);
    setMlError(null);
    if (myleagueSimId == null) return;
    getMyLeaguePlayerStats(myleagueSimId, line.player_id)
      .then(setMlStats)
      .catch((e) => setMlError(String(e)));
  }, [myleagueSimId, line.player_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const myEvents = events.filter((ev) => involvement(ev, line.player_id).length > 0);
  const availableTags = TAG_ORDER.filter((t) =>
    myEvents.some((ev) => involvement(ev, line.player_id).includes(t))
  );
  // start with every available tag active; App keys the modal per player so this
  // initializer re-runs on a new player (no reset effect / empty first render).
  const [activeTags, setActiveTags] = useState<Set<string>>(() => new Set(availableTags));
  const toggleTag = (t: string) =>
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  // Precompute running-stat badges across ALL of the player's events in order.
  // Running totals accumulate on the unfiltered stream so numbers stay stable as
  // chips toggle; the badge for each event is then rendered only if that event
  // is in shownEvents below.
  const badges = new Map<PossessionEvent, string>();
  {
    const run: RunState = { pts: 0, reb: 0, ast: 0, stl: 0, blk: 0, tov: 0, pf: 0 };
    for (const ev of myEvents) {
      const b = runningBadge(ev, run);
      if (b) badges.set(ev, b.label);
    }
  }
  const shownEvents = myEvents.filter((ev) =>
    involvement(ev, line.player_id).some((t) => activeTags.has(t))
  );
  const a = profile?.season_averages;

  return (
    <div className="pm-overlay" onClick={onClose}>
      <div className="pm-modal" onClick={(e) => e.stopPropagation()}>
        <button className="pm-close" onClick={onClose} aria-label="Close">
          ✕
        </button>

        {(() => {
          // Split the player's name into first + last for the stacked header
          // (matches ESPN's two-line convention). "Kobe Bryant" → ["Kobe", "Bryant"].
          const nameParts = line.name.trim().split(/\s+/);
          const firstName = nameParts.length > 1 ? nameParts.slice(0, -1).join(" ") : "";
          const lastName = nameParts[nameParts.length - 1] ?? line.name;
          const teamAbbr = profile?.team ?? undefined;
          const fr = teamAbbr ? franchiseFor(teamAbbr, season) : null;
          // Subtle team-color band behind the hero — primary color at low
          // opacity via CSS variable so the theme's dark background stays
          // dominant while identity signals through.
          const bandStyle = fr
            ? ({ ["--team-primary" as string]: fr.primaryColor } as React.CSSProperties)
            : undefined;
          return (
            <div className="pm-hero" style={bandStyle}>
              <div className="pm-hero-band" />
              <div className="pm-hero-inner">
                <PlayerHeadshot
                  playerId={line.player_id}
                  name={line.name}
                  size="large"
                  className="pm-headshot"
                />
                <div className="pm-hero-identity">
                  {firstName && <div className="pm-name-first">{firstName}</div>}
                  <div className="pm-name-last">{lastName}</div>
                  <div className="pm-meta">
                    {teamAbbr && <TeamLogo abbr={teamAbbr} season={season} size="sm" />}
                    <span>
                      {fr ? `${fr.city} ${fr.nickname}` : (teamAbbr ?? "")}
                      {profile?.position ? ` · ${profile.position}` : ""}
                      {` · ${season}`}
                    </span>
                  </div>
                </div>
                {profile?.season_averages && (
                  <div className="pm-hero-stats-wrap">
                    <div className="pm-hero-stats-label">
                      {season} Regular Season Stats
                    </div>
                    <div className="pm-hero-stats" title="Season averages">
                      <HeroStat k="PPG" v={profile.season_averages.pts.toFixed(1)} />
                      <HeroStat k="RPG" v={profile.season_averages.reb.toFixed(1)} />
                      <HeroStat k="APG" v={profile.season_averages.ast.toFixed(1)} />
                      <HeroStat
                        k="FG%"
                        v={
                          profile.season_averages.fg_pct != null
                            ? `${(profile.season_averages.fg_pct * 100).toFixed(1)}`
                            : "—"
                        }
                      />
                    </div>
                  </div>
                )}
              </div>
              {profile && Object.keys(profile.ratings).length > 0 && (
                <div className="pm-hero-ratings" title="Player ratings">
                  {RATING_ORDER.filter((k) => k in profile.ratings).map((k) => (
                    <div className="pm-hero-rating" key={k}>
                      <span className="pm-hero-rating-label">{RATING_LABEL[k] ?? k}</span>
                      <span className="pm-hero-rating-bar">
                        <span
                          className="pm-hero-rating-fill"
                          style={{ width: `${profile.ratings[k]}%` }}
                        />
                      </span>
                      <span className="pm-hero-rating-val">{profile.ratings[k]}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        <Section title="This game">
          <div className="pm-inline-stats">
            <span><b>MIN</b> {line.minutes.toFixed(1)}</span>
            <span><b>FG</b> {line.fgm}/{line.fga}</span>
            <span><b>3PT</b> {line.fg3m}/{line.fg3a}</span>
            <span><b>FT</b> {line.ftm}/{line.fta}</span>
            <span><b>REB</b> {line.rebounds}</span>
            <span><b>AST</b> {line.assists}</span>
            <span><b>STL</b> {line.steals}</span>
            <span><b>BLK</b> {line.blocks}</span>
            <span><b>TO</b> {line.turnovers}</span>
            <span><b>PF</b> {line.personal_fouls}</span>
            <span className={line.plus_minus > 0 ? "pm-plus" : line.plus_minus < 0 ? "pm-minus" : ""}>
              <b>+/-</b> {line.plus_minus > 0 ? `+${line.plus_minus}` : line.plus_minus}
            </span>
            <span className="pm-pts-highlight"><b>PTS</b> {line.points}</span>
          </div>
        </Section>

        <Section title="This game — play-by-play">
          {myEvents.length === 0 ? (
            <p className="pm-empty">No play-by-play events.</p>
          ) : (
            <>
              {availableTags.length > 1 && (
                <div className="pm-filters">
                  {availableTags.map((t) => (
                    <button
                      key={t}
                      className={activeTags.has(t) ? "pm-chip on" : "pm-chip"}
                      onClick={() => toggleTag(t)}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              )}
              {shownEvents.length === 0 ? (
                <p className="pm-empty">No events match the filter.</p>
              ) : (
                <ul className="pm-pbp">
                  {shownEvents.map((ev, i) => {
                    const period = ev.quarter <= 4 ? `Q${ev.quarter}` : `OT${ev.quarter - 4}`;
                    const badge = badges.get(ev);
                    return (
                      <li key={i}>
                        <span className="pm-clock">{period} {clock(ev.game_clock_seconds)}</span>
                        <span className="pm-tags">
                          {involvement(ev, line.player_id).map((t) => (
                            <span key={t} className="pm-tag">{t}</span>
                          ))}
                        </span>
                        <span className="pm-desc">{ev.description}</span>
                        {badge && <span className="pm-running">{badge}</span>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </Section>

        {myleagueSimId != null ? (
          <MyLeagueStatsSection
            stats={mlStats}
            error={mlError}
            season={season}
            profileAverages={a}
          />
        ) : (
          <Section title="Season averages">
            {error && <p className="pm-empty">{error}</p>}
            {!error && !profile && <p className="pm-empty">Loading…</p>}
            {a && (
              // Supplementary stats only — PTS/REB/AST/FG% already appear as
              // headline tiles in the hero, so this section shows the rest.
              <div className="pm-inline-stats">
                <span><b>GP</b> {a.gp}</span>
                <span><b>MPG</b> {a.min.toFixed(1)}</span>
                <span><b>STL</b> {a.stl.toFixed(1)}</span>
                <span><b>BLK</b> {a.blk.toFixed(1)}</span>
                <span><b>TOV</b> {a.tov.toFixed(1)}</span>
                <span><b>3P%</b> {pct(a.fg3_pct)}</span>
                <span><b>FT%</b> {pct(a.ft_pct)}</span>
              </div>
            )}
          </Section>
        )}

      </div>
    </div>
  );
}

/** MyLeague running-averages section — implements the design-locked contract
 * (project-myleague-stats-contract). Sim is primary reality; real is
 * reference/context. Never blend — both blocks stacked, sim always on top,
 * GP disclosed alongside averages so users can gauge sample size.
 *
 * Semantics for sample-size and 0-GP cases:
 *   ≥ 10 sim GP → sim primary, no tag
 *   1–9 sim GP → "(small sample)" tag after GP
 *   0 GP, team_gp==0 → "0 GP yet" (season fresh)
 *   0 GP, team_gp>0 → "0 GP (unavailable through N team games)"
 */
function MyLeagueStatsSection({
  stats, error, season,
  profileAverages,
}: {
  stats: MyLeaguePlayerStats | null;
  error: string | null;
  season: string;
  profileAverages: PlayerProfile["season_averages"] | undefined;
}) {
  return (
    <Section title="MyLeague statistics">
      {error && <p className="pm-empty">{error}</p>}
      {!error && !stats && <p className="pm-empty">Loading…</p>}
      {stats && (
        <div className="pm-ml-stats">
          <SimBlock sim={stats.sim} />
          <RealBlock real={stats.real} season={season} fallback={profileAverages} />
        </div>
      )}
    </Section>
  );
}

function SimBlock({ sim }: { sim: MyLeaguePlayerStats["sim"] }) {
  // Three distinct 0-GP states + normal render. Copy is intentionally verbose
  // — never leaves the user wondering "which world are these numbers from?".
  if (sim.gp === 0) {
    const label = sim.team_gp === 0
      ? "0 GP yet"
      : `0 GP (unavailable through ${sim.team_gp} team ${sim.team_gp === 1 ? "game" : "games"})`;
    return (
      <div className="pm-ml-block">
        <div className="pm-ml-label">MyLeague — {label}</div>
      </div>
    );
  }
  const smallSample = sim.gp < 10;
  return (
    <div className="pm-ml-block pm-ml-primary">
      <div className="pm-ml-label">
        MyLeague — {sim.gp} GP
        {smallSample && <span className="pm-ml-tag"> (small sample)</span>}
      </div>
      <div className="pm-inline-stats">
        <span><b>PPG</b> {sim.ppg.toFixed(1)}</span>
        <span><b>RPG</b> {sim.rpg.toFixed(1)}</span>
        <span><b>APG</b> {sim.apg.toFixed(1)}</span>
        <span><b>MPG</b> {sim.mpg.toFixed(1)}</span>
        <span><b>SPG</b> {sim.spg.toFixed(1)}</span>
        <span><b>BPG</b> {sim.bpg.toFixed(1)}</span>
        <span><b>TOV</b> {sim.topg.toFixed(1)}</span>
        <span><b>FG%</b> {pct(sim.fg_pct)}</span>
        <span><b>3P%</b> {pct(sim.fg3_pct)}</span>
        <span><b>FT%</b> {pct(sim.ft_pct)}</span>
      </div>
    </div>
  );
}

function RealBlock({
  real, season, fallback,
}: {
  real: MyLeaguePlayerStats["real"];
  season: string;
  fallback: PlayerProfile["season_averages"] | undefined;
}) {
  // real: null when the player has no PlayerSeasonStats row for the
  // MyLeague's season — rookies, retired, or un-ingested. Explicit copy
  // in place of a blank block.
  if (!real) {
    // Rarely, the profile endpoint has averages but the running-stats
    // endpoint returned null (e.g. divergent PSS filters). Fall back so
    // reference numbers still render.
    if (fallback && fallback.gp > 0) {
      return (
        <div className="pm-ml-block pm-ml-reference">
          <div className="pm-ml-label">{season} Real — {fallback.gp} GP <span className="pm-ml-tag">(reference)</span></div>
          <div className="pm-inline-stats">
            <span><b>PPG</b> {fallback.pts.toFixed(1)}</span>
            <span><b>RPG</b> {fallback.reb.toFixed(1)}</span>
            <span><b>APG</b> {fallback.ast.toFixed(1)}</span>
            <span><b>MPG</b> {fallback.min.toFixed(1)}</span>
            <span><b>FG%</b> {pct(fallback.fg_pct)}</span>
          </div>
        </div>
      );
    }
    return (
      <div className="pm-ml-block pm-ml-reference">
        <div className="pm-ml-label pm-ml-empty">No {season} real-life reference</div>
      </div>
    );
  }
  return (
    <div className="pm-ml-block pm-ml-reference">
      <div className="pm-ml-label">
        {season} Real — {real.gp} GP <span className="pm-ml-tag">(reference)</span>
      </div>
      <div className="pm-inline-stats">
        <span><b>PPG</b> {real.ppg.toFixed(1)}</span>
        <span><b>RPG</b> {real.rpg.toFixed(1)}</span>
        <span><b>APG</b> {real.apg.toFixed(1)}</span>
        <span><b>MPG</b> {real.mpg.toFixed(1)}</span>
        <span><b>SPG</b> {real.spg.toFixed(1)}</span>
        <span><b>BPG</b> {real.bpg.toFixed(1)}</span>
        <span><b>TOV</b> {real.topg.toFixed(1)}</span>
        <span><b>FG%</b> {pct(real.fg_pct)}</span>
        <span><b>3P%</b> {pct(real.fg3_pct)}</span>
        <span><b>FT%</b> {pct(real.ft_pct)}</span>
      </div>
    </div>
  );
}

/** Hero variant — bigger number, small caps label. Used in the header band. */
function HeroStat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="pm-hero-stat">
      <span className="pm-hero-stat-k">{k}</span>
      <span className="pm-hero-stat-v">{v}</span>
    </div>
  );
}
