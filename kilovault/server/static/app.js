/* KiloVault HLX+ Monitor dashboard logic. Fully offline; talks to the local
   server over fetch() + Server-Sent Events. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const batteries = {};   // address -> latest battery dict
  let bankState = {};

  // When the dashboard is exposed on the LAN, the page URL carries a token that
  // every request must include. On localhost there's no token. U() appends it.
  const TOKEN = new URLSearchParams(location.search).get("token") || "";
  function U(path) {
    if (!TOKEN) return path;
    return path + (path.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
  }

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
  let es = null;
  function connect() {
    if (es) { try { es.close(); } catch (_) {} }
    es = new EventSource(U("/api/stream"));
    es.onopen = () => setConn(true);
    es.onerror = () => {
      setConn(false);
      // EventSource auto-retries while CONNECTING, but once it reaches CLOSED
      // (e.g. a non-2xx from the server/proxy) it never retries — reconnect
      // manually so the dashboard recovers without a page reload.
      if (es && es.readyState === EventSource.CLOSED) {
        try { es.close(); } catch (_) {}
        setTimeout(connect, 3000);
      }
    };
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
      const r = await fetch(U("/api/snapshot"));
      const s = await r.json();
      bankState = s.bank;
      (s.batteries || []).forEach((b) => { batteries[b.address] = b; });
      $("transport-pill").textContent = s.transport;
      // Refresh the cards + selector too — if the SSE stream silently stalls
      // (proxy buffering, collector hiccup) this poll is the only thing keeping
      // the per-battery cards from freezing on stale values.
      renderBank(); renderCards(); populateBatterySelect();
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
    const sinces = Object.values(batteries).map((x) => x.since_ts).filter(Boolean);
    setText("bank-since", sinces.length ? "since " + new Date(Math.min(...sinces) * 1000).toLocaleDateString() : "");
    renderAlarmBanner(b.alarms || []);
  }

  const WARN_ALARMS = ["HTC", "HTD", "LTC", "LTD", "CELL_IMBALANCE", "TEMP_HIGH", "TEMP_LOW", "SOC_LOW"];
  function renderAlarmBanner(alarms) {
    const el = $("alarm-banner");
    if (!alarms.length) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    // Warning (amber) styling only when every active alarm is warning-level;
    // any critical alarm keeps the red banner.
    const allWarn = alarms.every((a) => WARN_ALARMS.includes(a));
    el.classList.toggle("warn", allWarn);
    el.textContent = "⚠ Active alarms: " + alarms.join(", ");
  }

  // ---- battery cards ------------------------------------------------
  function renderCards() {
    const grid = $("battery-grid");
    const list = Object.values(batteries).sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    const hint = $("empty-hint");
    if (hint) hint.classList.toggle("hidden", list.length > 0);
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
        <div class="card-name"><span class="nm"></span><span class="edit" title="Rename">✎</span><span class="cfg" title="Alarm thresholds">⚙</span></div>
        <span class="state-tag"></span>
      </div>
      <div class="soc-row">
        <canvas class="ring" width="96" height="96"></canvas>
        <div class="soc-meta">
          <div><span class="soc-num">—</span></div>
          <small class="rem"></small>
          <small class="soh" title="State of Health — a rough estimate of remaining capacity vs new, based on cycle count."></small>
          <small class="eta" title="Estimated time to fully charge or run empty at the current rate."></small>
        </div>
      </div>
      <div class="kv-grid">
        <div class="kv"><label>Voltage</label><div class="v voltage">—</div></div>
        <div class="kv"><label>Current</label><div class="v current">—</div></div>
        <div class="kv"><label>Power</label><div class="v power">—</div></div>
        <div class="kv"><label>Temp <i class="tip" data-tip="Battery temperature. Avoid charging below about 0°C (32°F) — the BMS may shut the pack down to protect it.">i</i></label><div class="v temp">—</div></div>
        <div class="kv"><label>Cycles <i class="tip" data-tip="How many full charge/discharge cycles this battery has done. LiFePO4 batteries last for thousands.">i</i></label><div class="v cycles">—</div></div>
        <div class="kv"><label>Cell Δ <i class="tip" data-tip="The voltage gap between this battery's highest and lowest cell. Keep under 300 mV. A big gap means it needs a full, slow charge to re-balance.">i</i></label><div class="v delta">—</div></div>
      </div>
      <div class="cells">
        <div class="cells-head"><span>Cells <i class="tip" data-tip="Each bar is one of the 4 cells inside this battery. Green = highest, orange = lowest. They should be close together.">i</i></span><span class="cellinfo"></span></div>
        <div class="cellbars"></div>
      </div>
      <div class="alarm-chips"></div>
      <div class="card-foot"><span class="conn"></span><span class="meta"></span></div>`;
    card.querySelector(".edit").addEventListener("click", () => doRename(bat.address));
    card.querySelector(".cfg").addEventListener("click", () => openThresholds(bat.address));
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
      dv.textContent = Number.isFinite(s.cell_delta)
        ? Math.round(s.cell_delta * 1000) + " mV" : "—";
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
    const dmv = Number.isFinite(s.cell_delta) ? Math.round(s.cell_delta * 1000) : "—";
    card.querySelector(".cellinfo").textContent =
      `min ${fmt(lo, 3)}V · max ${fmt(hi, 3)}V · Δ ${dmv}mV`;
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
    try {
      const r = await fetch(U("/api/rename"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address, name: name.trim() }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      // Only reflect the new name locally once the server accepted it.
      if (batteries[address]) batteries[address].name = name.trim();
      renderCards(); populateBatterySelect();
    } catch (_) {
      alert("Could not rename the battery — check the connection and try again.");
    }
  }

  // ---- per-battery alarm thresholds ---------------------------------
  const THRESHOLD_FIELDS = [
    ["soc_low", "Low state-of-charge warning", "%"],
    ["soc_critical", "Critical state-of-charge", "%"],
    ["temp_high", "High temperature", "°C"],
    ["temp_low", "Low temperature", "°C"],
    ["voltage_high", "High pack voltage", "V"],
    ["voltage_low", "Low pack voltage", "V"],
    ["cell_delta_warn", "Cell imbalance warning", "V"],
    ["cell_delta_critical", "Cell imbalance critical", "V"],
  ];
  async function openThresholds(address) {
    const bat = batteries[address] || {};
    let data = { global: {}, overrides: {} };
    try {
      data = await (await fetch(U(`/api/thresholds?address=${encodeURIComponent(address)}`))).json();
    } catch (_) {}
    const g = data.global || {}, ov = data.overrides || {};
    const rows = THRESHOLD_FIELDS.map(([f, label, unit]) =>
      `<label class="thr-row"><span>${esc(label)} <small>(${esc(unit)})</small></span>
        <input type="number" step="any" data-f="${f}" value="${ov[f] != null ? esc(ov[f]) : ""}"
               placeholder="${g[f] != null ? esc(g[f]) : ""}"></label>`).join("");
    openModal(`
      <button class="close-x">×</button>
      <h2>Alarm thresholds</h2>
      <p class="sub">For <b>${esc(bat.name || address)}</b>. Leave a field blank to use
        the global default (shown greyed as the placeholder).</p>
      <div class="thr-grid">${rows}</div>
      <div class="wiz-actions">
        <button class="btn btn-ghost" id="thr-reset">Clear overrides</button>
        <button class="btn" id="thr-save">Save</button>
      </div>`);
    async function post(overrides) {
      try {
        const r = await fetch(U("/api/thresholds"), { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ address, overrides }) });
        if (!r.ok) throw new Error("HTTP " + r.status);
        closeModal();
      } catch (_) {
        alert("Could not save thresholds — check the connection and try again.");
      }
    }
    $("thr-save").onclick = () => {
      const overrides = {};
      modalBox.querySelectorAll("input[data-f]").forEach((inp) => {
        if (inp.value.trim() !== "") overrides[inp.dataset.f] = parseFloat(inp.value);
      });
      post(overrides);
    };
    $("thr-reset").onclick = () => post({});
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

  // Monotonic request tokens so a slow response for a previously-selected
  // battery/range can't overwrite the chart with stale (wrong-battery) data.
  let histReq = 0, summaryReq = 0;

  async function loadHistory() {
    const addr = $("hist-battery").value;
    const minutes = $("hist-range").value;
    if (!addr) return;
    const my = ++histReq;
    $("hist-export").href = U(`/api/export.csv?address=${encodeURIComponent(addr)}&minutes=${minutes}`);
    let data;
    try {
      const r = await fetch(U(`/api/history?address=${encodeURIComponent(addr)}&minutes=${minutes}`));
      data = await r.json();
    } catch (_) { return; }
    if (my !== histReq) return;  // a newer request superseded this one
    const rows = data.rows || [];
    const col = (k) => rows.map((r) => [r.ts, r[k]]);
    KVCharts.lineChart($("chart-voltage"), [{ name: "V", color: "#38d39f", points: col("voltage") }], { unit: "V" });
    KVCharts.lineChart($("chart-current"), [{ name: "A", color: "#4ea1ff", points: col("current") }], { unit: "A", zeroLine: true });
    KVCharts.lineChart($("chart-soc"), [{ name: "%", color: "#ffb23e", points: col("soc") }], { unit: "%", fixedMin: 0, fixedMax: 100 });
    KVCharts.lineChart($("chart-temp"), [{ name: "°C", color: "#ff8a5b", points: col("temperature") }], { unit: "°C" });
    KVCharts.lineChart($("chart-delta"), [{ name: "mV", color: "#b58bff", points: rows.map((r) => [r.ts, (r.cell_delta || 0) * 1000]) }], { unit: "mV", fixedMin: 0 });
    loadSummary(addr);
  }

  async function loadSummary(addr) {
    const tb = $("summary-body");
    if (!tb) return;
    const my = ++summaryReq;
    tb.innerHTML = "";  // clear first so a failed/switched request never shows stale rows
    let data;
    try {
      data = await (await fetch(U(`/api/summary?address=${encodeURIComponent(addr)}&days=30`))).json();
    } catch (_) { return; }
    if (my !== summaryReq) return;  // superseded by a newer selection
    (data.days || []).forEach((d) => {
      const tr = document.createElement("tr");
      const cells = [
        d.day,
        `${fmt(d.min_soc, 0)}–${fmt(d.max_soc, 0)}%`,
        `${fmt(d.min_v, 2)}–${fmt(d.max_v, 2)} V`,
        `${fmt(d.min_t, 1)}–${fmt(d.max_t, 1)} °C`,
        `${fmt(d.wh_charged, 0)} Wh`,
        `${fmt(d.wh_discharged, 0)} Wh`,
      ];
      cells.forEach((t) => {
        const td = document.createElement("td");
        td.textContent = t;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    if (!(data.days || []).length) {
      tb.innerHTML = '<tr><td colspan="6" class="diag-hint">No history yet.</td></tr>';
    }
  }
  $("hist-refresh").addEventListener("click", loadHistory);
  $("hist-battery").addEventListener("change", loadHistory);
  $("hist-range").addEventListener("change", loadHistory);
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    // Debounce: a drag-resize fires continuously; only redraw once it settles.
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if ($("tab-history").classList.contains("active")) loadHistory();
    }, 250);
  });

  // ---- events -------------------------------------------------------
  async function loadEvents() {
    let data;
    try { data = await (await fetch(U("/api/events?limit=200"))).json(); }
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

  // ============================ TOOLTIPS ============================
  // Works on hover (desktop) and tap (mobile). Single floating bubble.
  const tipEl = $("tooltip");
  let tipPinned = null;
  function showTip(target) {
    const text = target.getAttribute("data-tip");
    if (!text) return;
    tipEl.textContent = text;
    tipEl.classList.remove("hidden");
    const r = target.getBoundingClientRect();
    const tw = Math.min(280, tipEl.offsetWidth);
    let left = r.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    let top = r.bottom + 8;
    if (top + tipEl.offsetHeight > window.innerHeight - 8) top = r.top - tipEl.offsetHeight - 8;
    tipEl.style.left = left + "px";
    tipEl.style.top = Math.max(8, top) + "px";
  }
  function hideTip() { tipEl.classList.add("hidden"); tipPinned = null; }
  document.addEventListener("mouseover", (e) => {
    const t = e.target.closest(".tip"); if (t && !tipPinned) showTip(t);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest(".tip") && !tipPinned) hideTip();
  });
  document.addEventListener("click", (e) => {
    const t = e.target.closest(".tip");
    // Ignore decorative .tip icons that carry no data-tip, otherwise they'd
    // "pin" an empty bubble and freeze hover tooltips on the real ones.
    if (t && t.getAttribute("data-tip")) {
      e.stopPropagation();
      if (tipPinned === t) hideTip(); else { tipPinned = t; showTip(t); }
    } else if (tipPinned) hideTip();
  });
  window.addEventListener("scroll", hideTip, true);

  // ============================ MODAL ============================
  const modalRoot = $("modal-root"), modalBox = $("modal-box");
  function openModal(html) {
    modalBox.innerHTML = html;
    modalRoot.classList.remove("hidden");
  }
  function closeModal() { modalRoot.classList.add("hidden"); modalBox.innerHTML = ""; }
  modalRoot.addEventListener("click", (e) => {
    if (e.target.hasAttribute("data-close") || e.target.classList.contains("close-x")) closeModal();
  });

  // ============================ HELP / GLOSSARY ============================
  const GLOSSARY = [
    ["State of Charge (SoC)", "How full the battery is, 0–100%, like a fuel gauge. It's an estimate from the battery's BMS."],
    ["State of Health (SoH)", "A rough estimate of how much capacity the battery still has versus when it was new, based on cycle count."],
    ["Voltage", "Electrical 'pressure'. A 12V LiFePO4 rests near 13.3–13.4V full and ~12.0–13.0V in use. Below ~12V it's nearly empty."],
    ["Current (Amps)", "How much electricity is flowing. Positive = charging the battery, negative = the battery is powering your loads."],
    ["Power (Watts)", "Voltage × Current. How fast energy is moving right now."],
    ["Capacity (Ah)", "Amp-hours the battery holds when full. Remaining Ah is how much is left."],
    ["Cell & Cell Δ (delta)", "Each 12V battery has 4 internal cells. Cell Δ is the gap between the highest and lowest. Keep it under 300 mV; a big gap means a battery needs a full, slow charge to re-balance."],
    ["Cycles", "One full charge + discharge ≈ one cycle. LiFePO4 lasts for thousands."],
    ["Alarms", "Protections the battery's BMS reports: HV/LV (high/low voltage), OCC/OCD (over-current charge/discharge), HTC/HTD/LTC/LTD (high/low temperature), SCD (short circuit)."],
    ["RSSI (signal)", "Bluetooth signal strength in dBm. Closer to 0 is stronger; below about −90 is weak and may drop out."],
  ];
  function openHelp() {
    const items = GLOSSARY.map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("");
    openModal(`
      <button class="close-x" aria-label="Close">×</button>
      <h2>Help &amp; glossary</h2>
      <p class="sub">Hover or tap any <span class="tip">i</span> on the dashboard for a quick explanation.
        Here's what the main terms mean.</p>
      <dl class="glossary">${items}</dl>
      <div class="wiz-actions">
        <button class="btn" id="help-wizard">Open the setup wizard</button>
        <button class="btn btn-ghost" data-close>Close</button>
      </div>`);
    const hw = $("help-wizard"); if (hw) hw.addEventListener("click", () => { closeModal(); openWizard(); });
  }

  // ============================ SETUP WIZARD ============================
  let wizState = { step: 0, method: null, port: "" };
  function openWizard() { wizState = { step: 0, method: null, port: "" }; renderWizard(); }

  function wizDots(n, total) {
    return `<div class="wiz-steps">${Array.from({length: total}, (_, i) =>
      `<div class="wiz-dot${i <= n ? " active" : ""}"></div>`).join("")}</div>`;
  }

  function renderWizard() {
    const total = 4;
    if (wizState.step === 0) {
      openModal(`
        <button class="close-x">×</button>${wizDots(0, total)}
        <h2>Welcome 👋</h2>
        <p class="sub">Let's get your KiloVault batteries on screen. This takes a minute.</p>
        <p>You'll need:</p>
        <ul>
          <li>Your batteries <b>awake</b> — apply a load or charger (they sleep when idle).</li>
          <li>Bluetooth on this PC, <i>or</i> an ESP32 USB adapter, <i>or</i> just try the demo.</li>
        </ul>
        <div class="wiz-actions">
          <span></span>
          <button class="btn" id="wiz-next">Get started →</button>
        </div>`);
      $("wiz-next").onclick = () => { wizState.step = 1; renderWizard(); };
    } else if (wizState.step === 1) {
      openModal(`
        <button class="close-x">×</button>${wizDots(1, total)}
        <h2>How do you want to connect?</h2>
        <p class="sub">You can change this any time from ⚙ Setup.</p>
        <button class="choice" data-m="ble"><span class="ic">📶</span><span><div class="ct">This PC's Bluetooth</div><div class="cd">Easiest if the PC is near the batteries.</div></span></button>
        <button class="choice" data-m="serial"><span class="ic">🔌</span><span><div class="ct">ESP32 USB adapter</div><div class="cd">For PCs without Bluetooth, or to reach a distant bank.</div></span></button>
        <button class="choice" data-m="simulator"><span class="ic">🧪</span><span><div class="ct">Just show me a demo</div><div class="cd">See the app working with pretend batteries — no hardware.</div></span></button>
        <div class="wiz-actions"><button class="btn btn-ghost" id="wiz-back">← Back</button><span></span></div>`);
      $("wiz-back").onclick = () => { wizState.step = 0; renderWizard(); };
      modalBox.querySelectorAll(".choice").forEach((c) =>
        c.onclick = () => { wizState.method = c.dataset.m; wizState.step = 2; renderWizard(); });
    } else if (wizState.step === 2) {
      renderWizardCheck(total);
    } else if (wizState.step === 3) {
      openModal(`
        <button class="close-x">×</button>${wizDots(3, total)}
        <h2>You're all set 🎉</h2>
        <p class="sub">Your data source is running.</p>
        <p>Batteries will appear on the <b>Live</b> tab as they connect. If one is
           missing, wake it (apply a load or charger) and give it a few seconds.</p>
        <p>Tip: hover the <span class="tip">i</span> icons to learn what each number means,
           and check the <b>Diagnostics</b> tab if anything looks wrong.</p>
        <div class="wiz-actions"><span></span><button class="btn" data-close>Done</button></div>`);
    }
  }

  async function renderWizardCheck(total) {
    openModal(`
      <button class="close-x">×</button>${wizDots(2, total)}
      <h2>Checking…</h2>
      <p class="sub">Making sure everything's ready for <b>${esc(methodName(wizState.method))}</b>.</p>
      <div id="wiz-checks"><span class="spinner"></span> running checks…</div>
      <div class="wiz-actions"><button class="btn btn-ghost" id="wiz-back">← Back</button>
        <button class="btn" id="wiz-go" disabled>Continue →</button></div>`);
    $("wiz-back").onclick = () => { wizState.step = 1; renderWizard(); };

    let pf = {};
    try { pf = await (await fetch(U("/api/preflight"))).json(); } catch (_) {}
    const checks = [];
    let hardFail = false;
    if (wizState.method === "ble") {
      const bt = pf.bluetooth || {};
      if (bt.installed) {
        checks.push(ok(`Bluetooth support installed (bleak ${bt.version || ""})`));
      } else {
        hardFail = true;
        checks.push(bad("Bluetooth support not installed", "Run: pip install bleak — or use the demo / an ESP32."));
      }
      checks.push(info("Wake your batteries", "Apply a load or charger so they advertise over Bluetooth."));
    } else if (wizState.method === "serial") {
      const ports = pf.serial_ports || [];
      if (pf.serial && pf.serial.installed) {
        checks.push(ok("Serial support installed (pyserial)"));
      } else {
        hardFail = true;
        checks.push(bad("Serial support not installed", "Run: pip install pyserial"));
      }
      if (ports.length) {
        const opts = ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)} — ${esc(p.description || "")}</option>`).join("");
        checks.push(`<div class="check-row"><span class="check-badge check-ok">✓</span>
          <div><div class="ct">Found ${ports.length} serial port(s)</div>
          <div class="cd">Pick your ESP32: <select id="wiz-port">${opts}</select></div></div></div>`);
      } else {
        hardFail = true;
        checks.push(bad("No serial ports found", "Plug in the ESP32 over USB and try again."));
      }
    } else {
      checks.push(ok("Demo mode is ready — no hardware needed."));
    }
    $("wiz-checks").innerHTML = checks.join("");
    const go = $("wiz-go");
    go.disabled = hardFail;  // can't continue if a required dependency is missing
    go.onclick = async () => {
      go.disabled = true; const label = go.textContent; go.textContent = "Starting…";
      const body = { type: wizState.method };
      const portSel = $("wiz-port");
      if (wizState.method === "serial" && portSel) body.serial_port = portSel.value;
      if (wizState.method === "simulator") body.sim_batteries = 2;
      let okResp = false;
      try {
        const r = await fetch(U("/api/transport"), { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        okResp = r.ok && j.ok !== false;
        if (!okResp && !$("wiz-err")) {
          const div = document.createElement("div");
          div.id = "wiz-err";
          div.innerHTML = bad("Could not start that source", (j && j.error) || "Please try again or pick another option.");
          $("wiz-checks").appendChild(div);
        }
      } catch (e) { okResp = false; }
      if (okResp) { wizState.step = 3; renderWizard(); }
      else { go.disabled = false; go.textContent = label; }
    };
  }
  function methodName(m) { return m === "ble" ? "this PC's Bluetooth" : m === "serial" ? "an ESP32 adapter" : "the demo"; }
  function ok(t) { return `<div class="check-row"><span class="check-badge check-ok">✓</span><div class="ct">${esc(t)}</div></div>`; }
  function bad(t, d) { return `<div class="check-row"><span class="check-badge check-bad">✗</span><div><div class="ct">${esc(t)}</div><div class="cd">${esc(d || "")}</div></div></div>`; }
  function info(t, d) { return `<div class="check-row"><span class="check-badge check-wait">i</span><div><div class="ct">${esc(t)}</div><div class="cd">${esc(d || "")}</div></div></div>`; }

  // ============================ DIAGNOSTICS ============================
  const FAQ = [
    ["No batteries found", "KiloVault batteries sleep when idle. Apply a load or a charger to wake them. Keep this PC (or the ESP32) within Bluetooth range, and make sure Bluetooth is on. On Linux, scanning may need elevated privileges."],
    ["It connects, then drops out", "Usually a weak signal — move the PC/ESP32 closer, or use an ESP32 next to the bank. The app reconnects automatically; watch the signal bars below."],
    ["Numbers look slightly off vs a meter", "The battery's reading can differ from the terminals by up to ~0.3V mid-cycle; they converge when full or empty. Always trust a real meter for critical decisions."],
    ["A battery shut off / bank dropped", "Often one battery hit a voltage limit before the others, or it's too cold to charge (near 0°C/32°F). Charge each battery fully to ~14.1V and keep cells balanced (Cell Δ under 300 mV)."],
    ["How do I get help?", "Click 'Download diagnostics (.zip)' above and email it. It contains the log, your settings and system info — no passwords."],
  ];
  function openDiag() { refreshDiag(); refreshLog(); }
  async function refreshDiag() {
    let d = {};
    try { d = await (await fetch(U("/api/diagnostics"))).json(); } catch (_) { return; }
    const sys = [
      ["App version", d.version], ["System", d.platform], ["Python", d.python],
      ["Data source", d.transport],
      ["Bluetooth (bleak)", d.bleak_version || "not installed"],
      ["Serial (pyserial)", d.pyserial_version || "not installed"],
      ["Hardware alerting", d.hardware_alerting || "off"],
      ["Batteries online", `${d.online_count ?? 0} / ${d.battery_count ?? 0}`],
      ["Data folder", d.db_path],
    ];
    $("diag-system").innerHTML = sys.map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${esc(String(v == null ? "—" : v))}</td></tr>`).join("");

    const bats = d.batteries || [];
    $("diag-batteries").innerHTML = bats.length ? bats.map((b) => {
      const seen = b.last_seen ? timeAgo(b.last_seen) : "never";
      const crcRate = b.frames_received ? ((b.crc_errors / b.frames_received) * 100).toFixed(1) : "0";
      return `<div class="diag-batt">
        <div class="top"><span class="nm"><span class="dot ${b.connected ? "dot-on" : "dot-off"}"></span>${esc(b.name)}</span>
          ${signalBars(b.rssi)}</div>
        <div class="meta">${b.connected ? "connected" : "offline"} · last data ${esc(seen)} ·
          ${b.frames_received} frames · ${crcRate}% CRC errors${b.rssi != null ? " · " + b.rssi + " dBm" : ""}
          ${b.firmware ? " · fw " + esc(b.firmware) : ""}</div>
        ${b.error ? `<div class="err">⚠ ${esc(b.error)}</div>` : ""}</div>`;
    }).join("") : `<p class="diag-hint">No batteries yet. Use ⚙ Setup or wake your batteries.</p>`;

    $("diag-faq").innerHTML = FAQ.map(([q, a]) =>
      `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("");
  }
  async function refreshLog() {
    try {
      const txt = await (await fetch(U("/api/log?kb=64"))).text();
      const el = $("log-view"); el.textContent = txt; el.scrollTop = el.scrollHeight;
    } catch (_) {}
  }
  function signalBars(rssi) {
    let bars = 0;
    if (rssi != null) bars = rssi >= -60 ? 4 : rssi >= -72 ? 3 : rssi >= -85 ? 2 : rssi >= -95 ? 1 : 0;
    let h = '<span class="signal" title="Bluetooth signal strength">';
    for (let i = 1; i <= 4; i++) h += `<i class="${i <= bars ? "on" : ""}" style="height:${i * 3 + 2}px"></i>`;
    return h + "</span>";
  }
  async function runBtTest() {
    const box = $("bt-test-result");
    box.className = "bt-result"; box.classList.remove("hidden");
    box.innerHTML = `<span class="spinner"></span> Scanning for ~5 seconds…`;
    let r = {};
    try { r = await (await fetch(U("/api/test-bluetooth?timeout=5"), { method: "POST" })).json(); } catch (e) { r = { ok: false, error: String(e) }; }
    if (!r.ok) {
      box.className = "bt-result bad";
      box.innerHTML = `<b>✗ Bluetooth test failed.</b><br>${esc(r.error || "Unknown error")}` +
        `<br><small>Install Bluetooth support (pip install bleak), check the adapter, or use an ESP32 / the demo.</small>`;
      return;
    }
    box.className = "bt-result ok";
    const list = (r.devices || []).filter((d) => d.is_hlx).map((d) =>
      `<div class="row"><span>${esc(d.name)}</span><span>${d.rssi != null ? d.rssi + " dBm" : ""}</span></div>`).join("");
    box.innerHTML = `<b>✓ Bluetooth is working.</b> Found <b>${r.count}</b> KiloVault batter${r.count === 1 ? "y" : "ies"}` +
      ` (saw ${r.total_seen} Bluetooth device(s) total).` +
      (list ? `<div class="wiz-found">${list}</div>` : `<br><small>No KiloVault batteries in range — wake them with a load/charger and retry.</small>`);
  }
  function timeAgo(ts) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    return Math.round(s / 3600) + "h ago";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ============================ WIRING ============================
  $("btn-setup").addEventListener("click", openWizard);
  $("btn-help").addEventListener("click", openHelp);
  const rc = $("reset-counters");
  if (rc) rc.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!confirm("Reset the charged/discharged energy counters to zero for all batteries?")) return;
    try {
      await fetch(U("/api/reset-counters"), { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      // Refresh immediately so the totals don't linger until the next poll.
      const s = await (await fetch(U("/api/snapshot"))).json();
      bankState = s.bank;
      (s.batteries || []).forEach((b) => { batteries[b.address] = b; });
      renderBank(); renderCards();
    } catch (_) {}
  });
  const es2 = $("empty-setup"); if (es2) es2.addEventListener("click", openWizard);
  $("btn-bt-test").addEventListener("click", runBtTest);
  document.querySelectorAll(".tab").forEach((btn) => {
    if (btn.dataset.tab === "diag") btn.addEventListener("click", openDiag);
  });
  let diagTimer = null;
  document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
    clearInterval(diagTimer);
    if (btn.dataset.tab === "diag") diagTimer = setInterval(() => { refreshDiag(); refreshLog(); }, 4000);
  }));

  // Token-aware download links (Diagnostics tab).
  const dlLog = $("dl-log"); if (dlLog) dlLog.href = U("/api/log?kb=256");
  const dlDiag = $("dl-diag"); if (dlDiag) dlDiag.href = U("/api/diagnostics.zip");

  // boot
  fetch(U("/api/snapshot")).then((r) => r.json()).then((s) => {
    $("transport-pill").textContent = s.transport || "—";
    $("footer-version").textContent = "";
    // First run: greet new users with the setup wizard.
    if (!localStorage.getItem("kv_seen")) { localStorage.setItem("kv_seen", "1"); openWizard(); }
  }).catch(() => {});
  connect();
})();
