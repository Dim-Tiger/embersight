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
        },
      },
    },
  },
  plugins: [],
};

export default config;

// Appended: the dashboard uses text-ember-300 (28 places), bg-ember-300,
// text-ember-100 and ring-ember-800, but those shades were never defined, so
// Tailwind generated no CSS for them and headings like "Why" / "Impact" and
// the chat agent name fell back to the inherited colour. Values are the
// Tailwind orange scale the other ember shades already follow. Added at the
// end of the file to stay clear of the smoke-palette change in PR #40.
const ember = (config.theme?.extend?.colors as Record<string, Record<string, string>>).ember;
ember["100"] = "#ffedd5";
ember["300"] = "#fdba74";
ember["800"] = "#9a3412";
