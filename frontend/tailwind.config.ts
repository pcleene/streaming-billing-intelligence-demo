import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{html,js,svelte,ts}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Acme-inspired dark palette with magenta/violet accents
        bg:        "rgb(var(--bg) / <alpha-value>)",
        surface:   "rgb(var(--surface) / <alpha-value>)",
        elevated:  "rgb(var(--elevated) / <alpha-value>)",
        border:    "rgb(var(--border) / <alpha-value>)",
        muted:     "rgb(var(--muted) / <alpha-value>)",
        fg:        "rgb(var(--fg) / <alpha-value>)",
        accent:    "rgb(var(--accent) / <alpha-value>)",
        accent2:   "rgb(var(--accent2) / <alpha-value>)",
        ok:        "rgb(var(--ok) / <alpha-value>)",
        warn:      "rgb(var(--warn) / <alpha-value>)",
        danger:    "rgb(var(--danger) / <alpha-value>)"
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"]
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(255,80,180,0.18), 0 6px 30px -8px rgba(255,80,180,0.35)"
      }
    }
  },
  plugins: []
} satisfies Config;
