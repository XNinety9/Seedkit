/* seedkit front-end: theme switch and charts. */
(() => {
  "use strict";

  const root = document.documentElement;
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5"];
  // Color slot comes from the server so a tracker keeps its color on every chart.
  const slot = (c, i) => css(SERIES[(c ?? i) % SERIES.length]);

  // Theme -----------------------------------------------------------------

  function setTheme(theme) {
    root.dataset.theme = theme;
    try { localStorage.setItem("seedkit-theme", theme); } catch (e) { /* private mode */ }
    renderCharts(document);
  }
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-theme-toggle]");
    if (toggle) setTheme(root.dataset.theme === "light" ? "dark" : "light");
  });

  // Formatting ------------------------------------------------------------

  const I18N = window.SEEDKIT_I18N || { locale: "en-US", units: ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] };
  const fill = (template, values) => template.replace(/\{(\w+)\}/g, (_, k) => values[k]);
  function number(v, digits) {
    return v.toLocaleString(I18N.locale, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function bytes(n) {
    if (n == null || isNaN(n)) return "–";
    let i = 0;
    let v = Math.abs(n);
    while (v >= 1024 && i < I18N.units.length - 1) { v /= 1024; i++; }
    const digits = i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2;
    return `${number(Math.sign(n) * v, digits)} ${I18N.units[i]}`;
  }
  const dayLabel = (iso) => {
    const d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString(I18N.locale, { day: "numeric", month: "short" });
  };
  window.seedkitBytes = bytes;
  window.seedkitNumber = number;

  // Chart.js defaults -----------------------------------------------------

  function applyDefaults() {
    const C = window.Chart;
    C.defaults.font.family = css("--font") || "Inter, system-ui, sans-serif";
    C.defaults.font.size = 12;
    C.defaults.color = css("--muted");
    C.defaults.borderColor = css("--grid");
    C.defaults.animation.duration = 650;
    C.defaults.maintainAspectRatio = false;
    C.defaults.plugins.legend.display = false;
    const tt = C.defaults.plugins.tooltip;
    tt.backgroundColor = css("--surface-3");
    tt.titleColor = css("--text");
    tt.bodyColor = css("--text-2");
    tt.borderColor = css("--border-strong");
    tt.borderWidth = 1;
    tt.padding = 10;
    tt.cornerRadius = 10;
    tt.boxPadding = 5;
    tt.usePointStyle = true;
    tt.titleFont = { weight: "600" };
  }

  const axis = (extra = {}) => ({
    grid: { color: css("--grid"), drawTicks: false },
    border: { display: false },
    ticks: { padding: 8, color: css("--muted") },
    ...extra,
  });

  function gradientFill(ctx, color) {
    const { chartArea } = ctx.chart;
    if (!chartArea) return color + "22";
    const g = ctx.chart.ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    g.addColorStop(0, color + "55");
    g.addColorStop(1, color + "00");
    return g;
  }

  // Chart builders ---------------------------------------------------------

  const builders = {
    // Tiny trend line for stat tiles.
    spark(data) {
      const color = css(data.color || "--accent");
      return {
        type: "line",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values, borderColor: color, borderWidth: 2, tension: 0.35, fill: true,
            pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: color,
            backgroundColor: (ctx) => gradientFill(ctx, color),
          }],
        },
        options: {
          interaction: { intersect: false, mode: "index" },
          scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
          plugins: {
            tooltip: {
              displayColors: false,
              callbacks: {
                title: (items) => dayLabel(items[0].label),
                label: (item) => data.unit === "bytes" ? bytes(item.raw) : String(item.raw),
              },
            },
          },
        },
      };
    },

    // Daily upload, stacked by tracker.
    daily(data) {
      const many = data.series.length > 1;
      return {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: data.series.map((s, i) => ({
            label: s.name,
            data: s.data,
            backgroundColor: slot(s.c, i),
            borderRadius: { topLeft: 4, topRight: 4 },
            borderSkipped: "bottom",
            maxBarThickness: 24,
            borderColor: css("--surface"),
            borderWidth: many ? { top: 2 } : 0,
          })),
        },
        options: {
          interaction: { intersect: false, mode: "index" },
          scales: {
            x: axis({ stacked: true, grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkipPadding: 14, callback(v) { return dayLabel(this.getLabelForValue(v)); } } }),
            y: axis({ stacked: true, beginAtZero: true, ticks: { color: css("--muted"), padding: 8, maxTicksLimit: 5, callback: (v) => bytes(v) } }),
          },
          plugins: {
            legend: { display: many, position: "top", align: "end", labels: { usePointStyle: true, pointStyle: "rectRounded", boxWidth: 8, boxHeight: 8, color: css("--text-2") } },
            tooltip: {
              callbacks: {
                title: (items) => dayLabel(items[0].label),
                label: (item) => ` ${item.dataset.label}: ${bytes(item.raw)}`,
                footer: (items) => many ? fill(I18N.total, { value: bytes(items.reduce((a, i) => a + i.raw, 0)) }) : "",
              },
            },
          },
        },
      };
    },

    // Share of disk per tracker.
    donut(data) {
      return {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values,
            backgroundColor: data.labels.map((_, i) => slot(data.colors && data.colors[i], i)),
            borderColor: css("--surface"),
            borderWidth: 3,
            borderRadius: 6,
            hoverOffset: 6,
          }],
        },
        options: {
          cutout: "72%",
          plugins: {
            tooltip: { callbacks: { label: (item) => ` ${item.label}: ${bytes(item.raw)}` } },
          },
        },
      };
    },

    // Every torrent: size vs upload per day, colored by tracker.
    scatter(data) {
      return {
        type: "scatter",
        data: {
          datasets: data.datasets.map((d, i) => {
            const color = slot(d.c, i);
            return {
              label: d.name,
              data: d.points,
              backgroundColor: color + "bb",
              borderColor: css("--surface"),
              borderWidth: 1.5,
              pointRadius: 4.5,
              pointHoverRadius: 7,
              pointHoverBorderWidth: 2,
            };
          }),
        },
        options: {
          scales: {
            x: axis({ type: "logarithmic", title: { display: true, text: I18N.size, color: css("--muted") }, ticks: { color: css("--muted"), maxTicksLimit: 8, callback: (v) => bytes(v) } }),
            y: axis({ type: "logarithmic", min: data.floor, title: { display: true, text: fill(I18N.upload_per_day, { window: data.window }), color: css("--muted") }, ticks: { color: css("--muted"), maxTicksLimit: 7, callback: (v) => v <= data.floor ? I18N.le1 : bytes(v) } }),
          },
          plugins: {
            tooltip: {
              callbacks: {
                title: (items) => items[0].raw.n,
                label: (item) => [
                  ` ${item.dataset.label}`,
                  " " + fill(I18N.size_value, { value: bytes(item.raw.x) }),
                  " " + fill(I18N.upload, { value: (item.raw.y <= data.floor ? I18N.le1 : bytes(item.raw.y)) + I18N.per_day }),
                ],
              },
            },
          },
        },
      };
    },

    // Single-series columns (disk by age, etc.).
    columns(data) {
      return {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: [{
            data: data.values,
            backgroundColor: css("--series-1"),
            hoverBackgroundColor: css("--accent-2"),
            borderRadius: { topLeft: 4, topRight: 4 },
            borderSkipped: "bottom",
            maxBarThickness: 24,
          }],
        },
        options: {
          scales: {
            x: axis({ grid: { display: false } }),
            y: axis({ beginAtZero: true, ticks: { color: css("--muted"), padding: 8, maxTicksLimit: 4, callback: (v) => data.unit === "bytes" ? bytes(v) : v } }),
          },
          plugins: {
            tooltip: {
              displayColors: false,
              callbacks: {
                label: (item) => {
                  const v = data.unit === "bytes" ? bytes(item.raw) : item.raw;
                  return data.counts ? `${v} · ${fill(I18N.torrents, { n: data.counts[item.dataIndex] })}` : String(v);
                },
              },
            },
          },
        },
      };
    },
  };

  // Rendering -----------------------------------------------------------------

  const charts = new Map();

  function renderCharts(scope) {
    if (!window.Chart) return;
    applyDefaults();
    scope.querySelectorAll("[data-chart]").forEach((el) => {
      const source = el.querySelector("script[type='application/json']");
      const canvas = el.querySelector("canvas");
      if (!source || !canvas) return;
      const build = builders[el.dataset.chart];
      if (!build) return;
      const data = JSON.parse(source.textContent);
      if (charts.has(canvas)) charts.get(canvas).destroy();
      charts.set(canvas, new window.Chart(canvas, build(data)));
    });
  }

  document.addEventListener("DOMContentLoaded", () => renderCharts(document));
  document.addEventListener("htmx:afterSettle", (e) => renderCharts(e.target));
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => renderCharts(document));
})();
