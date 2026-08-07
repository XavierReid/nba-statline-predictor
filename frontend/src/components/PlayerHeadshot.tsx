import { useState } from "react";

/**
 * NBA player headshot from the official CDN. Falls back to a neutral silhouette
 * (initials in a colored circle) if the CDN returns 404 — common for retired
 * or historical players.
 *
 * URL pattern: https://cdn.nba.com/headshots/nba/latest/{size}/{playerId}.png
 * Size options: 260x190 (small), 1040x760 (large). We default to 260x190 for
 * inline lists and pass "large" for modal headers.
 */

interface Props {
  playerId: number;
  name: string;
  size?: "small" | "large";
  className?: string;
}

function headshotUrl(playerId: number, size: "small" | "large"): string {
  const dim = size === "large" ? "1040x760" : "260x190";
  return `https://cdn.nba.com/headshots/nba/latest/${dim}/${playerId}.png`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function PlayerHeadshot({ playerId, name, size = "small", className = "" }: Props) {
  const [errored, setErrored] = useState(false);
  const cls = `player-headshot player-headshot--${size} ${className}`.trim();

  if (errored || !playerId) {
    return (
      <div className={`${cls} player-headshot--fallback`} aria-label={name}>
        {initials(name)}
      </div>
    );
  }

  return (
    <img
      className={cls}
      src={headshotUrl(playerId, size)}
      alt={name}
      onError={() => setErrored(true)}
      loading="lazy"
    />
  );
}
