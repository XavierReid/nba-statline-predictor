import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BoxScore from "./BoxScore";
import type { PlayerLine } from "../types";

function line(over: Partial<PlayerLine>): PlayerLine {
  return {
    player_id: 0,
    name: "Player",
    minutes: 20,
    points: 10,
    rebounds: 5,
    assists: 3,
    steals: 1,
    blocks: 0,
    turnovers: 2,
    personal_fouls: 2,
    plus_minus: 0,
    fgm: 4,
    fga: 9,
    fg3m: 1,
    fg3a: 3,
    ftm: 1,
    fta: 2,
    fouled_out: false,
    ...over,
  };
}

const players: PlayerLine[] = [
  line({ player_id: 1, name: "Star", points: 30, rebounds: 4 }),
  line({ player_id: 2, name: "Big", points: 8, rebounds: 12 }),
  line({ player_id: 3, name: "FouledOut", points: 12, personal_fouls: 6, fouled_out: true }),
  line({ player_id: 4, name: "BenchDNP", minutes: 0 }),
];

describe("BoxScore", () => {
  it("defaults to sorting by points", () => {
    render(<BoxScore title="Home" players={players} />);
    const rows = screen.getAllByRole("row").slice(1); // drop header
    expect(within(rows[0]).getByText("Star")).toBeInTheDocument();
  });

  it("renders a DNP row for a player with no minutes", () => {
    render(<BoxScore title="Home" players={players} />);
    const dnp = screen.getByText("BenchDNP").closest("tr");
    expect(dnp).toHaveClass("dnp");
    expect(within(dnp!).getByText("DNP")).toBeInTheDocument();
  });

  it("marks a fouled-out player", () => {
    render(<BoxScore title="Home" players={players} />);
    const row = screen.getByText("FouledOut").closest("tr");
    expect(within(row!).getByText("FO")).toBeInTheDocument();
  });

  it("re-sorts when a column header is clicked", () => {
    render(<BoxScore title="Home" players={players} />);
    fireEvent.click(screen.getByText("REB"));
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Big")).toBeInTheDocument(); // 12 reb tops the list
  });
});
