/* LifePlan AI — web client. Vanilla JS: form -> /api/runs -> live SSE agent feed -> plan view. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const INTERESTS = ["food", "cafe", "street food", "museum", "culture", "attractions", "nature", "shopping", "movies",
    "entertainment", "nightlife", "live music", "comedy", "sports", "workshop", "religious"];
  const MODES = ["walk", "transit", "cab", "drive", "bike", "train", "flight"];
  const TRIP = ["domestic_trip", "international_trip", "business_trip"];
  const ICON = { meal: "🍽️", snack: "☕", activity: "🎯", event: "🎟️", travel: "🚕", hotel: "🏨", flight: "✈️", train: "🚆", busy: "⛔", buffer: "⏸️" };
  const MODE_ICON = { walk: "🚶", transit: "🚌", cab: "🚕", drive: "🚗", bike: "🚲", none: "•" };
  const AGENT_ICON = { coordinator: "🤖", profile: "🧠", discovery: "📍", logistics: "🚗", planner: "📅", user: "🙂", environment: "🌍", system: "⚙️" };

  let currentPlan = null, currentRun = null, map = null, mapLayer = null, source = null;

  // ------------------------------------------------------------------ setup
  function chipGroup(el, values, on) {
    el.innerHTML = values.map((v) => `<span class="chip${on.includes(v) ? " on" : ""}" data-v="${esc(v)}">${esc(v)}</span>`).join("");
    el.addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) c.classList.toggle("on"); });
  }
  const selected = (el) => Array.from(el.querySelectorAll(".chip.on")).map((c) => c.dataset.v);
  function setChips(el, values) { el.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", values.includes(c.dataset.v))); }

  chipGroup($("#interests"), INTERESTS, ["food", "culture"]);
  chipGroup($("#modes"), MODES, ["walk", "transit", "cab"]);

  $("#mode").addEventListener("change", () => $("#dest-wrap").classList.toggle("hidden", !TRIP.includes($("#mode").value)));
  $("#geo-btn").addEventListener("click", () => {
    if (!navigator.geolocation) return alert("Geolocation not available");
    navigator.geolocation.getCurrentPosition((p) => {
      $("#location").value = `${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)}`;
      $("#location").dataset.lat = p.coords.latitude; $("#location").dataset.lon = p.coords.longitude;
    }, () => alert("Could not get your location"));
  });
  $("#location").addEventListener("input", () => { delete $("#location").dataset.lat; delete $("#location").dataset.lon; });

  function addBusy(b = {}) {
    const row = document.createElement("div");
    row.className = "busy-row";
    row.innerHTML = `<input type="datetime-local" class="bs" value="${esc(b.start || "")}"><input type="datetime-local" class="be" value="${esc(b.end || "")}">
      <input class="bt" placeholder="Meeting" value="${esc(b.title || "")}"><button type="button" class="ghost">✕</button>`;
    row.querySelector("button").onclick = () => row.remove();
    $("#busy-list").appendChild(row);
  }
  $("#add-busy").onclick = () => addBusy();

  $$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  function showTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".tab-body").forEach((b) => b.classList.toggle("hidden", b.id !== `tab-${name}`));
    if (name === "map") setTimeout(drawMap, 50);
  }
  $("#show-llm").addEventListener("change", () => $$(".ev.llm").forEach((e) => e.classList.toggle("hidden", !$("#show-llm").checked)));

  fetch("/api/health").then((r) => r.json()).then((h) => {
    const pills = h.llm_providers.length ? h.llm_providers.map((p) => `<span class="pill on">LLM: ${esc(p)}</span>`)
      : ['<span class="pill off">no LLM key configured</span>'];
    Object.entries(h.data_sources).forEach(([k, v]) => pills.push(`<span class="pill ${v ? "on" : "off"}">${esc(k)}</span>`));
    $("#health").innerHTML = pills.join("");
  }).catch(() => {});

  fetch("/api/samples").then((r) => r.json()).then((list) => {
    $("#samples").innerHTML = list.map((s, i) => `<span class="chip" data-i="${i}">${esc(s.request._label || s.name)}</span>`).join("");
    $("#samples").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) fillForm(list[+c.dataset.i].request); });
  }).catch(() => {});

  const toLocalInput = (iso) => (iso ? iso.slice(0, 16) : "");
  function fillForm(r) {
    $("#query").value = r.query || "";
    $("#mode").value = r.mode || "auto"; $("#mode").dispatchEvent(new Event("change"));
    $("#location").value = r.location || ""; delete $("#location").dataset.lat;
    $("#destination").value = r.destination || "";
    $("#start").value = toLocalInput(r.start); $("#end").value = toLocalInput(r.end);
    $("#budget").value = r.budget ? r.budget.amount : ""; $("#currency").value = r.budget ? r.budget.currency : "INR";
    $("#party").value = r.party_size || 1; $("#pace").value = r.pace || "balanced";
    setChips($("#interests"), r.interests || []); setChips($("#modes"), r.travel_modes || ["walk", "transit", "cab"]);
    $("#learned").checked = r.use_learned_schedule !== false;
    $("#busy-list").innerHTML = ""; (r.busy_blocks || []).forEach((b) => addBusy({ start: toLocalInput(b.start), end: toLocalInput(b.end), title: b.title }));
    if ((r.busy_blocks || []).length) $(".busy-box").open = true;
    $("#plan-form").dataset.now = r.now || "";
  }

  // ------------------------------------------------------------------ submit
  async function buildRequest() {
    const req = {
      query: $("#query").value.trim(), mode: $("#mode").value, location: $("#location").value.trim(),
      interests: selected($("#interests")), travel_modes: selected($("#modes")),
      party_size: +$("#party").value || 1, pace: $("#pace").value, use_learned_schedule: $("#learned").checked,
    };
    if ($("#location").dataset.lat) req.coordinates = { lat: +$("#location").dataset.lat, lon: +$("#location").dataset.lon };
    if (TRIP.includes(req.mode) && $("#destination").value.trim()) req.destination = $("#destination").value.trim();
    if ($("#start").value) req.start = $("#start").value;
    if ($("#end").value) req.end = $("#end").value;
    if (+$("#budget").value > 0) req.budget = { amount: +$("#budget").value, currency: $("#currency").value };
    if ($("#plan-form").dataset.now) req.now = $("#plan-form").dataset.now;
    req.busy_blocks = $$(".busy-row").map((r) => ({ start: r.querySelector(".bs").value, end: r.querySelector(".be").value, title: r.querySelector(".bt").value || "Busy" }))
      .filter((b) => b.start && b.end);
    const f = $("#ics").files[0];
    if (f) req.calendar_ics = await f.text();
    return req;
  }

  $("#plan-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const req = await buildRequest();
    startRun("/api/runs", req, "Planning");
  });

  async function startRun(url, body, label) {
    $("#submit").disabled = true;
    $("#feed").innerHTML = "";
    $$(".agent").forEach((a) => a.classList.remove("active", "done"));
    showTab("live");
    setStatus(`${label}… the agents are working (typically 30–120 s on free-tier models).`);
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.join("; ") : (data.detail || res.statusText));
      currentRun = data.run_id;
      listen(data.run_id);
    } catch (err) {
      setStatus(`❌ ${esc(err.message)}`); $("#submit").disabled = false;
    }
  }

  function setStatus(html) { $("#status-line").innerHTML = html; }

  // ------------------------------------------------------------------ live feed
  function listen(runId) {
    if (source) source.close();
    source = new EventSource(`/api/runs/${runId}/events`);
    source.onmessage = (m) => renderEvent(JSON.parse(m.data));
    source.addEventListener("done", async () => {
      source.close();
      $$(".agent").forEach((a) => a.classList.remove("active"));
      const st = await (await fetch(`/api/runs/${runId}`)).json();
      $("#submit").disabled = false;
      $("#dl-json").href = `/api/runs/${runId}/trace`; $("#dl-md").href = `/api/runs/${runId}/trace?format=md`;
      $("#dl-json").classList.remove("disabled"); $("#dl-md").classList.remove("disabled");
      $("#trace-stats").innerHTML = stats(st.stats);
      if (st.status === "done" && st.result) {
        setStatus(`✅ Done — ${st.stats.llm_calls} LLM calls, ${st.stats.tool_calls} tool calls, ${st.stats.messages} agent messages. See the <b>Plan</b> tab.`);
        renderPlan(st.result); showTab("plan");
      } else {
        setStatus(`❌ ${esc(st.error || "run failed")}`);
      }
    });
    source.onerror = () => { /* browser auto-reconnects; history is replayed */ };
  }

  const time = (ms) => (ms / 1000).toFixed(1) + "s";
  function renderEvent(e) {
    const d = e.data || {};
    if (e.type === "agent_start") { $(`.agent[data-agent=${e.agent}]`)?.classList.add("active"); return; }
    if (e.type === "agent_end") { const a = $(`.agent[data-agent=${e.agent}]`); a?.classList.remove("active"); a?.classList.add("done"); return; }
    if (e.type === "decision") { $$(".agent").forEach((a) => a.classList.remove("active")); $(".agent[data-agent=coordinator]").classList.add("active"); }
    let html = "", cls = "", agent = e.agent;
    const who = (a) => `<span class="who ${esc(a)}">${AGENT_ICON[a] || ""} ${esc(a)}</span>`;
    switch (e.type) {
      case "message":
        cls = "message";
        html = `${who(e.agent)} → ${who(d.to)}<span class="kind">${esc(d.kind)}</span><div>${esc(e.content)}</div>`; break;
      case "tool_call":
        cls = "tool";
        html = `<details><summary>🔧 ${who(e.agent)} called <b>${esc(e.content)}</b> ${d.ok ? "✓" : "✗"} <span class="muted">${d.duration_ms} ms</span></summary>
          <pre>args: ${esc(JSON.stringify(d.args, null, 1))}\nresult: ${esc(JSON.stringify(d.result, null, 1)).slice(0, 3000)}</pre></details>`; break;
      case "llm_call":
        cls = "llm" + ($("#show-llm").checked ? "" : " hidden");
        html = `💭 ${who(e.agent)} <span class="muted">${esc(d.model)} · ${d.duration_ms} ms${d.fallback_used ? " · FALLBACK" : ""}</span>
          ${d.tool_calls?.length ? " → " + d.tool_calls.map((t) => esc(t.name)).join(", ") : ""} ${e.content ? `<div>${esc(e.content.slice(0, 400))}</div>` : ""}`; break;
      case "decision":
        cls = "decision";
        html = `${who("coordinator")} decides: <b>${esc(d.action)}${d.target ? " → " + esc(d.target) : ""}</b><div class="muted">${esc(e.content)}</div>`; break;
      case "conflict":
        cls = "conflict";
        html = `⚠️ ${who(e.agent)} conflict <span class="kind">${esc(d.type)} · ${esc(d.severity)}</span><div>${esc(e.content)}</div>`; break;
      case "guardrail": case "degraded_mode": case "llm_error": case "agent_error": case "error":
        cls = e.type === "error" ? "error" : "degraded";
        html = `⚙️ ${who(e.agent)} <b>${esc(e.type.replace("_", " "))}</b>: ${esc(e.content)}`; break;
      case "setup": cls = "decision"; html = `⚙️ set-up: ${esc(e.content)}`; break;
      case "final": cls = "final"; html = `🏁 ${esc(e.content)}`; break;
      default: return;
    }
    const div = document.createElement("div");
    div.className = `ev ${cls}`; div.dataset.agent = agent;
    div.innerHTML = `<span class="t">${time(e.t_ms)}</span>${html}`;
    const feed = $("#feed");
    const stick = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 40;
    feed.appendChild(div);
    if (stick) feed.scrollTop = feed.scrollHeight;
  }

  function stats(s) {
    if (!s) return "";
    return [["LLM calls", s.llm_calls], ["tool calls", s.tool_calls], ["agent messages", s.messages],
      ["tokens in/out", `${s.input_tokens}/${s.output_tokens}`], ["LLM fallbacks", s.llm_fallbacks], ["tool errors", s.tool_errors]]
      .map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  }

  // ------------------------------------------------------------------ plan view
  const money = (c, cur) => (c && c.amount != null ? `${c.estimate ? "~" : ""}${Math.round(c.amount).toLocaleString()} ${esc(c.currency || cur || "")}` : "");
  const hm = (iso) => (iso || "").slice(11, 16);

  function renderPlan(p) {
    currentPlan = p;
    $("#plan-empty").classList.add("hidden"); $("#plan").classList.remove("hidden");
    $("#plan-status").textContent = p.status; $("#plan-status").className = `badge ${p.status}`;
    $("#plan-summary").textContent = p.summary;
    const t = p.totals || {};
    const pct = t.budget ? Math.min(100, Math.round((t.cost / t.budget) * 100)) : null;
    $("#totals").innerHTML = `
      <div class="stat"><b>${esc(t.cost ?? 0)} ${esc(t.currency || "")}</b><span>estimated cost${t.budget ? ` of ${esc(t.budget)}` : ""}</span>
        ${pct != null ? `<div class="budget-bar"><i style="width:${pct}%;background:${pct > 100 ? "var(--danger)" : "var(--ok)"}"></i></div>` : ""}</div>
      <div class="stat"><b>${esc(t.stops ?? 0)}</b><span>stops</span></div>
      <div class="stat"><b>${esc(t.travel_minutes ?? 0)} min</b><span>travelling</span></div>
      <div class="stat"><b>${esc(p.stats?.coordinator_steps ?? "–")}</b><span>coordinator steps · ${esc(p.stats?.planner_revisions ?? 0)} revisions</span></div>`;
    $("#weather").innerHTML = (p.weather?.daily || []).map((w) =>
      `<span class="w">${esc(w.date)}: ${esc(w.desc)}, ${esc(w.t_min)}–${esc(w.t_max)}°C${w.precip_prob_max != null ? `, rain ${esc(w.precip_prob_max)}%` : ""}</span>`).join("")
      + (p.weather?.kind && p.weather.kind !== "forecast" ? `<span class="w">(${esc(p.weather.kind)})</span>` : "");

    let n = 0;
    $("#timeline").innerHTML = (p.days || []).map((d) => `<div class="day"><h3>${esc(new Date(d.date + "T00:00").toDateString())}</h3>${
      d.items.map((it) => {
        const num = ["meal", "snack", "activity", "event"].includes(it.kind) ? ++n : null;
        const place = it.place ? `${it.place.address ? esc(it.place.address) : ""}${it.place.url ? ` · <a href="${esc(it.place.url)}" target="_blank" rel="noopener">link</a>` : ""}${it.place.source ? ` · <span class="muted">${esc(it.place.source)}</span>` : ""}` : "";
        const tr = it.travel ? `${esc(it.travel.km)} km · ${esc(it.travel.source)}` : "";
        return `<div class="item ${esc(it.kind)}"><div class="time">${hm(it.start)}–${hm(it.end)}</div><div class="ico">${it.kind === "travel" && it.travel ? (MODE_ICON[it.travel.mode] || ICON.travel) : (ICON[it.kind] || "•")}</div>
          <div><div class="title">${num ? num + ". " : ""}${esc(it.title)}</div><div class="sub">${place || tr}${it.notes ? " · " + esc(it.notes) : ""}</div></div>
          <div class="cost">${money(it.cost, t.currency)}</div></div>`;
      }).join("")}</div>`).join("") || `<div class="empty">The agents could not fit anything into the available time.</div>`;

    $("#conflicts").innerHTML = p.conflicts?.length ? `<div class="box conf"><b>Conflicts the agents handled</b><ul>${p.conflicts.map((c) =>
      `<li><b>${esc(c.type)}</b> (raised by ${esc(c.raised_by.join(", "))}): ${esc(c.description)}<br><span class="muted">→ ${esc(c.resolution)}</span></li>`).join("")}</ul></div>` : "";
    $("#warnings").innerHTML = p.warnings?.length ? `<div class="box warn"><b>Notes</b><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
    $("#alternatives").innerHTML = p.alternatives?.length ? `<h3>Alternatives the agents found</h3><div class="chips">${p.alternatives.map((a) =>
      `<span class="chip">${esc(a.name)} <span class="muted">${esc(a.category || "")}${a.distance_km != null ? " · " + esc(a.distance_km) + " km" : ""}</span></span>`).join("")}</div>` : "";

    const opts = [];
    let idx = 0;
    (p.days || []).forEach((d) => d.items.forEach((it) => { if (it.kind !== "busy") opts.push(`<option value="${idx}">${hm(it.start)} ${esc(it.title)}</option>`); idx++; }));
    $("#replan-item").innerHTML = `<option value="">(affected item — optional)</option>` + opts.join("");
    if (!$("#tab-map").classList.contains("hidden")) drawMap();
  }

  // replan buttons
  $$("[data-replan]").forEach((b) => b.addEventListener("click", () => {
    if (!currentPlan) return;
    const kind = b.dataset.replan;
    const text = $("#replan-text").value.trim();
    const ev = { plan_id: currentPlan.plan_id, event: kind, details: text || b.textContent.trim() };
    const sel = $("#replan-item").value;
    if (sel !== "") ev.item_index = +sel;
    const items = (currentPlan.days || []).flatMap((d) => d.items);
    if (kind === "delay" || kind === "schedule_change") {
      const ref = sel !== "" ? items[+sel] : items.find((i) => i.kind !== "busy" && i.kind !== "travel");
      if (ref) {
        const s = new Date(ref.start), e = new Date(s.getTime() + (kind === "delay" ? 45 : 60) * 60000);
        const f = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}T${String(x.getHours()).padStart(2, "0")}:${String(x.getMinutes()).padStart(2, "0")}`;
        ev.new_busy_block = { start: f(new Date(s.getTime() - 60000 * (kind === "delay" ? 0 : 1))), end: f(e), title: kind === "delay" ? "Running late" : "Meeting extended" };
      }
    }
    if (kind === "closed" && sel === "") return alert("Pick the affected item first");
    startRun("/api/runs/replan", ev, "Re-planning");
  }));

  $("#send-feedback").addEventListener("click", async () => {
    if (!currentPlan) return;
    const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const res = await fetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_id: currentPlan.plan_id, rating: +$("#rating").value, liked: split($("#liked").value), disliked: split($("#disliked").value) }) });
    const d = await res.json();
    $("#feedback-out").textContent = res.ok ? `Saved. Learned preference weights: ${JSON.stringify(d.learned_weights)}` : (d.detail || "failed");
  });

  // ------------------------------------------------------------------ map
  function drawMap() {
    if (!window.L) { $("#map").innerHTML = "<div class='empty'>Map library could not load.</div>"; return; }
    if (!map) {
      map = L.map("map");
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }).addTo(map);
      map.setView([18.52, 73.85], 12);
    }
    map.invalidateSize();
    if (mapLayer) mapLayer.remove();
    mapLayer = L.layerGroup().addTo(map);
    if (!currentPlan) return;
    const pts = [];
    let n = 0;
    currentPlan.days.forEach((d) => d.items.forEach((it) => {
      if (!it.place || it.place.lat == null) return;
      const isStop = ["meal", "snack", "activity", "event"].includes(it.kind);
      const label = isStop ? ++n : "H";
      const icon = L.divIcon({ className: "", html: `<div class="num-icon">${label}</div>`, iconSize: [24, 24] });
      L.marker([it.place.lat, it.place.lon], { icon }).bindPopup(`<b>${esc(it.title)}</b><br>${hm(it.start)}–${hm(it.end)}`).addTo(mapLayer);
      pts.push([it.place.lat, it.place.lon]);
    }));
    if (pts.length > 1) L.polyline(pts, { color: "#5b5bd6", weight: 3, dashArray: "6 6" }).addTo(mapLayer);
    if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
  }
})();
