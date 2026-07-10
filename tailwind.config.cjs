module.exports = {
  content: ["./ui/templates/**/*.html", "./ui/static/react-dashboard.js"],
  theme: {
    extend: {
      colors: {
        brand: {
          blue: "#4472c4",
          blueDark: "#2f5597",
          blueSoft: "#edf4ff",
          orange: "#f4833d",
          orangeDark: "#d76624",
          orangeSoft: "#fff0e7"
        },
        surface: "#ffffff",
        ink: "#102033",
        muted: "#64748b"
      },
      boxShadow: {
        panel: "0 12px 30px rgba(15, 23, 42, 0.08)",
        accent: "0 14px 35px rgba(244, 131, 61, 0.14)"
      }
    }
  },
  plugins: []
};
