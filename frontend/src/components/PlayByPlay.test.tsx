import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PlayByPlay from "./PlayByPlay";
import type { SimEvent, SimulateGameResponse } from "../types";

function ev(over: Partial<SimEvent> & Pick<SimEvent, "type">): SimEvent {
  return {
    possession: 1,
    quarter: 1,
    game_clock_seconds: 600,
    is_home: true,
    pts: 0,
    ...over,
  };
}

function game(events: SimEvent[]): SimulateGameResponse {
  return {
    season: "2025-26",
    seed: 1,
    home_team: "HOU",
    away_team: "OKC",
    home_score: 0,
    away_score: 0,
    quarter_scores: { home: [0, 0, 0, 0], away: [0, 0, 0, 0] },
    home_box: [],
    away_box: [],
    events,
  };
}

// Open the PBP panel — everything below assumes it's expanded.
function openPBP() {
  fireEvent.click(screen.getByRole("button", { name: /Show play-by-play/ }));
}

describe("PlayByPlay", () => {
  it("renders nothing when there are no events", () => {
    const { container } = render(<PlayByPlay game={game([])} />);
    expect(container.textContent).toBe("");
  });

  it("hides the table until the toggle is clicked", () => {
    render(<PlayByPlay game={game([ev({ type: "SHOT", made: true, description: "P hits a layup" })])} />);
    expect(screen.queryByText("P hits a layup")).not.toBeInTheDocument();
    openPBP();
    expect(screen.getByText("P hits a layup")).toBeInTheDocument();
  });

  it("quarter filter shows only rows from the selected quarter", () => {
    const evs = [
      ev({ type: "SHOT", made: true, quarter: 1, description: "Q1 shot" }),
      ev({ type: "SHOT", made: true, quarter: 3, description: "Q3 shot" }),
      ev({ type: "SHOT", made: true, quarter: 4, description: "Q4 shot" }),
    ];
    render(<PlayByPlay game={game(evs)} />);
    openPBP();
    fireEvent.click(screen.getByRole("button", { name: "Q3" }));
    expect(screen.queryByText("Q1 shot")).not.toBeInTheDocument();
    expect(screen.getByText("Q3 shot")).toBeInTheDocument();
    expect(screen.queryByText("Q4 shot")).not.toBeInTheDocument();
  });

  it("shows OT tabs only when the game went to OT", () => {
    const noOT = [ev({ type: "SHOT", made: true, quarter: 4, description: "Q4 shot" })];
    const { rerender } = render(<PlayByPlay game={game(noOT)} />);
    openPBP();
    expect(screen.queryByRole("button", { name: "OT1" })).not.toBeInTheDocument();

    const withOT = [
      ...noOT,
      ev({ type: "SHOT", made: true, quarter: 5, description: "OT shot" }),
    ];
    rerender(<PlayByPlay game={game(withOT)} />);
    expect(screen.getByRole("button", { name: "OT1" })).toBeInTheDocument();
  });

  it("search filter is case-insensitive substring on description (and suffix)", () => {
    const evs = [
      ev({ type: "SHOT", made: true, description: "Alice hits a layup" }),
      ev({ type: "SHOT", made: false, description: "Bob misses a three" }),
    ];
    render(<PlayByPlay game={game(evs)} />);
    openPBP();
    const search = screen.getByRole("searchbox");
    fireEvent.change(search, { target: { value: "LAYUP" } });
    expect(screen.getByText("Alice hits a layup")).toBeInTheDocument();
    expect(screen.queryByText("Bob misses a three")).not.toBeInTheDocument();
  });

  it("chip filter operates on the collated row's event types, so AST surfaces the parent SHOT row", () => {
    // A made shot followed by an assist in the same possession — the AST folds
    // onto the parent SHOT row. Filtering to AST should keep that row visible.
    const evs = [
      ev({ type: "SHOT", possession: 1, player_id: 1, made: true, description: "P1 hits a layup" }),
      ev({ type: "AST", possession: 1, player_id: 2, shot_by: 1, description: "P2 assists P1's layup" }),
      ev({ type: "SHOT", possession: 2, player_id: 3, made: true, description: "P3 hits an unassisted layup" }),
    ];
    render(<PlayByPlay game={game(evs)} />);
    openPBP();
    const chips = screen.getAllByRole("group", { name: "Event type filter" })[0];
    fireEvent.click(within(chips).getByRole("button", { name: "AST" }));
    // Assisted shot row visible (AST folds onto it); unassisted shot row hidden.
    expect(screen.getByText(/P1 hits a layup/)).toBeInTheDocument();
    expect(screen.queryByText("P3 hits an unassisted layup")).not.toBeInTheDocument();
  });

  it("shows an empty state when filters exclude everything", () => {
    render(<PlayByPlay game={game([ev({ type: "SHOT", made: true, description: "only shot" })])} />);
    openPBP();
    const search = screen.getByRole("searchbox");
    fireEvent.change(search, { target: { value: "nonexistent" } });
    expect(screen.getByText("No events match the filter.")).toBeInTheDocument();
  });
});
