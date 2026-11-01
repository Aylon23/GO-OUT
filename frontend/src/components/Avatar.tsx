import { avatarBg } from "../lib/avatar";

export function Avatar({ ticker, size = 28 }: { ticker: string; size?: number }) {
  const letter = ticker.replace(/^\^/, "").charAt(0) || "?";
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 select-none items-center justify-center rounded-sm font-mono font-semibold text-text"
      style={{ width: size, height: size, background: avatarBg(ticker), fontSize: Math.round(size * 0.46) }}
    >
      {letter}
    </span>
  );
}
