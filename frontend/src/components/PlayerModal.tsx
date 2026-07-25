import { useEffect, useState, type ReactNode } from "react";
import { getPlayerProfile } from "../api";
import type { PlayerLine, PlayerProfile, PossessionEvent } from "../types";

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

// Which ways this player is involved in an event (a single event may match more than one).
function involvement(ev: PossessionEvent, id: number): string[] {
  const tags: string[] = [];
  if (ev.scorer === id) tags.push(ev.made ? "SCORE" : "SHOT");
  if (ev.assisted_by === id) tags.push("AST");
  if (ev.rebounded_by === id) tags.push("REB");
  if (ev.steal_by === id) tags.push("STL");
  if (ev.block_by === id) tags.push("BLK");
  if (ev.turnover_by === id) tags.push("TOV");
  if (ev.fouled_by === id || ev.nonshooting_foul_by === id) tags.push("FOUL");
  return tags;
}

// order in which filter chips appear
const TAG_ORDER = ["SCORE", "SHOT", "AST", "REB", "STL", "BLK", "TOV", "FOUL"];

function pct(x: number | null): string {
  return x == null ? "—" : `${(x * 100).toFixed(1)}%`;
}

const RATING_ORDER = [
  "overall", "three_point", "mid_range", "layup", "passing", "ball_handle",
  "perimeter_defense", "interior_defense", "offensive_rebound", "defensive_rebound", "clutch",
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

        <Section title="">
          <div className="pm-header">
            <span className="pm-name">{line.name}</span>
            <span className="pm-sub">
              {profile ? `${profile.position ?? "—"} · ${profile.team ?? "—"} · ${season}` : season}
            </span>
          </div>
        </Section>

        <Section title="This game">
          <div className="pm-statgrid">
            <Stat k="MIN" v={line.minutes.toFixed(1)} />
            <Stat k="PTS" v={line.points} />
            <Stat k="REB" v={line.rebounds} />
            <Stat k="AST" v={line.assists} />
            <Stat k="STL" v={line.steals} />
            <Stat k="BLK" v={line.blocks} />
            <Stat k="TOV" v={line.turnovers} />
            <Stat k="PF" v={line.personal_fouls} />
            <Stat k="FG" v={`${line.fgm}/${line.fga}`} />
            <Stat k="3PT" v={`${line.fg3m}/${line.fg3a}`} />
            <Stat k="FT" v={`${line.ftm}/${line.fta}`} />
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
                    return (
                      <li key={i}>
                        <span className="pm-clock">{period} {clock(ev.game_clock_seconds)}</span>
                        <span className="pm-tags">
                          {involvement(ev, line.player_id).map((t) => (
                            <span key={t} className="pm-tag">{t}</span>
                          ))}
                        </span>
                        <span className="pm-desc">{ev.description}</span>
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
