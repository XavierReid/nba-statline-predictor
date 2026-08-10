/**
 * Season-aware franchise metadata for cosmetic display.
 *
 * Keyed by (era abbreviation, season) so a 2007-08 game references "SEA" and
 * a 2024-25 game references "OKC" for the same underlying franchise id. The
 * backend already returns era-appropriate abbreviations via `team_identity`
 * (see app/services/franchise.py), so callers pass the abbreviation straight
 * through from the API response.
 *
 * Modern-era logos are hotlinked from NBA CDN. For franchises that used a
 * different identity historically (SEA, VAN, NJN, CHH, NOH, CHA Bobcats),
 * we currently fall back to the modern logo but display the era-appropriate
 * NAME. A follow-up may bundle historical SVGs locally for full period
 * fidelity — noted in memory.
 *
 * Team colors are the community-conventional primary colors used by
 * NBA broadcasts; approximate but well-known.
 */

export interface Franchise {
  franchiseId: number;      // stable across relocations
  city: string;
  nickname: string;
  fullName: string;         // "Seattle SuperSonics"
  primaryColor: string;     // hex
  secondaryColor: string;   // hex
  logoUrl: string;          // absolute URL (may hotlink)
  logoAltUrl?: string;      // fallback if primary 404s
}

/**
 * NBA CDN modern logo URL for a franchise id.
 * https://cdn.nba.com/logos/nba/{franchiseId}/primary/L/logo.svg
 */
function nbaCdnLogo(franchiseId: number): string {
  return `https://cdn.nba.com/logos/nba/${franchiseId}/primary/L/logo.svg`;
}

// Modern (2024-25) franchise identities, keyed by current-era abbreviation.
const MODERN: Record<string, Franchise> = {
  ATL: { franchiseId: 1610612737, city: "Atlanta", nickname: "Hawks", fullName: "Atlanta Hawks", primaryColor: "#E03A3E", secondaryColor: "#C1D32F", logoUrl: nbaCdnLogo(1610612737) },
  BOS: { franchiseId: 1610612738, city: "Boston", nickname: "Celtics", fullName: "Boston Celtics", primaryColor: "#007A33", secondaryColor: "#BA9653", logoUrl: nbaCdnLogo(1610612738) },
  BKN: { franchiseId: 1610612751, city: "Brooklyn", nickname: "Nets", fullName: "Brooklyn Nets", primaryColor: "#000000", secondaryColor: "#FFFFFF", logoUrl: nbaCdnLogo(1610612751) },
  CHA: { franchiseId: 1610612766, city: "Charlotte", nickname: "Hornets", fullName: "Charlotte Hornets", primaryColor: "#1D1160", secondaryColor: "#00788C", logoUrl: nbaCdnLogo(1610612766) },
  CHI: { franchiseId: 1610612741, city: "Chicago", nickname: "Bulls", fullName: "Chicago Bulls", primaryColor: "#CE1141", secondaryColor: "#000000", logoUrl: nbaCdnLogo(1610612741) },
  CLE: { franchiseId: 1610612739, city: "Cleveland", nickname: "Cavaliers", fullName: "Cleveland Cavaliers", primaryColor: "#860038", secondaryColor: "#FDBB30", logoUrl: nbaCdnLogo(1610612739) },
  DAL: { franchiseId: 1610612742, city: "Dallas", nickname: "Mavericks", fullName: "Dallas Mavericks", primaryColor: "#00538C", secondaryColor: "#002B5E", logoUrl: nbaCdnLogo(1610612742) },
  DEN: { franchiseId: 1610612743, city: "Denver", nickname: "Nuggets", fullName: "Denver Nuggets", primaryColor: "#0E2240", secondaryColor: "#FEC524", logoUrl: nbaCdnLogo(1610612743) },
  DET: { franchiseId: 1610612765, city: "Detroit", nickname: "Pistons", fullName: "Detroit Pistons", primaryColor: "#C8102E", secondaryColor: "#1D42BA", logoUrl: nbaCdnLogo(1610612765) },
  GSW: { franchiseId: 1610612744, city: "Golden State", nickname: "Warriors", fullName: "Golden State Warriors", primaryColor: "#1D428A", secondaryColor: "#FFC72C", logoUrl: nbaCdnLogo(1610612744) },
  HOU: { franchiseId: 1610612745, city: "Houston", nickname: "Rockets", fullName: "Houston Rockets", primaryColor: "#CE1141", secondaryColor: "#000000", logoUrl: nbaCdnLogo(1610612745) },
  IND: { franchiseId: 1610612754, city: "Indiana", nickname: "Pacers", fullName: "Indiana Pacers", primaryColor: "#002D62", secondaryColor: "#FDBB30", logoUrl: nbaCdnLogo(1610612754) },
  LAC: { franchiseId: 1610612746, city: "Los Angeles", nickname: "Clippers", fullName: "LA Clippers", primaryColor: "#C8102E", secondaryColor: "#1D428A", logoUrl: nbaCdnLogo(1610612746) },
  LAL: { franchiseId: 1610612747, city: "Los Angeles", nickname: "Lakers", fullName: "Los Angeles Lakers", primaryColor: "#552583", secondaryColor: "#FDB927", logoUrl: nbaCdnLogo(1610612747) },
  MEM: { franchiseId: 1610612763, city: "Memphis", nickname: "Grizzlies", fullName: "Memphis Grizzlies", primaryColor: "#5D76A9", secondaryColor: "#12173F", logoUrl: nbaCdnLogo(1610612763) },
  MIA: { franchiseId: 1610612748, city: "Miami", nickname: "Heat", fullName: "Miami Heat", primaryColor: "#98002E", secondaryColor: "#F9A01B", logoUrl: nbaCdnLogo(1610612748) },
  MIL: { franchiseId: 1610612749, city: "Milwaukee", nickname: "Bucks", fullName: "Milwaukee Bucks", primaryColor: "#00471B", secondaryColor: "#EEE1C6", logoUrl: nbaCdnLogo(1610612749) },
  MIN: { franchiseId: 1610612750, city: "Minnesota", nickname: "Timberwolves", fullName: "Minnesota Timberwolves", primaryColor: "#0C2340", secondaryColor: "#236192", logoUrl: nbaCdnLogo(1610612750) },
  NOP: { franchiseId: 1610612740, city: "New Orleans", nickname: "Pelicans", fullName: "New Orleans Pelicans", primaryColor: "#0C2340", secondaryColor: "#C8102E", logoUrl: nbaCdnLogo(1610612740) },
  NYK: { franchiseId: 1610612752, city: "New York", nickname: "Knicks", fullName: "New York Knicks", primaryColor: "#006BB6", secondaryColor: "#F58426", logoUrl: nbaCdnLogo(1610612752) },
  OKC: { franchiseId: 1610612760, city: "Oklahoma City", nickname: "Thunder", fullName: "Oklahoma City Thunder", primaryColor: "#007AC1", secondaryColor: "#EF3B24", logoUrl: nbaCdnLogo(1610612760) },
  ORL: { franchiseId: 1610612753, city: "Orlando", nickname: "Magic", fullName: "Orlando Magic", primaryColor: "#0077C0", secondaryColor: "#C4CED4", logoUrl: nbaCdnLogo(1610612753) },
  PHI: { franchiseId: 1610612755, city: "Philadelphia", nickname: "76ers", fullName: "Philadelphia 76ers", primaryColor: "#006BB6", secondaryColor: "#ED174C", logoUrl: nbaCdnLogo(1610612755) },
  PHX: { franchiseId: 1610612756, city: "Phoenix", nickname: "Suns", fullName: "Phoenix Suns", primaryColor: "#1D1160", secondaryColor: "#E56020", logoUrl: nbaCdnLogo(1610612756) },
  POR: { franchiseId: 1610612757, city: "Portland", nickname: "Trail Blazers", fullName: "Portland Trail Blazers", primaryColor: "#E03A3E", secondaryColor: "#000000", logoUrl: nbaCdnLogo(1610612757) },
  SAC: { franchiseId: 1610612758, city: "Sacramento", nickname: "Kings", fullName: "Sacramento Kings", primaryColor: "#5A2D81", secondaryColor: "#63727A", logoUrl: nbaCdnLogo(1610612758) },
  SAS: { franchiseId: 1610612759, city: "San Antonio", nickname: "Spurs", fullName: "San Antonio Spurs", primaryColor: "#C4CED4", secondaryColor: "#000000", logoUrl: nbaCdnLogo(1610612759) },
  TOR: { franchiseId: 1610612761, city: "Toronto", nickname: "Raptors", fullName: "Toronto Raptors", primaryColor: "#CE1141", secondaryColor: "#000000", logoUrl: nbaCdnLogo(1610612761) },
  UTA: { franchiseId: 1610612762, city: "Utah", nickname: "Jazz", fullName: "Utah Jazz", primaryColor: "#002B5C", secondaryColor: "#00471B", logoUrl: nbaCdnLogo(1610612762) },
  WAS: { franchiseId: 1610612764, city: "Washington", nickname: "Wizards", fullName: "Washington Wizards", primaryColor: "#002B5C", secondaryColor: "#E31837", logoUrl: nbaCdnLogo(1610612764) },
};

/**
 * Historical era variants — abbr → override franchise identity.
 * Era-correct logos live under `frontend/public/logos/historical/` and are
 * served by Vite from `/logos/historical/*`. Mix of SVG and PNG per source.
 * NOH/NOK share the New Orleans Hornets logo.
 */
const HISTORICAL: Record<string, Franchise> = {
  SEA: { franchiseId: 1610612760, city: "Seattle", nickname: "SuperSonics", fullName: "Seattle SuperSonics", primaryColor: "#006B3F", secondaryColor: "#FFC72C", logoUrl: "/logos/historical/sea.svg" },
  VAN: { franchiseId: 1610612763, city: "Vancouver", nickname: "Grizzlies", fullName: "Vancouver Grizzlies", primaryColor: "#00707B", secondaryColor: "#8B2942", logoUrl: "/logos/historical/van.svg" },
  NJN: { franchiseId: 1610612751, city: "New Jersey", nickname: "Nets", fullName: "New Jersey Nets", primaryColor: "#002F6C", secondaryColor: "#C8102E", logoUrl: "/logos/historical/njn.svg" },
  CHH: { franchiseId: 1610612740, city: "Charlotte", nickname: "Hornets", fullName: "Charlotte Hornets (original)", primaryColor: "#1D1160", secondaryColor: "#00788C", logoUrl: "/logos/historical/chh.png" },
  NOH: { franchiseId: 1610612740, city: "New Orleans", nickname: "Hornets", fullName: "New Orleans Hornets", primaryColor: "#00778B", secondaryColor: "#F8A81C", logoUrl: "/logos/historical/noh.png" },
  NOK: { franchiseId: 1610612740, city: "New Orleans/Oklahoma City", nickname: "Hornets", fullName: "New Orleans/Oklahoma City Hornets", primaryColor: "#00778B", secondaryColor: "#F8A81C", logoUrl: "/logos/historical/noh.png" },
  // CHA is used both by Bobcats (2004-2014) and Hornets revival (2014+).
  // The abbreviation is the same but the modern entry above serves post-2014
  // Hornets. Historical Bobcats era is displayed via a season-aware helper.
};

/** Franchise lookup — pass the abbreviation the backend returned. */
export function franchiseFor(abbr: string, season?: string): Franchise | null {
  const abbrUpper = abbr?.toUpperCase();
  if (!abbrUpper) return null;

  // Charlotte Bobcats era: abbr is "CHA" but franchise is 1610612766 during 2004-05 through 2013-14.
  if (abbrUpper === "CHA" && season) {
    const year = parseInt(season.split("-")[0], 10);
    if (year >= 2004 && year <= 2013) {
      return {
        franchiseId: 1610612766,
        city: "Charlotte",
        nickname: "Bobcats",
        fullName: "Charlotte Bobcats",
        primaryColor: "#00788C",
        secondaryColor: "#F26522",
        logoUrl: "/logos/historical/cha-bobcats.png",
      };
    }
  }

  return HISTORICAL[abbrUpper] ?? MODERN[abbrUpper] ?? null;
}

/** Fallback franchise for unknown abbreviations (defensive display). */
export const FALLBACK_FRANCHISE: Franchise = {
  franchiseId: 0,
  city: "",
  nickname: "",
  fullName: "Unknown",
  primaryColor: "#6b7280",
  secondaryColor: "#e5e7eb",
  logoUrl: "",
};
