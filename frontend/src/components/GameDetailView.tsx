/**
 * GameDetailView — full box/PBP drill-in for a single simulated game.
 *
 * Shared between LeagueView, MyLeagueView, and anywhere else a game
 * drill-in is needed. Fetches via getSeasonGame (works for any scope's
 * persisted games) and renders the standard game-detail stack:
 *   back button → GameContextHeader → LineScore → boxscores → PBP → PlayerModal
 */
import { useEffect, useState } from "react";
import { getSeasonGame } from "../api";
import type { PlayerLine, SimulateGameResponse } from "../types";
import BoxScore from "./BoxScore";
import GameContextHeader from "./GameContextHeader";
import LineScore from "./LineScore";
import PlayByPlay from "./PlayByPlay";
import PlayerModal from "./PlayerModal";

interface GameDetailViewProps {
  simId: number;
  gameId: string;
  onBack: () => void;
  onError: (msg: string) => void;
  backLabel?: string;
  /** When set, PlayerModal fetches MyLeague running averages for this sim
   * and shows the sim-vs-real split. Omitted for Season Sim / League Sim
   * drill-ins where the baseline profile-endpoint view is correct. */
  myleagueSimId?: number;
}

export default function GameDetailView({
  simId, gameId, onBack, onError, backLabel = "← Back", myleagueSimId,
}: GameDetailViewProps) {
  const [game, setGame] = useState<SimulateGameResponse | null>(null);
  const [selectedPlayer, setSelectedPlayer] = useState<PlayerLine | null>(null);

  useEffect(() => {
    getSeasonGame(simId, gameId)
      .then(setGame)
      .catch((e) => onError(String(e)));
  }, [simId, gameId, onError]);

  if (!game) return <div className="empty-hint">Loading game…</div>;

  return (
    <div className="league-game-detail">
      <button className="back-btn" onClick={onBack}>{backLabel}</button>
      <GameContextHeader game={game} />
      <LineScore game={game} />
      <div className="boxes">
        <BoxScore
          title={game.away_team}
          players={game.away_box}
          abbr={game.away_team}
          season={game.season}
          sideLabel="Away"
          onSelectPlayer={setSelectedPlayer}
        />
        <BoxScore
          title={game.home_team}
          players={game.home_box}
          abbr={game.home_team}
          season={game.season}
          sideLabel="Home"
          onSelectPlayer={setSelectedPlayer}
        />
      </div>
      {game.events && game.events.length > 0 && (
        <PlayByPlay game={game} />
      )}
      {selectedPlayer && (
        <PlayerModal
          key={selectedPlayer.player_id}
          line={selectedPlayer}
          season={game.season}
          events={game.events ?? []}
          onClose={() => setSelectedPlayer(null)}
          myleagueSimId={myleagueSimId}
        />
      )}
    </div>
  );
}
