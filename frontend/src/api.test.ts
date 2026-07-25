import { afterEach, describe, expect, it, vi } from "vitest";
import { getPlayerProfile, getSeasons, simulateGame } from "./api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown, ok = true) {
  const fn = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("getSeasons", () => {
  it("drops not-ready seasons and sorts newest first", async () => {
    mockFetch([
      { season: "2016-17", ready: true },
      { season: "2024-25", ready: false },
      { season: "2025-26", ready: true },
    ]);
    const seasons = await getSeasons();
    expect(seasons.map((s) => s.season)).toEqual(["2025-26", "2016-17"]);
  });
});

describe("simulateGame", () => {
  it("posts the matchup, seed, and preset in the config shape the API expects", async () => {
    const fn = mockFetch({ home_score: 100, away_score: 99 });
    await simulateGame({
      home_team: "BOS",
      away_team: "LAL",
      season: "2025-26",
      seed: 7,
      preset: "drama-m3",
      include_pbp: true,
    });
    const [url, opts] = fn.mock.calls[0];
    expect(url).toBe("/simulations/game");
    const sent = JSON.parse((opts as RequestInit).body as string);
    expect(sent).toMatchObject({
      home_team: "BOS",
      away_team: "LAL",
      season: "2025-26",
      seed: 7,
      include_pbp: true,
      config: { preset: "drama-m3" },
    });
  });

  it("sends seed null when omitted (random game)", async () => {
    const fn = mockFetch({ home_score: 1, away_score: 2 });
    await simulateGame({
      home_team: "BOS",
      away_team: "LAL",
      season: "2025-26",
      preset: "drama-m3",
      include_pbp: false,
    });
    const sent = JSON.parse((fn.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.seed).toBeNull();
  });

  it("throws with detail on a failed simulation", async () => {
    mockFetch({ detail: "boom" }, false);
    await expect(
      simulateGame({ home_team: "A", away_team: "B", season: "2025-26", preset: "baseline", include_pbp: false })
    ).rejects.toThrow(/Simulation failed/);
  });
});

describe("getPlayerProfile", () => {
  it("requests the profile endpoint with an encoded season", async () => {
    const fn = mockFetch({ id: 1, ratings: {} });
    await getPlayerProfile(203999, "2025-26");
    expect(fn.mock.calls[0][0]).toBe("/players/203999/profile?season=2025-26");
  });
});
