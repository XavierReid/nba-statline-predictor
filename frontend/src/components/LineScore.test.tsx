import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LineScore from "./LineScore";
import type { SimulateGameResponse } from "../types";

function game(over: Partial<SimulateGameResponse> = {}): SimulateGameResponse {
  return {
    season: "2025-26",
    seed: 1,
    home_team: "BOS",
    away_team: "LAL",
    home_score: 110,
    away_score: 104,
    quarter_scores: { home: [28, 27, 30, 25], away: [26, 26, 26, 26] },
    home_box: [],
    away_box: [],
    ...over,
  };
}

describe("LineScore", () => {
  it("shows Q1–Q4 with no OT column in a regulation game", () => {
    render(<LineScore game={game()} />);
    expect(screen.getByText("Q4")).toBeInTheDocument();
    expect(screen.queryByText("OT1")).not.toBeInTheDocument();
    expect(screen.queryByText(/overtime/i)).not.toBeInTheDocument();
  });

  it("adds an OT column when periods exceed 4", () => {
    render(
      <LineScore
        game={game({
          quarter_scores: { home: [28, 27, 30, 19, 6], away: [26, 26, 26, 26, 12] },
          home_score: 110,
          away_score: 116,
        })}
      />
    );
    expect(screen.getByText("OT1")).toBeInTheDocument();
  });

  it("marks the winning row", () => {
    const { container } = render(<LineScore game={game()} />);
    const winnerRow = container.querySelector("tr.winner");
    // Row now displays the era-appropriate franchise name via franchise lookup;
    // "BOS" abbreviation maps to "Boston Celtics".
    expect(winnerRow?.textContent).toContain("Celtics");
  });
});
