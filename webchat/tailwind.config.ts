import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#102033",
        mist: "#eef3f8",
        line: "#c8d6e5",
        accent: "#1d5fd0",
        glow: "#d9e8ff",
        foam: "#f8fbff",
        sand: "#f4efe3",
        pulse: "#7dd3fc",
      },
      fontFamily: {
        sans: ["Aptos", "Trebuchet MS", "Segoe UI", "system-ui", "sans-serif"],
        display: ["Cambria", "Georgia", "Times New Roman", "serif"],
      },
      boxShadow: {
        card: "0 16px 48px rgba(16, 32, 51, 0.08)",
        panel: "0 24px 60px rgba(16, 32, 51, 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
