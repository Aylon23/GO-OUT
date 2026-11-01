/** Deterministic low-chroma OKLCH hue from a ticker. Lightness/chroma come from theme tokens. */
export function tickerHue(ticker: string): number {
  let h = 2166136261;
  for (let i = 0; i < ticker.length; i++) {
    h ^= ticker.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h) % 360;
}
export const avatarBg = (ticker: string) => `oklch(var(--avatar-l) var(--avatar-c) ${tickerHue(ticker)})`;
