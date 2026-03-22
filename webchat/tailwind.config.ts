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
      },
      fontFamily: {
        sans: ["Segoe UI", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 16px 48px rgba(16, 32, 51, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
