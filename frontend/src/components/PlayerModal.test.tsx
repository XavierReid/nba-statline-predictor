import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlayerModal from "./PlayerModal";
import type { PlayerLine, PlayerProfile, PossessionEvent } from "../types";
import * as api from "../api";

vi.mock("../api", () => ({ getPlayerProfile: vi.fn() }));

const profile: PlayerProfile = {
  id: 5, full_name: "Test Guy", position: "G", team: "BOS", season: "2025-26",
  season_averages: { gp: 70, min: 32, pts: 24.5, reb: 4, ast: 6, stl: 1, blk: 0.2, tov: 2, fg_pct: 0.45, fg3_pct: 0.4, ft_pct: 0.9 },
  ratings: { overall: 90, three_point: 95, clutch: 88 },
};

function line(over: Partial<PlayerLine> = {}): PlayerLine {
  return {
    player_id: 5, name: "Test Guy", minutes: 30, points: 25, rebounds: 5, assists: 6,
    steals: 1, blocks: 0, turnovers: 2, personal_fouls: 2, plus_minus: 0,
    fgm: 9, fga: 15, fg3m: 3, fg3a: 6, ftm: 4, fta: 4, fouled_out: false, ...over,
  };
}

function ev(over: Partial<PossessionEvent> & Pick<PossessionEvent, "type">): PossessionEvent {
  return { possession: 1, game_clock_seconds: 600, quarter: 1, is_home: true, pts: 0, ...over };
}

const events: PossessionEvent[] = [
  ev({ type: "SHOT", player_id: 5, made: true, pts: 2, description: "Test Guy makes a layup" }),
  ev({ type: "SHOT", player_id: 99, made: true, pts: 2, description: "Someone Else scores" }),
  ev({ type: "AST", player_id: 5, description: "Test Guy assist" }),
];

beforeEach(() => vi.mocked(api.getPlayerProfile).mockResolvedValue(profile));

describe("PlayerModal", () => {
  it("shows only the player's own PBP events, tagged by involvement", async () => {
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={events} onClose={() => {}} />);
    expect(screen.getByText("Test Guy makes a layup")).toBeInTheDocument();
    expect(screen.getByText("Test Guy assist")).toBeInTheDocument();
    expect(screen.queryByText("Someone Else scores")).not.toBeInTheDocument();
    // scope to the PBP involvement tags ("AST" also appears as a stat-column label)
    const tags = [...container.querySelectorAll(".pm-tag")].map((t) => t.textContent);
    expect(tags).toContain("SCORE");
    expect(tags).toContain("AST");
  });

  it("tags a non-shooting foul as FOUL", () => {
    const evs = [ev({
      type: "FOUL", player_id: 5, foul_kind: "non_shooting", fouled_on: 99,
      description: "Test Guy commits a non-shooting foul on X",
    })];
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={evs} onClose={() => {}} />);
    const tags = [...container.querySelectorAll(".pm-tag")].map((t) => t.textContent);
    expect(tags).toContain("FOUL");
  });

  it("tags a free throw as FT (not SHOT)", () => {
    // Previously bonus FTs and foul-drawn misses were mis-tagged SHOT because
    // the legacy shape put the fouled-on player in `scorer`. Under typed events
    // FT has its own type + chip.
    const evs = [ev({
      type: "FT", player_id: 5, attempt: 1, of: 2, made: true, pts: 1,
      description: "Test Guy makes free throw 1 of 2",
    })];
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={evs} onClose={() => {}} />);
    const tags = [...container.querySelectorAll(".pm-tag")].map((t) => t.textContent);
    expect(tags).toContain("FT");
    expect(tags).not.toContain("SHOT");
  });

  it("hides events whose tag is toggled off", () => {
    render(<PlayerModal line={line()} season="2025-26" events={events} onClose={() => {}} />);
    const scoreChip = screen.getAllByRole("button").find((b) => b.textContent === "SCORE")!;
    fireEvent.click(scoreChip);
    expect(screen.queryByText("Test Guy makes a layup")).not.toBeInTheDocument();
    expect(screen.getByText("Test Guy assist")).toBeInTheDocument();
  });

  it("shows an empty message when the player has no events", () => {
    render(<PlayerModal line={line()} season="2025-26" events={[]} onClose={() => {}} />);
    expect(screen.getByText("No play-by-play events.")).toBeInTheDocument();
  });

  it("renders season averages and ratings once the profile loads", async () => {
    render(<PlayerModal line={line()} season="2025-26" events={events} onClose={() => {}} />);
    expect(await screen.findByText("24.5")).toBeInTheDocument(); // season PTS
    // Ratings section heading is unique (season averages section has its own header).
    expect(screen.getByText("Ratings")).toBeInTheDocument();
  });

  it("closes on overlay click", () => {
    const onClose = vi.fn();
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={[]} onClose={onClose} />);
    (container.querySelector(".pm-overlay") as HTMLElement).click();
    expect(onClose).toHaveBeenCalled();
  });

  it("renders inline running per-player stats on scoring/counting events", () => {
    const evs: PossessionEvent[] = [
      ev({ type: "SHOT", player_id: 5, made: true, shot_type: "layup", pts: 2, description: "Test Guy makes a layup" }),
      ev({ type: "SHOT", player_id: 5, made: true, shot_type: "above_break_three", pts: 3, description: "Test Guy hits a three" }),
      ev({ type: "REB", player_id: 5, is_oreb: false, description: "Test Guy defensive rebound" }),
      ev({ type: "AST", player_id: 5, description: "Test Guy assist" }),
      ev({ type: "SHOT", player_id: 5, made: false, shot_type: "mid_range", pts: 0, description: "Test Guy misses a mid-range jumper" }),
    ];
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={evs} onClose={() => {}} />);
    const badges = [...container.querySelectorAll(".pm-running")].map((b) => b.textContent);
    // Running total accumulates: 2 -> 5 pts, then REB 1, AST 1. Missed shot has no badge.
    expect(badges).toEqual(["PTS 2", "PTS 5", "REB 1", "AST 1"]);
  });

  it("running-stat totals are stable when chip filters toggle (accumulate on full stream, not filtered)", () => {
    const evs: PossessionEvent[] = [
      ev({ type: "SHOT", player_id: 5, made: true, shot_type: "layup", pts: 2, description: "Test Guy makes a layup" }),
      ev({ type: "REB", player_id: 5, is_oreb: false, description: "Test Guy defensive rebound" }),
      ev({ type: "SHOT", player_id: 5, made: true, shot_type: "layup", pts: 2, description: "Test Guy makes a layup again" }),
    ];
    const { container } = render(<PlayerModal line={line()} season="2025-26" events={evs} onClose={() => {}} />);
    // Toggle REB off — the second SHOT's running PTS should still read 4, not 2.
    const rebChip = screen.getAllByRole("button").find((b) => b.textContent === "REB")!;
    fireEvent.click(rebChip);
    const badges = [...container.querySelectorAll(".pm-running")].map((b) => b.textContent);
    expect(badges).toEqual(["PTS 2", "PTS 4"]);
  });
});
