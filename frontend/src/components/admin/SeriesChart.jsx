import { useEffect, useRef } from "react";
import Chart from "chart.js/auto";
import { useTheme } from "../../context/ThemeContext";
import { chartColors, seriesColors } from "../../lib/chartTheme";

// One series, so no legend: the surrounding card's title names it. Bars get
// rounded tops and a 2px surface gap; the line is 2px with a hover point.
export default function SeriesChart({ type = "bar", labels, values, label, formatValue = String, height = 240 }) {
  const canvasRef = useRef(null);
  const { theme } = useTheme();

  useEffect(() => {
    if (!canvasRef.current) return undefined;
    const isDark = theme === "dark";
    const base = chartColors(isDark);
    const { a: color, surface } = seriesColors(isDark);

    const dataset =
      type === "line"
        ? {
            label,
            data: values,
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBorderColor: surface,
            pointHoverBorderWidth: 2,
            tension: 0.3,
          }
        : {
            label,
            data: values,
            backgroundColor: color,
            borderColor: surface,
            borderWidth: { left: 1, right: 1 },
            borderRadius: { topLeft: 4, topRight: 4 },
            borderSkipped: "bottom",
            maxBarThickness: 40,
          };

    const chart = new Chart(canvasRef.current.getContext("2d"), {
      type,
      data: { labels, datasets: [dataset] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: base.tooltipBg,
            titleFont: { family: "Geist", size: 13 },
            bodyFont: { family: "Inter", size: 13 },
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
            callbacks: { label: (ctx) => `${label}: ${formatValue(ctx.parsed.y)}` },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: base.grid },
            border: { display: false },
            ticks: { font: { family: "Geist", size: 11 }, color: base.tick, precision: 0, callback: (v) => formatValue(v) },
          },
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { font: { family: "Geist", size: 11 }, color: base.tick, maxRotation: 0, autoSkipPadding: 8 },
          },
        },
      },
    });
    return () => chart.destroy();
  }, [type, labels, values, label, formatValue, theme]);

  return (
    <div className="w-full relative" style={{ height }}>
      <canvas ref={canvasRef} role="img" aria-label={label} />
    </div>
  );
}
