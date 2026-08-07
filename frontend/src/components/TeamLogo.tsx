import { useState } from "react";
import { franchiseFor, FALLBACK_FRANCHISE } from "../data/franchises";

/**
 * Team logo from NBA CDN, era-appropriate via franchise mapping. Falls back to
 * team abbreviation in the franchise primary color if the CDN 404s or the
 * franchise is unknown.
 */

interface Props {
  abbr: string;
  season?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export default function TeamLogo({ abbr, season, size = "md", className = "" }: Props) {
  const [errored, setErrored] = useState(false);
  const fr = franchiseFor(abbr, season) ?? FALLBACK_FRANCHISE;
  const cls = `team-logo team-logo--${size} ${className}`.trim();

  if (errored || !fr.logoUrl) {
    return (
      <div
        className={`${cls} team-logo--fallback`}
        style={{ background: fr.primaryColor, color: "#fff" }}
        aria-label={fr.fullName || abbr}
      >
        {abbr}
      </div>
    );
  }

  return (
    <img
      className={cls}
      src={fr.logoUrl}
      alt={fr.fullName || abbr}
      onError={() => setErrored(true)}
      loading="lazy"
    />
  );
}
