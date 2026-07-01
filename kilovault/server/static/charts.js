/* Tiny dependency-free canvas charts: a time-series line chart and an SoC ring.
   No external libraries, so the dashboard works fully offline. */

(function (global) {
  "use strict";

  const COLORS = {
    grid: "#272d38",
    axis: "#8b95a7",
    text: "#8b95a7",
    series: ["#38d39f", "#4ea1ff", "#ffb23e", "#ff5a5f", "#b58bff"],
  };

  function dpr() { return Math.min(global.devicePixelRatio || 1, 3); }

  function fitCanvas(canvas) {
    const ratio = dpr();
    // Use the CSS-rendered size (stable), NOT canvas.width/height — those are
    // the backing-store size we set below, and reading them back here would
    // compound by `ratio` on every redraw until the canvas overflows and the
    // browser shows a broken-canvas placeholder (seen on scaled displays).
    const rect = canvas.getBoundingClientRect();
    const cssW = Math.max(1, Math.round(rect.width || canvas.clientWidth || 300));
    const cssH = Math.max(1, Math.round(
      rect.height || parseInt(canvas.getAttribute("height"), 10) || 180));
    const bw = Math.round(cssW * ratio), bh = Math.round(cssH * ratio);
    if (canvas.width !== bw) canvas.width = bw;
    if (canvas.height !== bh) canvas.height = bh;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    return { ctx, w: cssW, h: cssH };
  }

  function niceTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  /* series: [{ name, color, points: [[t, v], ...] }]
     opts: { unit, zeroLine, fixedMin, fixedMax } */
  function lineChart(canvas, series, opts) {
    opts = opts || {};
    const { ctx, w, h } = fitCanvas(canvas);
    ctx.clearRect(0, 0, w, h);
    const padL = 46, padR = 12, padT = 10, padB = 22;
    const plotW = w - padL - padR, plotH = h - padT - padB;

    let tMin = Infinity, tMax = -Infinity, vMin = Infinity, vMax = -Infinity;
    series.forEach(s => s.points.forEach(([t, v]) => {
      if (v == null || isNaN(v)) return;
      if (t < tMin) tMin = t; if (t > tMax) tMax = t;
      if (v < vMin) vMin = v; if (v > vMax) vMax = v;
    }));
    if (!isFinite(tMin)) {
      ctx.fillStyle = COLORS.text; ctx.font = "13px sans-serif";
      ctx.fillText("No data yet", padL, padT + plotH / 2);
      return;
    }
    if (opts.fixedMin != null) vMin = Math.min(vMin, opts.fixedMin);
    if (opts.fixedMax != null) vMax = Math.max(vMax, opts.fixedMax);
    if (opts.zeroLine) { vMin = Math.min(vMin, 0); vMax = Math.max(vMax, 0); }
    if (vMin === vMax) { vMin -= 1; vMax += 1; }
    const vPad = (vMax - vMin) * 0.08; vMin -= vPad; vMax += vPad;
    if (tMin === tMax) tMax = tMin + 1;

    const X = t => padL + ((t - tMin) / (tMax - tMin)) * plotW;
    const Y = v => padT + (1 - (v - vMin) / (vMax - vMin)) * plotH;

    // grid + y labels
    ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.text;
    ctx.lineWidth = 1; ctx.font = "11px sans-serif"; ctx.textBaseline = "middle";
    const yTicks = 4;
    for (let i = 0; i <= yTicks; i++) {
      const v = vMin + (i / yTicks) * (vMax - vMin);
      const y = Y(v);
      ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillText(v.toFixed(Math.abs(vMax - vMin) < 5 ? 2 : 0), 6, y);
    }
    // zero line emphasis
    if (vMin < 0 && vMax > 0) {
      ctx.strokeStyle = "#3a4150"; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(padL, Y(0)); ctx.lineTo(w - padR, Y(0)); ctx.stroke();
    }
    // x labels (start / mid / end)
    ctx.textBaseline = "top"; ctx.fillStyle = COLORS.text;
    [tMin, (tMin + tMax) / 2, tMax].forEach((t, i) => {
      ctx.textAlign = i === 0 ? "left" : i === 2 ? "right" : "center";
      ctx.fillText(niceTime(t), X(t), h - padB + 5);
    });
    ctx.textAlign = "left";

    // series
    series.forEach((s, si) => {
      ctx.strokeStyle = s.color || COLORS.series[si % COLORS.series.length];
      ctx.lineWidth = 1.8; ctx.beginPath();
      let started = false;
      s.points.forEach(([t, v]) => {
        if (v == null || isNaN(v)) { started = false; return; }
        const x = X(t), y = Y(v);
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });
  }

  /* SoC ring gauge into a small canvas */
  function ring(canvas, pct, label) {
    const { ctx, w, h } = fitCanvas(canvas);
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 8;
    const frac = Math.max(0, Math.min(100, pct)) / 100;
    ctx.lineWidth = 10; ctx.lineCap = "round";
    ctx.strokeStyle = "#272d38";
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    const color = pct <= 15 ? "#ff5a5f" : pct <= 35 ? "#ffb23e" : "#38d39f";
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
    ctx.stroke();
    ctx.fillStyle = "#e7ecf3"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "700 20px sans-serif";
    ctx.fillText(Math.round(pct) + "%", cx, cy - 2);
    if (label) {
      ctx.font = "11px sans-serif"; ctx.fillStyle = "#8b95a7";
      ctx.fillText(label, cx, cy + 16);
    }
  }

  global.KVCharts = { lineChart, ring };
})(window);
