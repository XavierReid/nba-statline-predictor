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
 *   - "OUT so far" hint when the team has played but the player hasn't
 *   - Availability chip is read-only in M-3; M-4 turns it into a control
 *
 * Player rows are clickable → PlayerModal with runningStatsSimId,
 * runningStatsLabel="MyLeague" (matches the design-lock inside a
 * MyLeague context).
 */
import { useEffect, useState } from "react";
import { getMyLeagueTeam } from "../api";
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
  onBack: () => void;
  onOpenGame: (gameId: string) => void;
  onError: (msg: string) => void;
  backLabel?: string;
}

export default function TeamDrillInView({
  simId, teamAbbr, season, onBack, onOpenGame, onError,
  backLabel = "← Back",
}: TeamDrillInViewProps) {
  const [data, setData] = useState<TeamDrillInResponse | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<TeamDrillInRosterPlayer | null>(null);

  useEffect(() => {
    setData(null);
    getMyLeagueTeam(simId, teamAbbr)
      .then(setData)
      .catch((e) => onError(String(e)));
  }, [simId, teamAbbr, onError]);

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
  roster, onSelect,
}: {
  roster: TeamDrillInRosterPlayer[];
  onSelect: (p: TeamDrillInRosterPlayer) => void;
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
          <RosterTable rows={played} source="sim" onSelect={onSelect} />
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
          <RosterTable rows={notYet} source="real" onSelect={onSelect} />
        </section>
      )}
    </>
  );
}

function RosterTable({
  rows, source, onSelect,
}: {
  rows: TeamDrillInRosterPlayer[];
  source: "sim" | "real";
  onSelect: (p: TeamDrillInRosterPlayer) => void;
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
            onClick={() => onSelect(p)}
          />
        ))}
      </tbody>
    </table>
  );
}

function RosterRow({
  p, source, onClick,
}: {
  p: TeamDrillInRosterPlayer;
  source: "sim" | "real";
  onClick: () => void;
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

  return (
    <tr className={`rp-row ${p.is_starter ? "starter" : ""} clickable`} onClick={onClick}>
      <td className="rp-pos">{p.position}</td>
      <td className="rp-name">
        <span className="rp-name-text">{p.name}</span>
        {p.is_starter && <span className="rp-starter-tag">S</span>}
        {smallSample && <span className="rp-src-hint-inline">small sample</span>}
      </td>
      <td className="rp-status">
        <span className={`rp-status-chip rp-status-${p.availability.toLowerCase()}`}>
          {p.availability === "AVAILABLE" ? "Avail." : "OUT"}
        </span>
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

