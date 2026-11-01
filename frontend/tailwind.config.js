/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Dark navy / slate foundation for the enterprise compliance console.
        navy: {
          950: "#070b18",
          900: "#0b1222",
          850: "#101a2e",
          800: "#151f38",
          750: "#1b2744",
          700: "#223052",
          600: "#2d3f68",
        },
        accent: {
          DEFAULT: "#3b82f6",
          soft: "#60a5fa",
          deep: "#1d4ed8",
        },
        // Semantic status colours, consistent across tables, charts and badges.
        status: {
          pass: "#10b981",
          fail: "#f43f5e",
          warn: "#f59e0b",
          info: "#38bdf8",
          blocked: "#a78bfa",
          muted: "#64748b",
        },
      },
      fontFamily: {
        sans: ['"Inter var"', "Inter", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,.28), 0 8px 24px -12px rgba(0,0,0,.55)",
        glow: "0 0 0 1px rgba(59,130,246,.35), 0 8px 32px -12px rgba(59,130,246,.45)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "none" } },
        "pulse-ring": { "0%": { opacity: ".9", transform: "scale(.85)" }, "70%,100%": { opacity: "0", transform: "scale(1.9)" } },
      },
      animation: {
        "fade-in": "fade-in .28s ease-out both",
        "pulse-ring": "pulse-ring 1.8s cubic-bezier(.24,.6,.35,1) infinite",
      },
    },
  },
  plugins: [],
};
