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
        // Smoke shades are driven by CSS variables defined in globals.css so
        // that the same utility class (e.g. bg-smoke-900) resolves to a dark
        // value under html.dark and a light value under html.light. The
        // <alpha-value> placeholder keeps Tailwind's `/40`, `/60`, etc.
        // opacity modifiers working.
        smoke: {
          50: "rgb(var(--smoke-50) / <alpha-value>)",
          100: "rgb(var(--smoke-100) / <alpha-value>)",
          200: "rgb(var(--smoke-200) / <alpha-value>)",
          300: "rgb(var(--smoke-300) / <alpha-value>)",
          400: "rgb(var(--smoke-400) / <alpha-value>)",
          500: "rgb(var(--smoke-500) / <alpha-value>)",
          600: "rgb(var(--smoke-600) / <alpha-value>)",
          700: "rgb(var(--smoke-700) / <alpha-value>)",
          800: "rgb(var(--smoke-800) / <alpha-value>)",
          900: "rgb(var(--smoke-900) / <alpha-value>)",
        },
      },
    },
  },
  plugins: [],
};

export default config;
