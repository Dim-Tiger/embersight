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
          400: "#5b6677",
          200: "#a3afc1",
        },
      },
    },
  },
  plugins: [],
};

export default config;
