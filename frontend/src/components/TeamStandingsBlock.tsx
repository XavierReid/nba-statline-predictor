/**
 * Team standings card — 7-cell grid (Record, Home, Away, PPG, Opp PPG,
 * Blowout%, OT%). Shared between SeasonView and LeagueView's team drill-in.
 */
import type { TeamStandings } from "../lib/teamStandings";

export default function TeamStandingsBlock({ standings: s }: { standings: TeamStandings }) {
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
