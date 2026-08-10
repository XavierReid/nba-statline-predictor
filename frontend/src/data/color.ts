// Color helpers for readable team-brand rendering on the app's dark theme.

function parseHex(hex: string): [number, number, number] | null {
  const m = hex.trim().match(/^#?([0-9a-f]{6})$/i);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

function toHex(r: number, g: number, b: number): string {
  const c = (x: number) => Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

// Relative luminance (WCAG). Range 0 (black) to 1 (white).
function luminance(r: number, g: number, b: number): number {
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/**
 * Return a version of `hex` that stays legible on the app's dark panel.
 * Colors with luminance below the threshold get mixed toward white until they
 * clear the readability bar. Denver navy #0E2240 → a legible blue-grey; light
 * team colors (Warriors gold, Lakers purple-light) pass through unchanged.
 */
export function readableOnDark(hex: string, minLum = 0.22): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  const [r, g, b] = rgb;
  const L = luminance(r, g, b);
  if (L >= minLum) return hex;
  // Blend toward white until luminance clears the threshold. Binary search on
  // mix ratio keeps the tint close to the original hue.
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 10; i++) {
    const t = (lo + hi) / 2;
    const nr = r + (255 - r) * t;
    const ng = g + (255 - g) * t;
    const nb = b + (255 - b) * t;
    if (luminance(nr, ng, nb) >= minLum) hi = t;
    else lo = t;
  }
  const t = hi;
  return toHex(r + (255 - r) * t, g + (255 - g) * t, b + (255 - b) * t);
}
