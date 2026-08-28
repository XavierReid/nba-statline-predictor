/**
 * TeamDrillInView — M-3 read-only team roster inspection for MyLeague.
 *
 * Reachable from:
 *   - Any standings row click on the MyLeague dashboard
 *   - The opponent logo on NextGameCard
 *
 * Layout hierarchy (per Xavier's lock):
 *   team header → record/context → roster (centerpiece) → recent games
 *
 * Roster rendering follows the statistics contract:
 *   - Sim block used when the player has sim history
 *   - Real block ("real reference") when sim GP == 0
 *   - Small-sample tag when 1 ≤ sim GP < 10
 *
 * M-4 availability toggle:
 *   The Avail/OUT chip is interactive when this is the controlled team.
 *   Click → confirm → POST SET_UNAVAILABLE|SET_AVAILABLE event at the
 *   sim's current_calendar_date → refetch. Server enforces:
 *     - team_id === controlled_team_id (opponent mutation rejected)
 *     - player rostered on team for the sim's season
 *     - retroactive-mutation guard (already in the engine)
 *
 * Player rows are clickable → PlayerModal with runningStatsSimId,
 * runningStatsLabel="MyLeague" (matches the design-lock inside a
 * MyLeague context).
 */
import { useEffect, useState } from "react";
import { appendMyLeagueEvent, getMyLeagueTeam } from "../api";
import type {
  PlayerLine,
  TeamDrillInResponse,
  TeamDrillInRosterPlayer,
} from "../types";
import { franchiseFor } from "../data/franchises";
import PlayerModal from "./PlayerModal";
import TeamLogo from "./TeamLogo";

interface TeamDrillInViewProps {
  simId: number;
  teamAbbr: string;
  season: string;
  /** From MyLeague state — decides whether the availability chip is
   * interactive on this drill-in. Only rows on the controlled team
   * can be toggled (rest are read-only, matches the franchise-manager
   * mental model). */
  controlledTeamId: number | null;
  /** From MyLeague state — event's applied_at_date. Matches the "act
   * at the cursor" model; the engine's retroactive-mutation guard
   * enforces this can only affect games AFTER the current cursor. */
  currentCalendarDate: string;
  onBack: () => void;
  onOpenGame: (gameId: string) => void;
  onError: (msg: string) => void;
  backLabel?: string;
}

export default function TeamDrillInView({
  simId, teamAbbr, season, controlledTeamId, currentCalendarDate,
  onBack, onOpenGame, onError,
  backLabel = "← Back",
}: TeamDrillInViewProps) {
  const [data, setData] = useState<TeamDrillInResponse | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<TeamDrillInRosterPlayer | null>(null);
  const [pendingPlayerId, setPendingPlayerId] = useState<number | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    setData(null);
    getMyLeagueTeam(simId, teamAbbr)
      .then(setData)
      .catch((e) => onError(String(e)));
  }, [simId, teamAbbr, onError, reloadTick]);

  const canToggle = data != null && controlledTeamId != null && data.team_id === controlledTeamId;

  async function onToggleAvailability(p: TeamDrillInRosterPlayer) {
    if (!canToggle || !data) return;
    const goingOut = p.availability === "AVAILABLE";
    const verb = goingOut ? "OUT" : "available";
    // Apply from the day AFTER the current cursor so the M-1a
    // retroactive-mutation guard doesn't fire on same-day games that
    // have already been simulated. This is a hard invariant per the
    // engine's "history is what the fold at completion time produced"
    // rule.
    const effective = nextDayIso(currentCalendarDate);
    const confirmed = window.confirm(
      `Mark ${p.name} ${verb} starting ${effective}? `
      + `This affects games from that date forward. Games already `
      + `simulated in this MyLeague are not changed.`
    );
    if (!confirmed) return;
    setPendingPlayerId(p.player_id);
    try {
      await appendMyLeagueEvent(simId, {
        event_type: goingOut ? "SET_UNAVAILABLE" : "SET_AVAILABLE",
        applied_at_date: effective,
        payload: { team_id: data.team_id, player_id: p.player_id },
      });
      setReloadTick((t) => t + 1);   // refetch — server is authoritative
    } catch (e) {
      onError(String(e));
    } finally {
      setPendingPlayerId(null);
    }
  }

  if (!data) return <div className="empty-hint">Loading team…</div>;

  const fr = franchiseFor(data.team_abbr, season);
  const heroStyle = fr
    ? ({ ["--team-accent" as string]: fr.primaryColor } as React.CSSProperties)
    : undefined;

  return (
    <div className="team-drill-in">
      <button className="back-btn" onClick={onBack}>{backLabel}</button>

      {/* Team header */}
      <div className="myleague-hero" style={heroStyle}>
        <TeamLogo abbr={data.team_abbr} size="lg" season={season} />
        <div className="myleague-heading">
          <h2>{data.team_city} {data.team_nickname}</h2>
          <div className="myleague-meta">
            <span className="record">{data.record.wins}-{data.record.losses}</span>
            <span className="dot">·</span>
            <span>{(data.record.pct * 100).toFixed(1)}%</span>
            <span className="dot">·</span>
            <span className={`streak streak-${data.record.streak.startsWith("W") ? "w" : data.record.streak.startsWith("L") ? "l" : "n"}`}>
              {data.record.streak}
            </span>
            <span className="dot">·</span>
            <span>as of {data.as_of_date}</span>
          </div>
        </div>
      </div>

      {/* Record / context strip */}
      {(data.record.wins + data.record.losses) > 0 && (
        <div className="team-drill-context">
          <div className="tdc-cell">
            <span className="tdc-label">Home</span>
            <span className="tdc-value">{data.record.home_wins}-{data.record.home_losses}</span>
          </div>
          <div className="tdc-cell">
            <span className="tdc-label">Away</span>
            <span className="tdc-value">{data.record.away_wins}-{data.record.away_losses}</span>
          </div>
          <div className="tdc-cell">
            <span className="tdc-label">PPG</span>
            <span className="tdc-value">{data.record.ppg_scored.toFixed(1)}</span>
          </div>
          <div className="tdc-cell">
            <span className="tdc-label">Opp PPG</span>
            <span className="tdc-value">{data.record.ppg_allowed.toFixed(1)}</span>
          </div>
        </div>
      )}

      {/* Roster — centerpiece. Two sections: players who have appeared
          in this MyLeague vs those who haven't yet. Both sections are
          part of the active roster; the split makes the sim/real
          source clear per section rather than mixed per row. */}
      <RosterSections
        roster={data.roster}
        onSelect={setSelectedPlayer}
        canToggle={canToggle}
        pendingPlayerId={pendingPlayerId}
        onToggleAvailability={onToggleAvailability}
      />

      {/* Recent games */}
      <section className="team-drill-recent">
        <h3>Recent games</h3>
        {data.recent_games.length === 0 ? (
          <p className="empty-hint">No games played yet.</p>
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
              {data.recent_games.map((g) => (
                <tr
                  key={g.game_id}
                  className="clickable"
                  onClick={() => onOpenGame(g.game_id)}
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
                    {g.away_score}-{g.home_score}
                    {g.went_to_ot && <span className="ot-badge">OT</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Player modal — clicked from roster row */}
      {selectedPlayer && (
        <PlayerModal
          key={selectedPlayer.player_id}
          line={playerLineFromRoster(selectedPlayer)}
          season={season}
          events={[]}
          onClose={() => setSelectedPlayer(null)}
          runningStatsSimId={simId}
          runningStatsLabel="MyLeague"
        />
      )}
    </div>
  );
}

function nextDayIso(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const next = new Date(y, m - 1, d);
  next.setDate(next.getDate() + 1);
  return `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
}

/** Splits the roster into two sections per Xavier's design lock:
 *   - "MyLeague — Played": players with sim GP > 0
 *   - "MyLeague — No games yet": players with sim GP = 0 (still on roster,
 *      just no appearances so real-season reference is shown)
 *
 * Within each section, is_starter first (S badge), then real MPG desc.
 * The two-section layout removes the per-row source flip that made the
 * mixed table hard to read.
 */
function RosterSections({
  roster, onSelect, canToggle, pendingPlayerId, onToggleAvailability,
}: {
  roster: TeamDrillInRosterPlayer[];
  onSelect: (p: TeamDrillInRosterPlayer) => void;
  canToggle: boolean;
  pendingPlayerId: number | null;
  onToggleAvailability: (p: TeamDrillInRosterPlayer) => void;
}) {
  const played: TeamDrillInRosterPlayer[] = [];
  const notYet: TeamDrillInRosterPlayer[] = [];
  for (const p of roster) {
    if (p.sim && p.sim.gp > 0) played.push(p);
    else notYet.push(p);
  }
  return (
    <>
      <section className="team-drill-roster">
        <h3>
          MyLeague — Played
          <span className="tdr-count">{played.length}</span>
        </h3>
        <p className="tdr-note">
          All players are currently available. Player availability and injuries
          will be managed in future MyLeague updates.
        </p>
        {played.length === 0 ? (
          <p className="empty-hint">No players have appeared in this MyLeague yet.</p>
        ) : (
          <RosterTable
            rows={played} source="sim" onSelect={onSelect}
            canToggle={canToggle} pendingPlayerId={pendingPlayerId}
            onToggleAvailability={onToggleAvailability}
          />
        )}
      </section>
      {notYet.length > 0 && (
        <section className="team-drill-roster">
          <h3>
            MyLeague — No games yet
            <span className="tdr-count">{notYet.length}</span>
          </h3>
          <p className="tdr-note">
            These players are on the active roster but haven't logged a
            MyLeague appearance yet. Numbers below are their real-season
            reference stats.
          </p>
          <RosterTable
            rows={notYet} source="real" onSelect={onSelect}
            canToggle={canToggle} pendingPlayerId={pendingPlayerId}
            onToggleAvailability={onToggleAvailability}
          />
        </section>
      )}
    </>
  );
}

function RosterTable({
  rows, source, onSelect,
  canToggle, pendingPlayerId, onToggleAvailability,
}: {
  rows: TeamDrillInRosterPlayer[];
  source: "sim" | "real";
  onSelect: (p: TeamDrillInRosterPlayer) => void;
  canToggle: boolean;
  pendingPlayerId: number | null;
  onToggleAvailability: (p: TeamDrillInRosterPlayer) => void;
}) {
  return (
    <table className="team-drill-roster-table">
      <thead>
        <tr>
          <th className="rp-pos">Pos</th>
          <th className="rp-name">Player</th>
          <th className="rp-status">Status</th>
          <th className="rp-gp">GP</th>
          <th className="rp-num">MPG</th>
          <th className="rp-num">PPG</th>
          <th className="rp-num">RPG</th>
          <th className="rp-num">APG</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <RosterRow
            key={p.player_id} p={p} source={source}
            canToggle={canToggle}
            pending={pendingPlayerId === p.player_id}
            onClick={() => onSelect(p)}
            onToggleAvailability={() => onToggleAvailability(p)}
          />
        ))}
      </tbody>
    </table>
  );
}

function RosterRow({
  p, source, canToggle, pending, onClick, onToggleAvailability,
}: {
  p: TeamDrillInRosterPlayer;
  source: "sim" | "real";
  canToggle: boolean;
  pending: boolean;
  onClick: () => void;
  onToggleAvailability: () => void;
}) {
  const s = p.sim;
  const r = p.real;
  const dash = "—";
  const useSim = source === "sim" && s != null && s.gp > 0;
  const gp = useSim ? s!.gp : (r?.gp ?? dash);
  const mpg = useSim ? s!.mpg.toFixed(1) : (r ? r.mpg.toFixed(1) : dash);
  const ppg = useSim ? s!.ppg.toFixed(1) : (r ? r.ppg.toFixed(1) : dash);
  const rpg = useSim ? s!.rpg.toFixed(1) : (r ? r.rpg.toFixed(1) : dash);
  const apg = useSim ? s!.apg.toFixed(1) : (r ? r.apg.toFixed(1) : dash);
  const smallSample = useSim && s!.gp < 10;
  const chipLabel = pending ? "…" : (p.availability === "AVAILABLE" ? "Avail." : "OUT");
  const chipTitle = canToggle
    ? (p.availability === "AVAILABLE"
        ? `Mark ${p.name} unavailable`
        : `Mark ${p.name} available`)
    : `Availability read-only for this team`;

  return (
    <tr className={`rp-row ${p.is_starter ? "starter" : ""} clickable`} onClick={onClick}>
      <td className="rp-pos">{p.position}</td>
      <td className="rp-name">
        <span className="rp-name-text">{p.name}</span>
        {p.is_starter && <span className="rp-starter-tag">S</span>}
        {smallSample && <span className="rp-src-hint-inline">small sample</span>}
      </td>
      <td className="rp-status">
        {canToggle ? (
          <button
            type="button"
            className={`rp-status-chip rp-status-${p.availability.toLowerCase()} rp-toggle`}
            disabled={pending}
            title={chipTitle}
            onClick={(ev) => {
              ev.stopPropagation();       // don't open the modal
              onToggleAvailability();
            }}
          >
            {chipLabel}
          </button>
        ) : (
          <span
            className={`rp-status-chip rp-status-${p.availability.toLowerCase()}`}
            title={chipTitle}
          >
            {chipLabel}
          </span>
        )}
      </td>
      <td className="rp-gp">{gp}</td>
      <td className="rp-num">{mpg}</td>
      <td className="rp-num">{ppg}</td>
      <td className="rp-num">{rpg}</td>
      <td className="rp-num">{apg}</td>
    </tr>
  );
}

/** PlayerModal expects a PlayerLine (per-game stat line). Roster rows don't
 *  have a per-game context — they're at the roster level. Synthesize a
 *  zero-line so the modal renders identity + sim/real block (from the
 *  runningStatsSimId fetch); the "This game" section will show zeros with
 *  no events, which is honest for a modal opened from a roster panel. */
function playerLineFromRoster(p: TeamDrillInRosterPlayer): PlayerLine {
  return {
    player_id: p.player_id,
    name: p.name,
    minutes: 0, points: 0, rebounds: 0, assists: 0,
    steals: 0, blocks: 0, turnovers: 0, personal_fouls: 0,
    plus_minus: 0, fgm: 0, fga: 0, fg3m: 0, fg3a: 0,
    ftm: 0, fta: 0, fouled_out: false,
  };
}

