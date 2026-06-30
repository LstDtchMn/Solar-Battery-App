/* KiloVault HLX+ Monitor dashboard logic. Fully offline; talks to the local
   server over fetch() + Server-Sent Events. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const batteries = {};   // address -> latest battery dict
  let bankState = {};

  // ---- tabs ---------------------------------------------------------
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tabpane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "history") loadHistory();
      if (btn.dataset.tab === "events") loadEvents();
    });
  });

  // ---- clock --------------------------------------------------------
  setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);

  // ---- SSE ----------------------------------------------------------
  function connect() {
    const es = new EventSource("/api/stream");
    es.onopen = () => setConn(true);
    es.onerror = () => { setConn(false); };
    es.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch (_) { return; }
      if (msg.type === "snapshot") {
        const s = msg.snapshot;
        bankState = s.bank;
        (s.batteries || []).forEach((b) => { batteries[b.address] = b; });
        renderBank(); renderCards(); populateBatterySelect();
      } else if (msg.type === "sample" || msg.type === "state") {
        batteries[msg.address] = msg.battery;
        // bank totals are recomputed server-side on the next snapshot; do a light
        // local refresh from current batteries for responsiveness.
        renderCards();
        maybeUpdateBank();
      }
    };
  }

  function setConn(ok) {
    const p = $("conn-pill");
    p.textContent = ok ? "live" : "reconnecting…";
    p.className = "pill " + (ok ? "pill-ok" : "pill-idle");
  }

  // periodically pull a fresh snapshot to keep bank totals & events exact
  setInterval(async () => {
    try {
      const r = await fetch("/api/snapshot");
      const s = await r.json();
      bankState = s.bank;
      (s.batteries || []).forEach((b) => { batteries[b.address] = b; });
      $("transport-pill").textContent = s.transport;
      renderBank();
    } catch (_) {}
  }, 5000);

  function maybeUpdateBank() {
    // quick client-side recompute of headline numbers between snapshots
    const live = Object.values(batteries).filter((b) => b.sample);
    if (!live.length) return;
    let p = 0, c = 0, rem = 0, cap = 0, socw = 0, v = 0;
    live.forEach((b) => {
      const s = b.sample;
      p += s.power; c += s.current; rem += s.remaining_capacity;
      cap += b.capacity_ah; socw += s.soc * b.capacity_ah; v += s.voltage;
    });
    bankState.total_power = +p.toFixed(1);
    bankState.total_current = +c.toFixed(2);
    bankState.remaining_capacity_ah = +rem.toFixed(1);
    bankState.soc = cap ? +(socw / cap).toFixed(1) : 0;
    bankState.avg_voltage = +(v / live.length).toFixed(3);
    renderBank();
  }

  // ---- bank summary -------------------------------------------------
  function renderBank() {
    const b = bankState || {};
    setText("bank-soc", fmt(b.soc, 0));
    setText("bank-power", fmt(b.total_power, 0));
    setText("bank-current", fmt(b.total_current, 1));
    setText("bank-voltage", fmt(b.avg_voltage, 2));
    setText("bank-remaining", fmt(b.remaining_capacity_ah, 1));
    setText("bank-capacity", fmt(b.total_capacity_ah, 0));
    setText("bank-count", (b.online_count != null ? b.online_count : 0) + "/" + (b.battery_count || 0));
    setText("bank-delta", b.bank_cell_delta != null ? Math.round(b.bank_cell_delta * 1000) : "—");
    setText("bank-wh-in", fmt(b.wh_charged, 0));
    setText("bank-wh-out", fmt(b.wh_discharged, 0));
    setText("bank-temp", (b.min_temperature != null) ? `${b.min_temperature}–${b.max_temperature} °C` : "—");
    renderAlarmBanner(b.alarms || []);
  }

  function renderAlarmBanner(alarms) {
    const el = $("alarm-banner");
    if (!alarms.length) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    el.classList.remove("warn");
    el.textContent = "⚠ Active alarms: " + alarms.join(", ");
  }

  // ---- battery cards ------------------------------------------------
  function renderCards() {
    const grid = $("battery-grid");
    const list = Object.values(batteries).sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    // create/update card nodes
    list.forEach((bat) => {
      let card = document.getElementById("card-" + cssId(bat.address));
      if (!card) { card = buildCard(bat); grid.appendChild(card); }
      updateCard(card, bat);
    });
  }

  function buildCard(bat) {
    const card = document.createElement("div");
    card.className = "card";
    card.id = "card-" + cssId(bat.address);
    card.innerHTML = `
      <div class="card-head">
        <div class="card-name"><span class="nm"></span><span class="edit" title="Rename">✎</span></div>
        <span class="state-tag"></span>
      </div>
      <div class="soc-row">
        <canvas class="ring" width="96" height="96"></canvas>
        <div class="soc-meta">
          <div><span class="soc-num">—</span></div>
          <small class="rem"></small>
          <small class="soh"></small>
          <small class="eta"></small>
        </div>
      </div>
      <div class="kv-grid">
        <div class="kv"><label>Voltage</label><div class="v voltage">—</div></div>
        <div class="kv"><label>Current</label><div class="v current">—</div></div>
        <div class="kv"><label>Power</label><div class="v power">—</div></div>
        <div class="kv"><label>Temp</label><div class="v temp">—</div></div>
        <div class="kv"><label>Cycles</label><div class="v cycles">—</div></div>
        <div class="kv"><label>Cell Δ</label><div class="v delta">—</div></div>
      </div>
      <div class="cells">
        <div class="cells-head"><span>Cells</span><span class="cellinfo"></span></div>
        <div class="cellbars"></div>
      </div>
      <div class="alarm-chips"></div>
      <div class="card-foot"><span class="conn"></span><span class="meta"></span></div>`;
    card.querySelector(".edit").addEventListener("click", () => doRename(bat.address));
    return card;
  }

  function updateCard(card, bat) {
    const s = bat.sample;
    const online = bat.connected !== false && !!s;
    card.classList.toggle("offline", !online);
    card.querySelector(".nm").textContent = bat.name || bat.address;

    const stateTag = card.querySelector(".state-tag");
    if (s) {
      stateTag.textContent = s.state;
      stateTag.className = "state-tag state-" + s.state;
    } else { stateTag.textContent = "—"; stateTag.className = "state-tag"; }

    const hasAlarm = s && s.alarms && s.alarms.length;
    card.classList.toggle("alarm", !!hasAlarm);

    if (s) {
      KVCharts.ring(card.querySelector(".ring"), s.soc, "SoC");
      card.querySelector(".soc-num").textContent = fmt(s.soc, 0) + "%";
      card.querySelector(".rem").textContent = `${fmt(s.remaining_capacity, 1)} / ${fmt(bat.capacity_ah, 0)} Ah`;
      card.querySelector(".soh").textContent = `SoH ≈ ${fmt(bat.soh_estimate, 0)}% · ${s.cycles} cyc`;
      const eta = bat.time_to_full_h != null ? `Full in ${fmtDur(bat.time_to_full_h)}`
        : bat.time_to_empty_h != null ? `Empty in ${fmtDur(bat.time_to_empty_h)}` : "";
      card.querySelector(".eta").textContent = eta;

      setV(card, ".voltage", fmt(s.voltage, 2) + " V");
      const cur = card.querySelector(".current");
      cur.textContent = fmt(s.current, 1) + " A";
      cur.className = "v current " + (s.current > 0.1 ? "pos" : s.current < -0.1 ? "neg" : "");
      const pw = card.querySelector(".power");
      pw.textContent = fmt(s.power, 0) + " W";
      pw.className = "v power " + (s.power > 1 ? "pos" : s.power < -1 ? "neg" : "");
      setV(card, ".temp", fmt(s.temperature, 1) + " °C");
      setV(card, ".cycles", s.cycles);
      const dv = card.querySelector(".delta");
      dv.textContent = Math.round(s.cell_delta * 1000) + " mV";
      dv.className = "v delta " + (s.cell_delta >= 0.3 ? "neg" : "");

      renderCells(card, s);
      renderChips(card, s.alarms || []);
    }

    const conn = card.querySelector(".conn");
    conn.textContent = online ? (bat.rssi != null ? `📶 ${bat.rssi} dBm` : "connected")
      : (bat.error ? "⚠ " + bat.error : "offline");
    const meta = [];
    if (bat.model) meta.push(bat.model);
    if (bat.firmware) meta.push("fw " + bat.firmware);
    card.querySelector(".meta").textContent = meta.join(" · ");
  }

  function renderCells(card, s) {
    const wrap = card.querySelector(".cellbars");
    const cells = s.cell_voltages || [];
    const lo = s.min_cell, hi = s.max_cell;
    card.querySelector(".cellinfo").textContent =
      `min ${fmt(lo, 3)}V · max ${fmt(hi, 3)}V · Δ ${Math.round(s.cell_delta * 1000)}mV`;
    // scale bars within the active cell range for visual contrast
    const span = Math.max(0.02, hi - lo);
    wrap.innerHTML = "";
    cells.forEach((v, i) => {
      const pct = 20 + ((v - lo) / span) * 80;
      const div = document.createElement("div");
      div.className = "cellbar" + (v === hi ? " high" : v === lo ? " low" : "");
      div.innerHTML = `<div class="mv">${Math.round(v * 1000)}</div>
        <div class="bar" style="height:${Math.max(6, pct)}%"></div>
        <div class="cn">C${i + 1}</div>`;
      wrap.appendChild(div);
    });
  }

  function renderChips(card, alarms) {
    const wrap = card.querySelector(".alarm-chips");
    wrap.innerHTML = "";
    alarms.forEach((a) => {
      const chip = document.createElement("span");
      const warn = ["HTC", "HTD", "LTC", "LTD"].includes(a);
      chip.className = "chip" + (warn ? " warn" : "");
      chip.textContent = a;
      wrap.appendChild(chip);
    });
  }

  // ---- rename -------------------------------------------------------
  async function doRename(address) {
    const cur = (batteries[address] || {}).name || address;
    const name = prompt("Rename battery", cur);
    if (name == null || !name.trim()) return;
    await fetch("/api/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, name: name.trim() }),
    });
    if (batteries[address]) batteries[address].name = name.trim();
    renderCards(); populateBatterySelect();
  }

  // ---- history ------------------------------------------------------
  function populateBatterySelect() {
    const sel = $("hist-battery");
    const cur = sel.value;
    sel.innerHTML = "";
    Object.values(batteries).forEach((b) => {
      const o = document.createElement("option");
      o.value = b.address; o.textContent = b.name || b.address;
      sel.appendChild(o);
    });
    if (cur) sel.value = cur;
  }

  async function loadHistory() {
    const addr = $("hist-battery").value;
    const minutes = $("hist-range").value;
    if (!addr) return;
    $("hist-export").href = `/api/export.csv?address=${encodeURIComponent(addr)}&minutes=${minutes}`;
    let data;
    try {
      const r = await fetch(`/api/history?address=${encodeURIComponent(addr)}&minutes=${minutes}`);
      data = await r.json();
    } catch (_) { return; }
    const rows = data.rows || [];
    const col = (k) => rows.map((r) => [r.ts, r[k]]);
    KVCharts.lineChart($("chart-voltage"), [{ name: "V", color: "#38d39f", points: col("voltage") }], { unit: "V" });
    KVCharts.lineChart($("chart-current"), [{ name: "A", color: "#4ea1ff", points: col("current") }], { unit: "A", zeroLine: true });
    KVCharts.lineChart($("chart-soc"), [{ name: "%", color: "#ffb23e", points: col("soc") }], { unit: "%", fixedMin: 0, fixedMax: 100 });
    KVCharts.lineChart($("chart-temp"), [{ name: "°C", color: "#ff8a5b", points: col("temperature") }], { unit: "°C" });
    KVCharts.lineChart($("chart-delta"), [{ name: "mV", color: "#b58bff", points: rows.map((r) => [r.ts, (r.cell_delta || 0) * 1000]) }], { unit: "mV", fixedMin: 0 });
  }
  $("hist-refresh").addEventListener("click", loadHistory);
  $("hist-battery").addEventListener("change", loadHistory);
  $("hist-range").addEventListener("change", loadHistory);
  window.addEventListener("resize", () => {
    if ($("tab-history").classList.contains("active")) loadHistory();
  });

  // ---- events -------------------------------------------------------
  async function loadEvents() {
    let data;
    try { data = await (await fetch("/api/events?limit=200")).json(); }
    catch (_) { return; }
    const tb = $("events-body"); tb.innerHTML = "";
    (data.events || []).forEach((e) => {
      const tr = document.createElement("tr");
      const name = (batteries[e.address] || {}).name || e.address;
      // Build cells with textContent so a hostile BLE-advertised battery name
      // (or any field) is rendered as text, never interpreted as HTML (XSS).
      const cells = [
        new Date(e.raised_ts * 1000).toLocaleString(),
        name,
        e.severity,
        e.code,
        e.message || "",
        e.cleared_ts ? new Date(e.cleared_ts * 1000).toLocaleTimeString() : "active",
      ];
      cells.forEach((text, i) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (i === 2) td.className = "sev-" + cssId(String(e.severity));
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  // ---- helpers ------------------------------------------------------
  function setText(id, v) { const el = $(id); if (el) el.textContent = v; }
  function setV(card, sel, v) { card.querySelector(sel).textContent = v; }
  function fmt(v, d) { return (v == null || isNaN(v)) ? "—" : Number(v).toFixed(d); }
  function fmtDur(h) {
    if (h == null) return "—";
    if (h < 1) return Math.round(h * 60) + " min";
    const hh = Math.floor(h), mm = Math.round((h - hh) * 60);
    return hh + "h" + (mm ? " " + mm + "m" : "");
  }
  function cssId(addr) { return (addr || "").replace(/[^a-zA-Z0-9]/g, "_"); }

  // boot
  fetch("/api/snapshot").then((r) => r.json()).then((s) => {
    $("transport-pill").textContent = s.transport || "—";
    $("footer-version").textContent = "";
  }).catch(() => {});
  connect();
})();
