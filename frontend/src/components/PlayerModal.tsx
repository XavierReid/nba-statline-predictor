import { useEffect, useState, type ReactNode } from "react";
import { getPlayerProfile } from "../api";
import type { PlayerLine, PlayerProfile, PossessionEvent } from "../types";
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
}

export default function PlayerModal({ line, season, events, onClose }: Props) {
  const [profile, setProfile] = useState<PlayerProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setProfile(null);
    setError(null);
    getPlayerProfile(line.player_id, season)
      .then(setProfile)
      .catch((e) => setError(String(e)));
  }, [line.player_id, season]);

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
                <div className="pm-hero-stats">
                  <HeroStat k="MIN" v={line.minutes.toFixed(1)} />
                  <HeroStat k="PTS" v={line.points} />
                  <HeroStat k="REB" v={line.rebounds} />
                  <HeroStat k="AST" v={line.assists} />
                  <HeroStat k="FG" v={`${line.fgm}/${line.fga}`} />
                </div>
              </div>
            </div>
          );
        })()}

        <Section title="This game — full line">
          <div className="pm-inline-stats">
            <span><b>STL</b> {line.steals}</span>
            <span><b>BLK</b> {line.blocks}</span>
            <span><b>TO</b> {line.turnovers}</span>
            <span><b>PF</b> {line.personal_fouls}</span>
            <span><b>3PT</b> {line.fg3m}/{line.fg3a}</span>
            <span><b>FT</b> {line.ftm}/{line.fta}</span>
            <span><b>+/-</b> {line.plus_minus > 0 ? `+${line.plus_minus}` : line.plus_minus}</span>
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

        <Section title="Season averages">
          {error && <p className="pm-empty">{error}</p>}
          {!error && !profile && <p className="pm-empty">Loading…</p>}
          {a && (
            <div className="pm-statgrid">
              <Stat k="GP" v={a.gp} />
              <Stat k="MPG" v={a.min.toFixed(1)} />
              <Stat k="PTS" v={a.pts.toFixed(1)} />
              <Stat k="REB" v={a.reb.toFixed(1)} />
              <Stat k="AST" v={a.ast.toFixed(1)} />
              <Stat k="STL" v={a.stl.toFixed(1)} />
              <Stat k="BLK" v={a.blk.toFixed(1)} />
              <Stat k="TOV" v={a.tov.toFixed(1)} />
              <Stat k="FG%" v={pct(a.fg_pct)} />
              <Stat k="3P%" v={pct(a.fg3_pct)} />
              <Stat k="FT%" v={pct(a.ft_pct)} />
            </div>
          )}
        </Section>

        {profile && Object.keys(profile.ratings).length > 0 && (
          <Section title="Ratings">
            <div className="pm-ratings">
              {RATING_ORDER.filter((k) => k in profile.ratings).map((k) => (
                <div className="pm-rating" key={k}>
                  <span className="pm-rating-label">{RATING_LABEL[k] ?? k}</span>
                  <span className="pm-bar">
                    <span className="pm-bar-fill" style={{ width: `${profile.ratings[k]}%` }} />
                  </span>
                  <span className="pm-rating-val">{profile.ratings[k]}</span>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="pm-stat">
      <span className="pm-stat-k">{k}</span>
      <span className="pm-stat-v">{v}</span>
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
