import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ember: {
          50: "#fff7ed",
          200: "#fed7aa",
          // 300 was used across the app but never defined, so those
          // classes silently did nothing. Same value as Tailwind orange-300,
          // which the rest of this scale already follows.
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          900: "#7c2d12",
        },
        smoke: {
          900: "#0b0f14",
          800: "#111722",
          700: "#1a2230",
          600: "#26303f",
          500: "#64748b",
          400: "#94a3b8",
          300: "#cbd5e1",
          200: "#e2e8f0",
          100: "#f1f5f9",
          // Lightest text shade (slate-50, same as the body text colour);
          // referenced by Markdown headings and bold text but missing here.
          50: "#f8fafc",
        },
        // Severity tokens (defined in globals.css). CSS variables so the
        // light theme can swap them without touching components.
        sev: {
          "critical-bg": "var(--sev-critical-bg)",
          "critical-fg": "var(--sev-critical-fg)",
          "critical-edge": "var(--sev-critical-edge)",
          "warning-bg": "var(--sev-warning-bg)",
          "warning-fg": "var(--sev-warning-fg)",
          "warning-edge": "var(--sev-warning-edge)",
          "info-bg": "var(--sev-info-bg)",
          "info-fg": "var(--sev-info-fg)",
          "info-edge": "var(--sev-info-edge)",
          "ok-bg": "var(--sev-ok-bg)",
          "ok-fg": "var(--sev-ok-fg)",
          "ok-edge": "var(--sev-ok-edge)",
          "degraded-bg": "var(--sev-degraded-bg)",
          "degraded-fg": "var(--sev-degraded-fg)",
          "degraded-edge": "var(--sev-degraded-edge)",
        },
      },
    },
  },
  plugins: [],
};

export default config;
