/* LifePlan AI — Autonomous Multi-Agent Personal Planner Web Client */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));

  const INTERESTS = [
    "food", "cafe", "street food", "museum", "culture", "attractions", "nature",
    "shopping", "movies", "entertainment", "nightlife", "live music", "comedy",
    "sports", "workshop", "religious"
  ];
  const MODES = ["walk", "transit", "cab", "drive", "bike", "train", "flight"];
  const TRIP = ["domestic_trip", "international_trip", "business_trip"];
  const ICON = {
    meal: "🍽️", snack: "☕", activity: "🎯", event: "🎟️", travel: "🚕",
    hotel: "🏨", flight: "✈️", train: "🚆", busy: "⛔", buffer: "⏸️"
  };
  const MODE_ICON = { walk: "🚶", transit: "🚌", cab: "🚕", drive: "🚗", bike: "🚲", none: "•" };
  const AGENT_ICON = {
    coordinator: "🤖", profile: "🧠", discovery: "📍", logistics: "🚗",
    planner: "📅", user: "🙂", environment: "🌍", system: "⚙️"
  };

  let currentPlan = null, currentRun = null, map = null, mapLayer = null, source = null;
  let activeFilter = "all";

  // ------------------------------------------------------------------ Toast
  function showToast(message, duration = 3000) {
    const container = $("#toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(10px)";
      toast.style.transition = "all 0.25s ease";
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  // ------------------------------------------------------------------ Theme Switcher
  const savedTheme = localStorage.getItem("lifeplan-theme") || "auto";
  document.documentElement.dataset.theme = savedTheme;
  updateThemeIcon(savedTheme);

  function updateThemeIcon(t) {
    const icon = $("#theme-icon");
    if (!icon) return;
    if (t === "dark") icon.textContent = "☀️";
    else if (t === "light") icon.textContent = "🌙";
    else {
      const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      icon.textContent = isDark ? "☀️" : "🌙";
    }
  }

  $("#btn-theme")?.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "auto";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lifeplan-theme", next);
    updateThemeIcon(next);
    showToast(`Switched to ${next} mode`);
  });

  // ------------------------------------------------------------------ Modal (How it works)
  const modal = $("#about-modal");
  $("#btn-about")?.addEventListener("click", () => modal?.classList.remove("hidden"));
  $("#btn-close-about")?.addEventListener("click", () => modal?.classList.add("hidden"));
  $("#btn-close-modal-bottom")?.addEventListener("click", () => modal?.classList.add("hidden"));
  modal?.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });

  // ------------------------------------------------------------------ Pipeline Stage Tracker
  function setPipelineStep(stepNum) {
    $$(".pipeline-step").forEach((el) => {
      const s = parseInt(el.dataset.step, 10);
      if (s < stepNum) {
        el.className = "pipeline-step done";
      } else if (s === stepNum) {
        el.className = "pipeline-step active";
      } else {
        el.className = "pipeline-step";
      }
    });
  }

  // ------------------------------------------------------------------ Form Chips
  function chipGroup(el, values, on) {
    if (!el) return;
    el.innerHTML = values.map((v) => `<span class="chip${on.includes(v) ? " on" : ""}" data-v="${esc(v)}">${esc(v)}</span>`).join("");
    el.addEventListener("click", (e) => {
      const c = e.target.closest(".chip");
      if (c) c.classList.toggle("on");
    });
  }
  const selected = (el) => Array.from(el.querySelectorAll(".chip.on")).map((c) => c.dataset.v);
  function setChips(el, values) {
    if (!el) return;
    el.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", values.includes(c.dataset.v)));
  }

  chipGroup($("#interests"), INTERESTS, ["food", "culture"]);
  chipGroup($("#modes"), MODES, ["walk", "transit", "cab"]);

  $("#mode")?.addEventListener("change", () => {
    const val = $("#mode").value;
    $("#dest-wrap")?.classList.toggle("hidden", !TRIP.includes(val));
  });

  $("#geo-btn")?.addEventListener("click", () => {
    if (!navigator.geolocation) return alert("Geolocation not available");
    navigator.geolocation.getCurrentPosition(
      (p) => {
        $("#location").value = `${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)}`;
        $("#location").dataset.lat = p.coords.latitude;
        $("#location").dataset.lon = p.coords.longitude;
        showToast("Using your current coordinates!");
      },
      () => alert("Could not fetch your location")
    );
  });
  $("#location")?.addEventListener("input", () => {
    delete $("#location").dataset.lat;
    delete $("#location").dataset.lon;
  });

  function addBusy(b = {}) {
    const row = document.createElement("div");
    row.className = "busy-row";
    row.innerHTML = `<input type="datetime-local" class="bs" value="${esc(b.start || "")}">
      <input type="datetime-local" class="be" value="${esc(b.end || "")}">
      <button type="button" class="btn-del" title="Remove block">✕</button>
      <input class="bt" placeholder="e.g. Project meeting / Doctor appointment" value="${esc(b.title || "")}">`;
    row.querySelector(".btn-del").onclick = () => row.remove();
    $("#busy-list")?.appendChild(row);
  }
  $("#add-busy")?.addEventListener("click", () => addBusy());

  // Tabs
  $$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
  function showTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".tab-body").forEach((b) => b.classList.toggle("hidden", b.id !== `tab-${name}`));
    if (name === "map") setTimeout(drawMap, 50);
  }

  $("#show-llm")?.addEventListener("change", () => {
    $$(".ev.llm").forEach((e) => e.classList.toggle("hidden", !$("#show-llm").checked));
  });

  // Feed Filter Pills
  $$(".feed-filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".feed-filter").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.dataset.filter;
      applyFeedFilter();
    });
  });

  function applyFeedFilter() {
    $$("#feed .ev").forEach((el) => {
      if (activeFilter === "all") {
        el.style.display = "";
      } else if (activeFilter === "message") {
        el.style.display = el.classList.contains("message") ? "" : "none";
      } else if (activeFilter === "decision") {
        el.style.display = el.classList.contains("decision") ? "" : "none";
      } else if (activeFilter === "conflict") {
        el.style.display = el.classList.contains("conflict") ? "" : "none";
      } else if (activeFilter === "tool") {
        el.style.display = el.classList.contains("tool") ? "" : "none";
      }
    });
  }

  // Health fetch
  fetch("/api/health")
    .then((r) => r.json())
    .then((h) => {
      const pills = (h.llm_providers && h.llm_providers.length)
        ? h.llm_providers.map((p) => `<span class="pill on">● LLM: ${esc(p)}</span>`)
        : ['<span class="pill off">○ no LLM configured</span>'];
      Object.entries(h.data_sources || {}).forEach(([k, v]) => {
        pills.push(`<span class="pill ${v ? "on" : "off"}">${v ? "●" : "○"} ${esc(k)}</span>`);
      });
      const healthEl = $("#health");
      if (healthEl) healthEl.innerHTML = pills.join("");
    })
    .catch(() => {});

  // ------------------------------------------------------------------ Presets
  const PRESET_METAS = [
    { icon: "🎭", badge: "Conflict Demo", desc: "Hinjewadi meeting → Live comedy + dinner under ₹1500" },
    { icon: "⚡", badge: "Right Now", desc: "What can I do right now in Pune? Fast 2-hr plan" },
    { icon: "🏖️", badge: "Weekend Trip", desc: "Goa 3-day getaway: beaches, seafood & heritage" },
    { icon: "🏛️", badge: "Day Tour", desc: "Full-day Mumbai exploration: art, food & sights" },
    { icon: "💼", badge: "Work Trip", desc: "Bengaluru 2-day business schedule with travel buffer" },
    { icon: "🗼", badge: "Vacation", desc: "Tokyo 5-day vacation with hotels, transit & dining" },
  ];

  fetch("/api/samples")
    .then((r) => r.json())
    .then((list) => {
      renderPresetCards(list);
    })
    .catch(() => {});

  function renderPresetCards(list) {
    const grid = $("#preset-cards");
    if (!grid) return;
    grid.innerHTML = list.map((s, i) => {
      const meta = PRESET_METAS[i % PRESET_METAS.length];
      const title = s.request._label || s.name || `Scenario ${i + 1}`;
      return `
        <div class="preset-card" data-i="${i}">
          <div class="preset-card-top">
            <span class="preset-icon">${meta.icon}</span>
            <span class="preset-badge">${meta.badge}</span>
          </div>
          <div class="preset-name">${esc(title)}</div>
          <div class="preset-desc">${esc(meta.desc)}</div>
        </div>
      `;
    }).join("");

    grid.addEventListener("click", (e) => {
      const card = e.target.closest(".preset-card");
      if (!card) return;
      $$(".preset-card").forEach((c) => c.classList.remove("active"));
      card.classList.add("active");
      const idx = +card.dataset.i;
      if (list[idx]) {
        fillForm(list[idx].request);
        showToast(`Loaded scenario: ${list[idx].request._label || list[idx].name}`);
      }
    });
  }

  const toLocalInput = (iso) => (iso ? iso.slice(0, 16) : "");
  function fillForm(r) {
    if ($("#query")) $("#query").value = r.query || "";
    if ($("#mode")) {
      $("#mode").value = r.mode || "auto";
      $("#mode").dispatchEvent(new Event("change"));
    }
    if ($("#location")) {
      $("#location").value = r.location || "";
      delete $("#location").dataset.lat;
    }
    if ($("#destination")) $("#destination").value = r.destination || "";
    if ($("#start")) $("#start").value = toLocalInput(r.start);
    if ($("#end")) $("#end").value = toLocalInput(r.end);
    if ($("#budget")) $("#budget").value = r.budget ? r.budget.amount : "";
    if ($("#currency")) $("#currency").value = r.budget ? r.budget.currency : "INR";
    if ($("#party")) $("#party").value = r.party_size || 1;
    if ($("#pace")) $("#pace").value = r.pace || "balanced";

    setChips($("#interests"), r.interests || []);
    setChips($("#modes"), r.travel_modes || ["walk", "transit", "cab"]);
    if ($("#learned")) $("#learned").checked = r.use_learned_schedule !== false;

    if ($("#busy-list")) $("#busy-list").innerHTML = "";
    (r.busy_blocks || []).forEach((b) =>
      addBusy({ start: toLocalInput(b.start), end: toLocalInput(b.end), title: b.title })
    );
    if ((r.busy_blocks || []).length && $(".busy-box")) $(".busy-box").open = true;
    if ($("#plan-form")) $("#plan-form").dataset.now = r.now || "";
  }

  // ------------------------------------------------------------------ Submit
  async function buildRequest() {
    const req = {
      query: $("#query").value.trim(),
      mode: $("#mode").value,
      location: $("#location").value.trim(),
      interests: selected($("#interests")),
      travel_modes: selected($("#modes")),
      party_size: +$("#party").value || 1,
      pace: $("#pace").value,
      use_learned_schedule: $("#learned")?.checked ?? true,
    };
    if ($("#location").dataset.lat) {
      req.coordinates = { lat: +$("#location").dataset.lat, lon: +$("#location").dataset.lon };
    }
    if (TRIP.includes(req.mode) && $("#destination")?.value.trim()) {
      req.destination = $("#destination").value.trim();
    }
    if ($("#start")?.value) req.start = $("#start").value;
    if ($("#end")?.value) req.end = $("#end").value;
    if (+$("#budget")?.value > 0) {
      req.budget = { amount: +$("#budget").value, currency: $("#currency").value };
    }
    if ($("#plan-form")?.dataset.now) req.now = $("#plan-form").dataset.now;

    req.busy_blocks = $$(".busy-row").map((r) => ({
      start: r.querySelector(".bs").value,
      end: r.querySelector(".be").value,
      title: r.querySelector(".bt").value || "Busy",
    })).filter((b) => b.start && b.end);

    const f = $("#ics")?.files?.[0];
    if (f) req.calendar_ics = await f.text();
    return req;
  }

  $("#plan-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const req = await buildRequest();
    startRun("/api/runs", req, "Planning");
  });

  async function startRun(url, body, label) {
    const submitBtn = $("#submit");
    if (submitBtn) submitBtn.disabled = true;
    $("#feed").innerHTML = "";
    $$(".agent").forEach((a) => a.classList.remove("active", "done"));
    setPipelineStep(1);
    if ($("#board-status-text")) $("#board-status-text").textContent = "Agents Collaborating";
    showTab("live");
    setStatus(`<b>${label} in progress</b> — 5 agents are evaluating constraints, routing and searching venues.`);

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(Array.isArray(data.detail) ? data.detail.join("; ") : (data.detail || res.statusText));
      }
      currentRun = data.run_id;
      listen(data.run_id);
    } catch (err) {
      setStatus(`❌ <b>Planning Failed:</b> ${esc(err.message)}`);
      if (submitBtn) submitBtn.disabled = false;
      if ($("#board-status-text")) $("#board-status-text").textContent = "Error";
    }
  }

  function setStatus(html) {
    const line = $("#status-line");
    if (line) line.innerHTML = html;
  }

  // ------------------------------------------------------------------ SSE Live Feed
  function listen(runId) {
    if (source) source.close();
    source = new EventSource(`/api/runs/${runId}/events`);

    source.onmessage = (m) => renderEvent(JSON.parse(m.data));

    source.addEventListener("done", async () => {
      source.close();
      $$(".agent").forEach((a) => {
        a.classList.remove("active");
        a.classList.add("done");
      });
      setPipelineStep(5);
      if ($("#board-status-text")) $("#board-status-text").textContent = "Finalized";

      const st = await (await fetch(`/api/runs/${runId}`)).json();
      if ($("#submit")) $("#submit").disabled = false;

      const dlJson = $("#dl-json");
      const dlMd = $("#dl-md");
      if (dlJson) {
        dlJson.href = `/api/runs/${runId}/trace`;
        dlJson.classList.remove("disabled");
      }
      if (dlMd) {
        dlMd.href = `/api/runs/${runId}/trace?format=md`;
        dlMd.classList.remove("disabled");
      }

      if ($("#trace-stats")) $("#trace-stats").innerHTML = stats(st.stats);

      if (st.status === "done" && st.result) {
        setStatus(`✅ <b>Plan Complete!</b> ${st.stats.llm_calls} LLM calls · ${st.stats.tool_calls} tool calls · ${st.stats.messages} agent messages. Switched to <b>Final Itinerary</b> tab.`);
        renderPlan(st.result);
        showTab("plan");
        showToast("Plan ready! Check the Final Itinerary tab.");
      } else {
        setStatus(`❌ <b>Plan Error:</b> ${esc(st.error || "Run did not complete")}`);
      }
    });

    source.onerror = () => { /* browser auto-reconnects */ };
  }

  const time = (ms) => (ms / 1000).toFixed(1) + "s";

  function renderEvent(e) {
    const d = e.data || {};

    // Visual Board highlighting
    if (e.type === "agent_start") {
      $(`.agent[data-agent=${e.agent}]`)?.classList.add("active");
      if (e.agent === "discovery" || e.agent === "logistics") setPipelineStep(2);
      if (e.agent === "planner") setPipelineStep(3);
      return;
    }
    if (e.type === "agent_end") {
      const a = $(`.agent[data-agent=${e.agent}]`);
      a?.classList.remove("active");
      a?.classList.add("done");
      return;
    }
    if (e.type === "decision") {
      $$(".agent").forEach((a) => a.classList.remove("active"));
      $(".agent[data-agent=coordinator]")?.classList.add("active");
    }
    if (e.type === "conflict" || (e.type === "message" && d.kind === "critique")) {
      setPipelineStep(4);
    }

    let html = "", cls = "", agent = e.agent;
    const who = (a) => `<span class="who ${esc(a)}">${AGENT_ICON[a] || ""} ${esc(a)}</span>`;

    switch (e.type) {
      case "message":
        cls = "message";
        html = `${who(e.agent)} → ${who(d.to)}<span class="kind">${esc(d.kind)}</span><div style="margin-top:4px;">${esc(e.content)}</div>`;
        break;
      case "tool_call":
        cls = "tool";
        html = `<details><summary>🔧 ${who(e.agent)} called <b>${esc(e.content)}</b> ${d.ok ? "✓" : "✗"} <span class="muted">${d.duration_ms} ms</span></summary>
          <pre>args: ${esc(JSON.stringify(d.args, null, 1))}\nresult: ${esc(JSON.stringify(d.result, null, 1)).slice(0, 3000)}</pre></details>`;
        break;
      case "llm_call":
        cls = "llm" + ($("#show-llm")?.checked ? "" : " hidden");
        html = `💭 ${who(e.agent)} <span class="muted">${esc(d.model)} · ${d.duration_ms} ms${d.fallback_used ? " · FALLBACK" : ""}</span>
          ${d.tool_calls?.length ? " → " + d.tool_calls.map((t) => esc(t.name)).join(", ") : ""} ${e.content ? `<div>${esc(e.content.slice(0, 400))}</div>` : ""}`;
        break;
      case "decision":
        cls = "decision";
        html = `${who("coordinator")} decides: <b>${esc(d.action)}${d.target ? " → " + esc(d.target) : ""}</b><div class="muted" style="margin-top:2px;">${esc(e.content)}</div>`;
        break;
      case "conflict":
        cls = "conflict";
        html = `⚠️ ${who(e.agent)} detected conflict <span class="kind">${esc(d.type)} · ${esc(d.severity)}</span><div style="margin-top:3px;font-weight:600;">${esc(e.content)}</div>`;
        break;
      case "guardrail": case "degraded_mode": case "llm_error": case "agent_error": case "error":
        cls = e.type === "error" ? "error" : "degraded";
        html = `⚙️ ${who(e.agent)} <b>${esc(e.type.replace("_", " "))}</b>: ${esc(e.content)}`;
        break;
      case "setup":
        cls = "decision";
        html = `⚙️ Blackboard setup: ${esc(e.content)}`;
        break;
      case "final":
        cls = "final";
        html = `🏁 Coordinator finalized: ${esc(e.content)}`;
        break;
      default:
        return;
    }

    const div = document.createElement("div");
    div.className = `ev ${cls}`;
    div.dataset.agent = agent;
    div.innerHTML = `<span class="t">${time(e.t_ms)}</span>${html}`;

    // Apply active filter
    if (activeFilter === "message" && !div.classList.contains("message")) div.style.display = "none";
    if (activeFilter === "decision" && !div.classList.contains("decision")) div.style.display = "none";
    if (activeFilter === "conflict" && !div.classList.contains("conflict")) div.style.display = "none";
    if (activeFilter === "tool" && !div.classList.contains("tool")) div.style.display = "none";

    const feed = $("#feed");
    if (!feed) return;
    const stick = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 60;
    feed.appendChild(div);
    if (stick) feed.scrollTop = feed.scrollHeight;
  }

  function stats(s) {
    if (!s) return "";
    return [
      ["LLM Calls", s.llm_calls],
      ["Tool Calls", s.tool_calls],
      ["Agent Messages", s.messages],
      ["Tokens (In/Out)", `${s.input_tokens}/${s.output_tokens}`],
      ["LLM Fallbacks", s.llm_fallbacks],
      ["Tool Errors", s.tool_errors]
    ].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  }

  // ------------------------------------------------------------------ Plan View
  const money = (c, cur) => (c && c.amount != null ? `${c.estimate ? "~" : ""}${Math.round(c.amount).toLocaleString()} ${esc(c.currency || cur || "")}` : "");
  const hm = (iso) => (iso || "").slice(11, 16);

  function renderPlan(p) {
    currentPlan = p;
    $("#plan-empty")?.classList.add("hidden");
    $("#plan")?.classList.remove("hidden");

    const statusBadge = $("#plan-status");
    if (statusBadge) {
      statusBadge.textContent = p.status === "ok" ? "Optimized Plan" : p.status;
      statusBadge.className = `badge ${p.status}`;
    }
    if ($("#plan-summary")) $("#plan-summary").textContent = p.summary;

    const t = p.totals || {};
    const pct = t.budget ? Math.min(100, Math.round((t.cost / t.budget) * 100)) : null;

    if ($("#totals")) {
      $("#totals").innerHTML = `
        <div class="stat">
          <b>${esc(t.cost ?? 0)} ${esc(t.currency || "")}</b>
          <span>Estimated cost${t.budget ? ` of ${esc(t.budget)} budget` : ""}</span>
          ${pct != null ? `<div class="budget-bar"><i style="width:${pct}%;background:${pct > 100 ? "var(--danger)" : "var(--ok)"}"></i></div>` : ""}
        </div>
        <div class="stat"><b>${esc(t.stops ?? 0)}</b><span>Scheduled Stops</span></div>
        <div class="stat"><b>${esc(t.travel_minutes ?? 0)} min</b><span>Travel Time</span></div>
        <div class="stat"><b>${esc(p.stats?.coordinator_steps ?? "–")}</b><span>Coordinator Steps (${esc(p.stats?.planner_revisions ?? 0)} revisions)</span></div>`;
    }

    if ($("#weather")) {
      $("#weather").innerHTML = (p.weather?.daily || []).map((w) =>
        `<span class="w">📅 <b>${esc(w.date)}:</b> ${esc(w.desc)}, ${esc(w.t_min)}–${esc(w.t_max)}°C${w.precip_prob_max != null ? `, rain ${esc(w.precip_prob_max)}%` : ""}</span>`
      ).join("") + (p.weather?.kind && p.weather.kind !== "forecast" ? `<span class="w">(${esc(p.weather.kind)})</span>` : "");
    }

    let n = 0;
    if ($("#timeline")) {
      $("#timeline").innerHTML = (p.days || []).map((d) => `<div class="day"><h3>${esc(new Date(d.date + "T00:00").toDateString())}</h3>${
        d.items.map((it) => {
          const num = ["meal", "snack", "activity", "event"].includes(it.kind) ? ++n : null;
          let placeInfo = "";
          if (it.place) {
            const navUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((it.place.name || it.title) + " " + (it.place.address || ""))}`;
            placeInfo = `${it.place.address ? esc(it.place.address) : ""}${it.place.url ? ` · <a href="${esc(it.place.url)}" target="_blank" rel="noopener">Website</a>` : ""} · <a class="item-nav-link" href="${navUrl}" target="_blank" rel="noopener">📍 Directions</a>${it.place.source ? ` · <span class="muted">via ${esc(it.place.source)}</span>` : ""}`;
          }
          const tr = it.travel ? `${esc(it.travel.km)} km · ${esc(it.travel.source || "road network")}` : "";
          return `
            <div class="item ${esc(it.kind)}">
              <div class="time">${hm(it.start)}–${hm(it.end)}</div>
              <div class="ico">${it.kind === "travel" && it.travel ? (MODE_ICON[it.travel.mode] || ICON.travel) : (ICON[it.kind] || "•")}</div>
              <div>
                <div class="title">${num ? num + ". " : ""}${esc(it.title)}</div>
                <div class="sub">${placeInfo || tr}${it.notes ? " · " + esc(it.notes) : ""}</div>
              </div>
              <div class="cost">${money(it.cost, t.currency)}</div>
            </div>`;
        }).join("")}</div>`).join("") || `<div class="empty">The agents could not schedule any items in the available window.</div>`;
    }

    if ($("#conflicts")) {
      $("#conflicts").innerHTML = p.conflicts?.length ? `<div class="box conf"><b>⚡ Autonomous Conflict Resolutions (Agent Critique)</b><ul>${p.conflicts.map((c) =>
        `<li><b>${esc(c.type)}</b> (raised by ${esc(c.raised_by.join(", "))}): ${esc(c.description)}<br><span class="muted">→ Resolution: ${esc(c.resolution)}</span></li>`).join("")}</ul></div>` : "";
    }

    if ($("#warnings")) {
      $("#warnings").innerHTML = p.warnings?.length ? `<div class="box warn"><b>Notes & Feasibility Details</b><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
    }

    if ($("#alternatives")) {
      $("#alternatives").innerHTML = p.alternatives?.length ? `<h3>Alternative Places Explored</h3><div class="chips">${p.alternatives.map((a) =>
        `<span class="chip">${esc(a.name)} <span class="muted">${esc(a.category || "")}${a.distance_km != null ? " · " + esc(a.distance_km) + " km" : ""}</span></span>`).join("")}</div>` : "";
    }

    const opts = [];
    let idx = 0;
    (p.days || []).forEach((d) => d.items.forEach((it) => {
      if (it.kind !== "busy") opts.push(`<option value="${idx}">${hm(it.start)} ${esc(it.title)}</option>`);
      idx++;
    }));
    if ($("#replan-item")) {
      $("#replan-item").innerHTML = `<option value="">(Select affected stop — optional)</option>` + opts.join("");
    }

    if (!$("#tab-map")?.classList.contains("hidden")) drawMap();
  }

  // ------------------------------------------------------------------ Export Actions
  // 1. Calendar .ics generator
  $("#btn-export-ics")?.addEventListener("click", () => {
    if (!currentPlan) return alert("No active plan to export");
    const icsContent = generateICS(currentPlan);
    const blob = new Blob([icsContent], { type: "text/calendar;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `LifePlan-${new Date().toISOString().slice(0, 10)}.ics`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("Downloaded .ics! Open it to import into Google/Apple Calendar.");
  });

  function formatICSDate(isoStr) {
    if (!isoStr) return "";
    const clean = isoStr.replace(/[-:]/g, "");
    return clean.slice(0, 15) + (clean.length > 15 ? "Z" : "");
  }

  function generateICS(p) {
    const lines = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//LifePlan AI//Autonomous Multi-Agent Planner//EN",
      "CALSCALE:GREGORIAN",
      "METHOD:PUBLISH"
    ];

    (p.days || []).forEach((d) => {
      (d.items || []).forEach((it, idx) => {
        if (it.kind === "busy") return;
        lines.push("BEGIN:VEVENT");
        lines.push(`UID:lifeplan-${Date.now()}-${idx}@lifeplan.ai`);
        lines.push(`DTSTAMP:${formatICSDate(new Date().toISOString())}`);
        lines.push(`DTSTART:${formatICSDate(it.start)}`);
        lines.push(`DTEND:${formatICSDate(it.end)}`);
        lines.push(`SUMMARY:${(it.title || "Scheduled Activity").replace(/,/g, "\\,")}`);
        if (it.notes || it.kind) {
          lines.push(`DESCRIPTION:${(it.notes || it.kind).replace(/,/g, "\\,").replace(/\n/g, "\\n")}`);
        }
        if (it.place && it.place.address) {
          lines.push(`LOCATION:${it.place.address.replace(/,/g, "\\,")}`);
        }
        lines.push("STATUS:CONFIRMED");
        lines.push("END:VEVENT");
      });
    });

    lines.push("END:VCALENDAR");
    return lines.join("\r\n");
  }

  // 2. Copy Plan
  $("#btn-copy-plan")?.addEventListener("click", () => {
    if (!currentPlan) return alert("No active plan to copy");
    let text = `🗓️ LifePlan AI Itinerary\n${currentPlan.summary}\n\n`;
    (currentPlan.days || []).forEach((d) => {
      text += `📅 ${d.date}\n`;
      (d.items || []).forEach((it) => {
        const timeStr = `${hm(it.start)} - ${hm(it.end)}`;
        const costStr = it.cost?.amount ? ` (${it.cost.amount} ${it.cost.currency || "INR"})` : "";
        text += `  • ${timeStr} | ${it.title}${costStr}\n`;
        if (it.place?.address) text += `    📍 ${it.place.address}\n`;
      });
      text += "\n";
    });
    navigator.clipboard.writeText(text).then(() => {
      showToast("Itinerary copied to clipboard!");
    }).catch(() => {
      showToast("Could not copy automatically. Check permissions.");
    });
  });

  // 3. Print / PDF
  $("#btn-print-plan")?.addEventListener("click", () => {
    window.print();
  });

  // ------------------------------------------------------------------ Replan
  $$("[data-replan]").forEach((b) => b.addEventListener("click", () => {
    if (!currentPlan) return;
    const kind = b.dataset.replan;
    const text = $("#replan-text").value.trim();
    const ev = { plan_id: currentPlan.plan_id, event: kind, details: text || b.textContent.trim() };
    const sel = $("#replan-item")?.value;
    if (sel && sel !== "") ev.item_index = +sel;

    const items = (currentPlan.days || []).flatMap((d) => d.items);
    if (kind === "delay" || kind === "schedule_change") {
      const ref = (sel && sel !== "") ? items[+sel] : items.find((i) => i.kind !== "busy" && i.kind !== "travel");
      if (ref) {
        const s = new Date(ref.start), e = new Date(s.getTime() + (kind === "delay" ? 45 : 60) * 60000);
        const f = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}T${String(x.getHours()).padStart(2, "0")}:${String(x.getMinutes()).padStart(2, "0")}`;
        ev.new_busy_block = {
          start: f(new Date(s.getTime() - 60000 * (kind === "delay" ? 0 : 1))),
          end: f(e),
          title: kind === "delay" ? "Running late" : "Meeting extended"
        };
      }
    }
    if (kind === "closed" && (!sel || sel === "")) return alert("Pick the affected venue item from the dropdown first");
    startRun("/api/runs/replan", ev, "Re-planning");
  }));

  // Feedback
  $("#send-feedback")?.addEventListener("click", async () => {
    if (!currentPlan) return;
    const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan_id: currentPlan.plan_id,
        rating: +$("#rating").value,
        liked: split($("#liked").value),
        disliked: split($("#disliked").value)
      })
    });
    const d = await res.json();
    const out = $("#feedback-out");
    if (out) {
      out.textContent = res.ok
        ? `✓ Saved! Learned preference weights updated: ${JSON.stringify(d.learned_weights)}`
        : (d.detail || "Failed to save feedback");
    }
    showToast("Feedback saved! Profile agent updated memory.");
  });

  // ------------------------------------------------------------------ Map
  function drawMap() {
    if (!window.L) {
      $("#map").innerHTML = "<div class='empty'>Map library could not load.</div>";
      return;
    }
    if (!map) {
      map = L.map("map");
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "© OpenStreetMap contributors"
      }).addTo(map);
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
      const icon = L.divIcon({ className: "", html: `<div class="num-icon">${label}</div>`, iconSize: [26, 26] });
      L.marker([it.place.lat, it.place.lon], { icon })
        .bindPopup(`<b>${esc(it.title)}</b><br>${hm(it.start)}–${hm(it.end)}<br><small>${esc(it.place.address || "")}</small>`)
        .addTo(mapLayer);
      pts.push([it.place.lat, it.place.lon]);
    }));

    if (pts.length > 1) {
      L.polyline(pts, { color: "#4f46e5", weight: 3.5, dashArray: "6 6" }).addTo(mapLayer);
    }
    if (pts.length) {
      map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
    }
  }
})();
