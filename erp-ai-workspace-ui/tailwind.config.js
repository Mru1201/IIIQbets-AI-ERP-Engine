/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        fiori: {
          blue: "#0070F2",
          "blue-hover": "#0057B8",
          "blue-light": "#EBF3FC",
          dark: "#1D2D3E",
          gray: "#556B82",
          border: "#D9E1E8",
          bg: "#F5F6F7",
          card: "#FFFFFF",
          success: "#107E3E",
          "success-bg": "#EBF6EE",
          error: "#BB0000",
          "error-bg": "#FDF3F3",
          warning: "#E9730C",
          "warning-bg": "#FFF5EA",
        },
      },
      fontFamily: {
        sans: [
          '"72"',
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          '"72Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
}
