/** Every value maps to a token in src/styles/tokens.css. No raw colors here. */
const v = (name) => `var(--${name})`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      bg: v("bg"),
      surface: v("surface"),
      sunken: v("sunken"),
      hover: v("hover"),
      line: v("line"),
      "line-strong": v("line-strong"),
      text: v("text"),
      muted: v("muted"),
      faint: v("faint"),
      accent: v("accent"),
      "accent-text": v("accent-text"),
      "accent-soft": v("accent-soft"),
      gain: v("gain"),
      loss: v("loss"),
      spy: v("series-spy"),
      qqq: v("series-qqq"),
      scrim: v("scrim"),
    },
    fontFamily: {
      sans: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
      mono: ["Geist Mono", "ui-monospace", "monospace"],
    },
    fontSize: {
      xs: ["12px", "16px"],
      sm: ["13px", "20px"],
      base: ["15px", "24px"],
      lg: ["19px", "28px"],
      xl: ["24px", { lineHeight: "32px", letterSpacing: "-0.015em" }],
      "2xl": ["30px", { lineHeight: "38px", letterSpacing: "-0.02em" }],
      "3xl": ["38px", { lineHeight: "46px", letterSpacing: "-0.022em" }],
    },
    borderRadius: { none: "0", sm: v("r-sm"), md: v("r-md"), lg: v("r-lg"), full: "9999px" },
    boxShadow: { none: "none", pop: v("shadow-pop"), panel: v("shadow-panel") },
    extend: {
      maxWidth: { content: "1200px" },
      transitionTimingFunction: { out: v("ease-out") },
      transitionDuration: { fast: v("dur-fast"), DEFAULT: v("dur"), slow: v("dur-slow") },
    },
  },
  plugins: [],
};
