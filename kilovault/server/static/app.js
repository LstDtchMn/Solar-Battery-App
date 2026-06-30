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
        <div class="card-name"><span class="nm"></span><span class="edit" title="Rename">✎</span></div>
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
    if (t) { e.stopPropagation(); if (tipPinned === t) hideTip(); else { tipPinned = t; showTip(t); } }
    else if (tipPinned) hideTip();
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
    try { pf = await (await fetch("/api/preflight")).json(); } catch (_) {}
    const checks = [];
    if (wizState.method === "ble") {
      const bt = pf.bluetooth || {};
      checks.push(bt.installed
        ? ok(`Bluetooth support installed (bleak ${esc(bt.version || "")})`)
        : bad("Bluetooth support not installed", "Run: pip install bleak — or use the demo / an ESP32."));
      checks.push(info("Wake your batteries", "Apply a load or charger so they advertise over Bluetooth."));
    } else if (wizState.method === "serial") {
      const ports = pf.serial_ports || [];
      checks.push((pf.serial && pf.serial.installed)
        ? ok("Serial support installed (pyserial)")
        : bad("Serial support not installed", "Run: pip install pyserial"));
      if (ports.length) {
        const opts = ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)} — ${esc(p.description || "")}</option>`).join("");
        checks.push(`<div class="check-row"><span class="check-badge check-ok">✓</span>
          <div><div class="ct">Found ${ports.length} serial port(s)</div>
          <div class="cd">Pick your ESP32: <select id="wiz-port">${opts}</select></div></div></div>`);
      } else {
        checks.push(bad("No serial ports found", "Plug in the ESP32 over USB and try again."));
      }
    } else {
      checks.push(ok("Demo mode is ready — no hardware needed."));
    }
    $("wiz-checks").innerHTML = checks.join("");
    const go = $("wiz-go");
    go.disabled = false;
    go.onclick = async () => {
      go.disabled = true; go.textContent = "Starting…";
      const body = { type: wizState.method };
      const portSel = $("wiz-port");
      if (wizState.method === "serial" && portSel) body.serial_port = portSel.value;
      if (wizState.method === "simulator") body.sim_batteries = 2;
      try { await fetch("/api/transport", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); } catch (_) {}
      wizState.step = 3; renderWizard();
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
    try { d = await (await fetch("/api/diagnostics")).json(); } catch (_) { return; }
    const sys = [
      ["App version", d.version], ["System", d.platform], ["Python", d.python],
      ["Data source", d.transport],
      ["Bluetooth (bleak)", d.bleak_version || "not installed"],
      ["Serial (pyserial)", d.pyserial_version || "not installed"],
      ["Batteries online", `${d.online_count} / ${d.battery_count}`],
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
      const txt = await (await fetch("/api/log?kb=64")).text();
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
    try { r = await (await fetch("/api/test-bluetooth?timeout=5", { method: "POST" })).json(); } catch (e) { r = { ok: false, error: String(e) }; }
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

  // boot
  fetch("/api/snapshot").then((r) => r.json()).then((s) => {
    $("transport-pill").textContent = s.transport || "—";
    $("footer-version").textContent = "";
    // First run: greet new users with the setup wizard.
    if (!localStorage.getItem("kv_seen")) { localStorage.setItem("kv_seen", "1"); openWizard(); }
  }).catch(() => {});
  connect();
})();
