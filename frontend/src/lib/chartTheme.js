// Chart.js draws to a <canvas>, so it can't pick up CSS variables the way
// the rest of the UI does — these give each chart theme-appropriate colors
// to match whichever mode is active when it's (re)created.
export function chartColors(isDark) {
  return isDark
    ? {
        line: "#c0c1ff",
        point: "#ffb0cd",
        gradientTop: "rgba(192, 193, 255, 0.45)",
        gradientBottom: "rgba(192, 193, 255, 0.0)",
        grid: "rgba(201, 197, 209, 0.15)",
        tick: "#c9c5d1",
        tooltipBg: "rgba(50, 49, 56, 0.95)",
      }
    : {
        line: "#4648d4",
        point: "#b4136d",
        gradientTop: "rgba(70, 72, 212, 0.5)",
        gradientBottom: "rgba(70, 72, 212, 0.0)",
        grid: "rgba(199, 196, 215, 0.2)",
        tick: "#6c748b",
        tooltipBg: "rgba(25, 28, 30, 0.9)",
      };
}
