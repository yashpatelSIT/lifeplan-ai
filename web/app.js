/* LifePlan AI — Smart Personal Planner & Itinerary Builder Web Client */
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
  const MODE_ICON = { walk: "🚶", transit: "🚌", cab: "🚕", drive: "🚗", bike: "🚲", train: "🚆", flight: "✈️", none: "•" };
  const AGENT_ICON = {
    coordinator: "🤖", profile: "🧠", discovery: "📍", logistics: "🚗",
    planner: "📅", user: "🙂", environment: "🌍", system: "⚙️"
  };

  let currentPlan = null;
  let currentRun = null;
  let map = null;
  let mapLayer = null;
  let source = null;
  let currentStep = 1;
  let maxVisitedStep = 1;
  let commitments = [];

  // ------------------------------------------------------------------ Toast
  function showToast(message, duration = 3200) {
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
  const savedTheme = localStorage.getItem("lifeplan-theme") || "light";
  document.documentElement.dataset.theme = savedTheme;
  updateThemeIcon(savedTheme);

  function updateThemeIcon(t) {
    const icon = $("#theme-icon");
    if (!icon) return;
    icon.textContent = t === "dark" ? "☀️" : "🌙";
  }

  $("#btn-theme")?.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lifeplan-theme", next);
    updateThemeIcon(next);
    showToast(`Switched to ${next} theme`);
  });

  // ------------------------------------------------------------------ Dev Mock Mode
  const mockToggle = $("#toggle-mock");
  if (mockToggle) {
    const savedMock = localStorage.getItem("lifeplan-mock-mode");
    if (savedMock !== null) {
      mockToggle.checked = savedMock === "true";
    }
    mockToggle.addEventListener("change", () => {
      localStorage.setItem("lifeplan-mock-mode", mockToggle.checked ? "true" : "false");
      showToast(mockToggle.checked
        ? "🧪 Mock Mode ON: Plans will be generated offline with zero external API calls"
        : "🌐 Live API Mode ON: Planning will invoke live AI models & data providers");
    });
  }

  // ------------------------------------------------------------------ Modal (How it works)
  const modal = $("#about-modal");
  $("#btn-about")?.addEventListener("click", () => modal?.classList.remove("hidden"));
  $("#btn-close-about")?.addEventListener("click", () => modal?.classList.add("hidden"));
  $("#btn-close-modal-bottom")?.addEventListener("click", () => modal?.classList.add("hidden"));
  modal?.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal && !modal.classList.contains("hidden")) {
      modal.classList.add("hidden");
    }
  });

  // ------------------------------------------------------------------ Health fetch
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

  chipGroup($("#interests"), INTERESTS, ["food", "cafe", "culture"]);
  chipGroup($("#modes"), MODES, ["walk", "transit", "cab"]);

  // Trip Mode handler
  $("#mode")?.addEventListener("change", () => {
    const val = $("#mode").value;
    const destInput = $("#destination");
    if (TRIP.includes(val) && destInput && !destInput.value) {
      destInput.focus();
    }
  });

  // GPS Current Location
  $("#geo-btn")?.addEventListener("click", () => {
    if (!navigator.geolocation) {
      showToast("Geolocation is not supported by your browser");
      return;
    }
    showToast("Locating your coordinates...");
    navigator.geolocation.getCurrentPosition(
      (p) => {
        const locInput = $("#location");
        if (locInput) {
          locInput.value = `${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)}`;
          locInput.dataset.lat = p.coords.latitude;
          locInput.dataset.lon = p.coords.longitude;
        }
        showToast("📍 Using your current coordinates!");
      },
      () => showToast("Could not retrieve your location. Please type your city/area.")
    );
  });

  $("#location")?.addEventListener("input", () => {
    delete $("#location").dataset.lat;
    delete $("#location").dataset.lon;
  });

  // ------------------------------------------------------------------ Step 1 Suggestions
  $$(".sugg-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = btn.dataset.query;
      const queryEl = $("#query");
      if (queryEl) {
        queryEl.value = q;
        queryEl.focus();
        showToast("Idea loaded! Review or customize your request.");
      }
    });
  });

  // ------------------------------------------------------------------ Step 3: My Existing Commitments Management
  let editingCommitmentIdx = -1;

  function renderCommitments() {
    const list = $("#commitments-list");
    const emptyMsg = $("#no-commitments-msg");
    const badge = $("#commitments-count-badge");
    if (!list) return;

    list.innerHTML = "";
    if (badge) badge.textContent = `${commitments.length} commitment${commitments.length === 1 ? "" : "s"}`;

    if (commitments.length === 0) {
      if (emptyMsg) emptyMsg.classList.remove("hidden");
      return;
    }

    if (emptyMsg) emptyMsg.classList.add("hidden");
    commitments.forEach((b, idx) => {
      const item = document.createElement("div");
      item.className = "commitment-item";
      const locStr = b.location ? ` · 📍 ${esc(b.location)}` : "";
      const notesStr = b.notes ? ` · 📝 ${esc(b.notes)}` : "";
      item.innerHTML = `
        <div class="commitment-item-info">
          <span class="commitment-title">${esc(b.title || "Scheduled commitment")}</span>
          <span class="commitment-time">📅 ${esc(b.date)}: ${esc(b.start_time)} – ${esc(b.end_time)}${locStr}${notesStr}</span>
        </div>
        <div class="commitment-item-actions">
          <button type="button" class="btn-edit-commitment" data-idx="${idx}">Edit</button>
          <button type="button" class="btn-remove-commitment" title="Remove commitment" data-idx="${idx}">✕</button>
        </div>
      `;
      item.querySelector(".btn-edit-commitment").onclick = () => {
        loadCommitmentIntoForm(idx);
      };
      item.querySelector(".btn-remove-commitment").onclick = () => {
        commitments.splice(idx, 1);
        if (editingCommitmentIdx === idx) resetCommitmentForm();
        renderCommitments();
        showToast("Commitment removed");
      };
      list.appendChild(item);
    });
  }

  function loadCommitmentIntoForm(idx) {
    const c = commitments[idx];
    if (!c) return;
    editingCommitmentIdx = idx;
    if ($("#commitment-title")) $("#commitment-title").value = c.title || "";
    if ($("#commitment-date")) $("#commitment-date").value = c.date || "";
    if ($("#commitment-start-time")) $("#commitment-start-time").value = c.start_time || "";
    if ($("#commitment-end-time")) $("#commitment-end-time").value = c.end_time || "";
    if ($("#commitment-location")) $("#commitment-location").value = c.location || "";
    if ($("#commitment-notes")) $("#commitment-notes").value = c.notes || "";

    const heading = $("#form-commitment-heading");
    if (heading) heading.textContent = "Edit commitment";
    const btnSave = $("#btn-save-commitment");
    if (btnSave) btnSave.textContent = "✓ Update Commitment";
    $("#btn-cancel-edit-commitment")?.classList.remove("hidden");
  }

  function resetCommitmentForm() {
    editingCommitmentIdx = -1;
    if ($("#commitment-title")) $("#commitment-title").value = "";
    if ($("#commitment-date")) $("#commitment-date").value = "";
    if ($("#commitment-start-time")) $("#commitment-start-time").value = "";
    if ($("#commitment-end-time")) $("#commitment-end-time").value = "";
    if ($("#commitment-location")) $("#commitment-location").value = "";
    if ($("#commitment-notes")) $("#commitment-notes").value = "";

    const heading = $("#form-commitment-heading");
    if (heading) heading.textContent = "Add a commitment";
    const btnSave = $("#btn-save-commitment");
    if (btnSave) btnSave.textContent = "+ Add Commitment";
    $("#btn-cancel-edit-commitment")?.classList.add("hidden");
  }

  $("#btn-cancel-edit-commitment")?.addEventListener("click", resetCommitmentForm);

  $("#btn-save-commitment")?.addEventListener("click", () => {
    const title = $("#commitment-title")?.value.trim() || "Existing commitment";
    const dateVal = $("#commitment-date")?.value;
    const startTime = $("#commitment-start-time")?.value;
    const endTime = $("#commitment-end-time")?.value;
    const location = $("#commitment-location")?.value.trim() || "";
    const notes = $("#commitment-notes")?.value.trim() || "";

    if (!title) {
      showToast("Please enter an event or commitment name.");
      $("#commitment-title")?.focus();
      return;
    }
    if (!dateVal) {
      showToast("Please choose the date of your commitment.");
      $("#commitment-date")?.focus();
      return;
    }
    if (!startTime || !endTime) {
      showToast("Please provide both start and end times.");
      return;
    }
    if (startTime >= endTime) {
      showToast("End time must be after start time.");
      return;
    }

    const start = `${dateVal}T${startTime}`;
    const end = `${dateVal}T${endTime}`;

    const item = {
      title,
      date: dateVal,
      start_time: startTime,
      end_time: endTime,
      start,
      end,
      location,
      notes,
    };

    if (editingCommitmentIdx >= 0 && editingCommitmentIdx < commitments.length) {
      commitments[editingCommitmentIdx] = item;
      showToast(`Updated: ${title}`);
    } else {
      commitments.push(item);
      showToast(`Added: ${title}`);
    }

    resetCommitmentForm();
    renderCommitments();
  });

  $("#btn-disconnect-cal")?.addEventListener("click", async () => {
    try {
      await fetch("/api/calendar/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feed_url: "" })
      });
      showToast("Calendar feed disconnected.");
      if ($("#input-feed-url")) $("#input-feed-url").value = "";
      $("#modal-cal-connect")?.classList.add("hidden");
    } catch (err) {
      showToast(`Error: ${err.message}`);
    }
  });

  // ------------------------------------------------------------------ Wizard Stepper Navigation
  function goToStep(step) {
    if (step < 1 || step > 4) return;
    currentStep = step;
    if (step > maxVisitedStep) maxVisitedStep = step;

    // Panes
    $$(".wizard-step-pane").forEach((pane) => {
      pane.classList.toggle("active", pane.id === `step-${step}`);
      pane.classList.toggle("hidden", pane.id !== `step-${step}`);
    });

    // Indicators
    $$(".step-indicator").forEach((ind) => {
      const s = parseInt(ind.dataset.step, 10);
      ind.classList.remove("active", "completed");
      if (s === step) {
        ind.classList.add("active");
      } else if (s < step) {
        ind.classList.add("completed");
      }
    });

    if (step === 3) {
      renderCommitments();
    } else if (step === 4) {
      updateReviewCard();
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // Allow clicking on stepper indicators
  $$(".step-indicator").forEach((ind) => {
    ind.addEventListener("click", () => {
      const targetStep = parseInt(ind.dataset.step, 10);
      // Allow navigation if target is already visited or current step is validated
      if (targetStep < currentStep || targetStep <= maxVisitedStep) {
        goToStep(targetStep);
      }
    });
  });

  // Jump buttons on Step 4 review
  $$("[data-jump-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const step = parseInt(btn.dataset.jumpStep, 10);
      goToStep(step);
    });
  });

  // Step 1 navigation
  $("#btn-step1-next")?.addEventListener("click", () => {
    const q = $("#query")?.value.trim();
    if (!q || q.length < 3) {
      $("#query")?.focus();
      showToast("Please write a short description of what you'd like to do.");
      return;
    }

    // Auto-detect destination if mentioned in query and not already set
    const qLower = q.toLowerCase();
    const destInput = $("#destination");
    const locInput = $("#location");

    // Check "from X to Y"
    const fromToMatch = q.match(/\bfrom\s+([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)(?:\s+(?:from|on|next|for|with|in|by|at|\.|\,)|$)/i);
    if (fromToMatch) {
      if (locInput && (!locInput.value || locInput.value === "Shivajinagar, Pune")) {
        locInput.value = fromToMatch[1].trim();
      }
      if (destInput && !destInput.value) {
        destInput.value = fromToMatch[2].trim();
      }
    } else {
      // Check for popular travel destinations in query
      const knownDest = ["mumbai", "goa", "delhi", "jaipur", "lonavala", "khandala", "mahabaleshwar", "agra", "udaipur", "bengaluru", "bangalore", "chennai", "kolkata", "kochi", "alibaug", "tokyo", "paris", "london", "dubai"];
      if (destInput && !destInput.value) {
        for (const kd of knownDest) {
          const re = new RegExp(`\\b${kd}\\b`, "i");
          if (re.test(qLower)) {
            // Check if it was declared as "to <city>" or "in <city>"
            const toMatch = new RegExp(`\\b(?:to|trip to|vacation in|visit|in)\\s+${kd}\\b`, "i");
            if (toMatch.test(qLower) || qLower.includes(`to ${kd}`)) {
              destInput.value = kd.charAt(0).toUpperCase() + kd.slice(1);
              break;
            }
          }
        }
      }
    }

    if (destInput && destInput.value && $("#mode") && $("#mode").value === "auto") {
      $("#mode").value = qLower.includes("weekend") ? "weekend" : "domestic_trip";
    }

    // Auto-detect party size if mentioned in query
    const partyInput = $("#party");
    if (partyInput) {
      const partyMatch = q.match(/\b(?:party of|group of|family of|for)\s+(\d+)\s*(?:people|persons|members|adults|friends)?\b/i)
        || q.match(/\b(\d+)\s*(?:people|persons|members|adults|friends)\b/i);
      if (partyMatch && partyMatch[1]) {
        partyInput.value = parseInt(partyMatch[1], 10);
      } else if (/\bcouple\b/i.test(q)) {
        partyInput.value = 2;
      } else if (/\bsolo\b/i.test(q)) {
        partyInput.value = 1;
      }
    }

    goToStep(2);
  });

  // Step 2 navigation & reactive party size updates
  $("#party")?.addEventListener("input", () => {
    updateReviewCard();
  });
  $("#party")?.addEventListener("change", () => {
    updateReviewCard();
  });
  $("#btn-step2-prev")?.addEventListener("click", () => goToStep(1));
  $("#btn-step2-next")?.addEventListener("click", () => {
    const loc = $("#location")?.value.trim();
    if (!loc) {
      $("#location")?.focus();
      showToast("Please provide your starting location or city.");
      return;
    }
    goToStep(3);
  });

  // Step 3 navigation
  $("#btn-step3-prev")?.addEventListener("click", () => goToStep(2));
  $("#btn-step3-skip")?.addEventListener("click", () => goToStep(4));
  $("#btn-step3-next")?.addEventListener("click", () => goToStep(4));

  // Step 4 navigation
  $("#btn-step4-prev")?.addEventListener("click", () => goToStep(3));

  // ------------------------------------------------------------------ Step 4: Review Summary & Requirements Extractor
  function renderExtractedRequirementsCard(ext) {
    const card = $("#extracted-requirements-card");
    const grid = $("#extracted-reqs-grid");
    if (!card || !grid || !ext) return;

    card.classList.remove("hidden");
    const items = [];

    if (ext.origin) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Origin (Starting Point)</span>
        <span class="req-item-val">${esc(ext.origin)}</span>
      </div>`);
    }
    if (ext.destination) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Destination</span>
        <span class="req-item-val">${esc(ext.destination)}</span>
      </div>`);
    }
    if (ext.start && ext.end) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Dates & Window</span>
        <span class="req-item-val">${esc(ext.start.slice(0, 10))} → ${esc(ext.end.slice(0, 10))}</span>
      </div>`);
    }
    if (ext.return_deadline?.time_str) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Return Deadline</span>
        <span class="req-item-val">⏰ Arrive by ${esc(ext.return_deadline.time_str)}</span>
      </div>`);
    }
    if (ext.budget != null) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Extracted Budget</span>
        <span class="req-item-val">💰 ${Number(ext.budget).toLocaleString()} ${esc(ext.currency || "INR")}</span>
      </div>`);
    }
    if (ext.hotel?.min_stars) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Hotel Rating</span>
        <span class="req-item-val">⭐ ${ext.hotel.min_stars}-star minimum</span>
      </div>`);
    }
    const activeParty = +($("#party")?.value) || ext.party_size || 1;
    items.push(`<div class="extracted-req-item">
      <span class="req-item-label">Party Size</span>
      <span class="req-item-val">👥 ${activeParty} ${activeParty == 1 ? "person" : "people"}</span>
    </div>`);
    if (ext.travel_modes && ext.travel_modes.length) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Transport</span>
        <span class="req-item-val">🚗 ${esc(ext.travel_modes.join(", "))}</span>
      </div>`);
    }
    if (ext.interests && ext.interests.length) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Activities</span>
        <span class="req-item-val">🎯 ${esc(ext.interests.join(", "))}</span>
      </div>`);
    }
    if (ext.exclusions && ext.exclusions.length) {
      items.push(`<div class="extracted-req-item" style="border-color: var(--danger-soft);">
        <span class="req-item-label" style="color: var(--danger);">Exclusions</span>
        <span class="req-item-val" style="color: var(--danger);">🚫 ${esc(ext.exclusions.join(", "))}</span>
      </div>`);
    }
    if (ext.pace) {
      items.push(`<div class="extracted-req-item">
        <span class="req-item-label">Pace</span>
        <span class="req-item-val">⚡ ${esc(ext.pace)}</span>
      </div>`);
    }

    grid.innerHTML = items.length ? items.join("") : `<div class="text-muted text-xs">Standard defaults applied.</div>`;
  }

  async function updateReviewCard() {
    const query = $("#query")?.value.trim() || "No request specified";
    const startVal = $("#start")?.value;
    const endVal = $("#end")?.value;
    const loc = $("#location")?.value.trim() || "Not specified";
    const mode = $("#mode")?.value || "auto";
    const dest = $("#destination")?.value.trim();
    const budgetVal = $("#budget")?.value;
    const currency = $("#currency")?.value || "INR";
    const party = $("#party")?.value || 1;
    const interests = selected($("#interests"));
    const modes = selected($("#modes"));
    const pace = $("#pace")?.value || "balanced";

    // Text query
    if ($("#rev-query")) $("#rev-query").textContent = `"${query}"`;

    // Fetch structured extracted requirements from API (Priority 3)
    try {
      const res = await fetch("/api/extract-requirements", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, start: startVal || null, end: endVal || null }),
      });
      if (res.ok) {
        const ext = await res.json();
        renderExtractedRequirementsCard(ext);
      }
    } catch (e) {
      console.warn("Could not extract requirements:", e);
    }

    // Datetime & Precedence Check
    const datePattern = /\b(from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+to\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)|tomorrow|today|tonight|this\s+weekend)\b/i;
    const hasNlTiming = datePattern.test(query);

    const callout = $("#precedence-callout");
    const calloutText = $("#precedence-text");

    if (hasNlTiming) {
      if (callout) callout.classList.remove("hidden");
      if (calloutText) {
        calloutText.innerHTML = `<b>Smart Precedence:</b> Specific dates and times in your request take priority over selector pickers.`;
      }
    } else {
      if (callout) callout.classList.add("hidden");
    }

    let dtSummary = "";
    if (startVal && endVal) {
      dtSummary = `${startVal.replace("T", " ")} to ${endVal.replace("T", " ")}`;
    } else if (startVal) {
      dtSummary = `Starts: ${startVal.replace("T", " ")} (duration auto-fit)`;
    } else if (endVal) {
      dtSummary = `Ends by: ${endVal.replace("T", " ")}`;
    } else {
      dtSummary = hasNlTiming
        ? "Derived from your natural-language request"
        : "Auto-detecting optimal free window";
    }
    if ($("#rev-datetime")) $("#rev-datetime").textContent = dtSummary;

    // Location
    let locSummary = loc;
    if (dest) {
      locSummary = `${loc} → ${dest} (Trip Destination)`;
    } else {
      locSummary = `${loc} (Local outing)`;
    }
    if ($("#rev-location")) $("#rev-location").textContent = locSummary;

    // Budget & Group
    const budgetSummary = budgetVal && +budgetVal > 0
      ? `Budget: ${Number(budgetVal).toLocaleString()} ${currency} · Party: ${party} ${party == 1 ? "person" : "people"}`
      : `Budget: Flexible · Party: ${party} ${party == 1 ? "person" : "people"}`;
    if ($("#rev-budget")) $("#rev-budget").textContent = budgetSummary;

    // Preferences & Transport
    const prefsSummary = [
      interests.length ? `Interests: ${interests.join(", ")}` : "All activities",
      modes.length ? `Transport: ${modes.join(", ")}` : "Walk / Transit / Cab",
      `Pace: ${pace}`
    ].join(" · ");
    if ($("#rev-prefs")) $("#rev-prefs").textContent = prefsSummary;

    // Commitments
    const modeChoice = document.querySelector('input[name="commitments_mode"]:checked')?.value || "plan_around";
    let commitmentsSummary = "";
    const icsFile = $("#ics")?.files?.[0];
    if (modeChoice === "ignore") {
      commitmentsSummary = "Ignore Commitments chosen. Entire window will be treated as 100% open.";
    } else if (commitments.length > 0) {
      commitmentsSummary = `${commitments.length} commitment(s) declared. Planning strictly around them.`;
    } else if (icsFile) {
      commitmentsSummary = `Importing busy blocks from ${icsFile.name}.`;
    } else {
      commitmentsSummary = "No commitments added. Calendar is treated as completely open (zero constraints).";
    }
    if ($("#rev-commitments")) $("#rev-commitments").textContent = commitmentsSummary;
  }

  // ------------------------------------------------------------------ Submit Plan
  async function buildRequest() {
    const isMock = $("#toggle-mock")?.checked ?? false;
    const modeChoice = document.querySelector('input[name="commitments_mode"]:checked')?.value || "plan_around";
    const req = {
      query: $("#query").value.trim(),
      mode: $("#mode").value,
      location: $("#location").value.trim(),
      interests: selected($("#interests")),
      travel_modes: selected($("#modes")),
      party_size: +$("#party").value || 1,
      pace: $("#pace").value,
      commitments_mode: modeChoice,
      use_learned_schedule: $("#learned")?.checked ?? false,
      dev_mock: isMock
    };

    if ($("#location").dataset.lat) {
      req.coordinates = { lat: +$("#location").dataset.lat, lon: +$("#location").dataset.lon };
    }
    const destVal = $("#destination")?.value.trim();
    if (destVal) {
      req.destination = destVal;
      if (req.mode === "auto") {
        req.mode = "domestic_trip";
      }
    }
    if ($("#start")?.value) req.start = $("#start").value;
    if ($("#end")?.value) req.end = $("#end").value;
    if (+$("#budget")?.value > 0) {
      req.budget = { amount: +$("#budget").value, currency: $("#currency").value };
    }
    if ($("#plan-form")?.dataset.now) req.now = $("#plan-form").dataset.now;

    if (modeChoice === "ignore") {
      req.busy_blocks = [];
    } else {
      req.busy_blocks = commitments.map((b) => ({
        start: b.start,
        end: b.end,
        title: b.title || "Commitment",
        date: b.date || b.start.slice(0, 10),
        location: b.location || null,
        notes: b.notes || null,
      }));
    }

    const f = $("#ics")?.files?.[0];
    if (f && modeChoice !== "ignore") req.calendar_ics = await f.text();
    return req;
  }

  $("#plan-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const req = await buildRequest();
    startRun("/api/runs", req, "Creating itinerary");
  });

  // ------------------------------------------------------------------ Planning Activity Panel Stages Tracker
  function resetStages() {
    setStageStatus(1, "in-progress", "In progress");
    setStageStatus(2, "waiting", "Waiting");
    setStageStatus(3, "waiting", "Waiting");
    setStageStatus(4, "waiting", "Waiting");
    setStageStatus(5, "waiting", "Waiting");

    const overallStatus = $("#panel-overall-status");
    if (overallStatus) {
      overallStatus.textContent = "In progress";
      overallStatus.style.background = "var(--accent-primary-soft)";
      overallStatus.style.color = "var(--accent-primary)";
    }
  }

  function setStageStatus(stageNum, state, badgeText, descText) {
    const el = $(`#stage-${stageNum}`);
    if (!el) return;
    el.className = `stage-item ${state}`;
    const badge = el.querySelector(".stage-badge");
    if (badge) {
      badge.textContent = badgeText;
      if (state === "completed") {
        badge.style.color = "var(--ok)";
      } else if (state === "in-progress") {
        badge.style.color = "var(--accent-primary)";
      } else if (state === "needs-attention") {
        badge.style.color = "var(--warn)";
      } else {
        badge.style.color = "var(--text-muted)";
      }
    }
    if (descText) {
      const desc = el.querySelector(".stage-desc");
      if (desc) desc.textContent = descText;
    }
  }

  async function startRun(url, body, label) {
    const submitBtn = $("#submit");
    if (submitBtn) {
      submitBtn.disabled = true;
      const btnText = submitBtn.querySelector(".btn-text");
      if (btnText) btnText.textContent = "Planning Itinerary...";
    }

    $("#feed").innerHTML = "";
    resetStages();

    // Show Results view with skeleton/loading state
    $("#wizard-card")?.classList.add("hidden");
    $("#results-view")?.classList.remove("hidden");

    $("#plan-status").textContent = "Creating Plan...";
    $("#plan-status").className = "badge partial";
    $("#plan-summary").textContent = "Five specialized agents are coordinating to build your personalized schedule...";
    $("#totals").innerHTML = "";
    $("#timeline").innerHTML = `<div class="empty" style="padding: 28px 16px; text-align: center; color: var(--text-secondary);">
      <div style="font-size: 28px; margin-bottom: 8px;">⏳</div>
      <b>Collaborating Agents at Work</b><br>
      Finding verified venues, measuring travel buffers, and solving constraints...
    </div>`;

    // Expand activity panel during planning to show live progress
    const activityPanel = $("#activity-panel");
    if (activityPanel) activityPanel.open = true;

    setStatus(`<b>${label} in progress</b> — 5 agents are evaluating constraints, routing, and searching venues.`);

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
      if (submitBtn) {
        submitBtn.disabled = false;
        const btnText = submitBtn.querySelector(".btn-text");
        if (btnText) btnText.textContent = "Create My Itinerary";
      }
      setStageStatus(1, "needs-attention", "Needs attention");
      const overallStatus = $("#panel-overall-status");
      if (overallStatus) {
        overallStatus.textContent = "Failed";
        overallStatus.style.background = "var(--danger-soft)";
        overallStatus.style.color = "var(--danger)";
      }
      showToast(`Planning failed: ${err.message}`, 5000);
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
      setStageStatus(5, "completed", "Completed");

      const overallStatus = $("#panel-overall-status");
      if (overallStatus) {
        overallStatus.textContent = "Completed";
        overallStatus.style.background = "var(--ok-soft)";
        overallStatus.style.color = "var(--ok)";
      }

      const st = await (await fetch(`/api/runs/${runId}`)).json();
      const submitBtn = $("#submit");
      if (submitBtn) {
        submitBtn.disabled = false;
        const btnText = submitBtn.querySelector(".btn-text");
        if (btnText) btnText.textContent = "Create My Itinerary";
      }

      const dlJson = $("#dl-json");
      const dlMd = $("#dl-md");
      if (dlJson) dlJson.href = `/api/runs/${runId}/trace`;
      if (dlMd) dlMd.href = `/api/runs/${runId}/trace?format=md`;

      if ($("#trace-stats")) $("#trace-stats").innerHTML = stats(st.stats);

      if (st.status === "done" && st.result) {
        setStatus(`✅ <b>Plan Complete!</b> ${st.stats.llm_calls} LLM calls · ${st.stats.tool_calls} tool calls · ${st.stats.messages} agent messages.`);
        renderPlan(st.result);
        showToast("Your itinerary is ready!");
      } else {
        setStatus(`❌ <b>Plan Error:</b> ${esc(st.error || "Run did not complete")}`);
        $("#plan-status").textContent = "Planning Error";
        $("#plan-status").className = "badge failed";
        $("#plan-summary").textContent = st.error || "The agents were unable to complete the schedule.";
      }
    });

    source.onerror = () => { /* browser auto-reconnects */ };
  }

  const time = (ms) => (ms / 1000).toFixed(1) + "s";

  function renderEvent(e) {
    const d = e.data || {};

    // Progress stage updates based on actual agent events
    if (e.type === "agent_start") {
      if (e.agent === "coordinator") {
        setStageStatus(1, "in-progress", "Analyzing request");
      } else if (e.agent === "profile") {
        setStageStatus(1, "completed", "Completed");
        setStageStatus(2, "in-progress", "Checking routine & availability");
      } else if (e.agent === "discovery") {
        setStageStatus(2, "completed", "Completed");
        setStageStatus(3, "in-progress", "Finding places & weather");
      } else if (e.agent === "logistics") {
        setStageStatus(3, "completed", "Completed");
        setStageStatus(4, "in-progress", "Comparing travel routes");
      } else if (e.agent === "planner") {
        setStageStatus(4, "completed", "Completed");
        setStageStatus(5, "in-progress", "Drafting schedule", "Combinatorial solver is arranging stops, opening hours, and budget...");
      }
    } else if (e.type === "agent_end") {
      if (e.agent === "profile") setStageStatus(2, "completed", "Completed");
      if (e.agent === "discovery") setStageStatus(3, "completed", "Completed");
      if (e.agent === "logistics") setStageStatus(4, "completed", "Completed");
      if (e.agent === "planner") setStageStatus(5, "in-progress", "Validating plan", "Checking calendar fit, venue timings, and travel routes...");
    } else if (e.type === "message" && d.kind === "critique") {
      setStageStatus(5, "in-progress", `Review: ${e.agent} verified`, `Specialist agents (Profile, Discovery, Logistics) are verifying constraints...`);
    } else if (e.type === "decision" && (d.action === "finalize" || (e.content && e.content.includes("final")))) {
      setStageStatus(5, "in-progress", "Finalizing options", "Generating curated alternatives (Top Match, Budget-Saver, Quick Transit)...");
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
          <pre>args: ${esc(JSON.stringify(d.args, null, 1))}\nresult: ${esc(JSON.stringify(d.result, null, 1)).slice(0, 2500)}</pre></details>`;
        break;
      case "llm_call":
        cls = "llm" + ($("#show-llm")?.checked ? "" : " hidden");
        html = `💭 ${who(e.agent)} <span class="muted">${esc(d.model)} · ${d.duration_ms} ms${d.fallback_used ? " · FALLBACK" : ""}</span>
          ${d.tool_calls?.length ? " → " + d.tool_calls.map((t) => esc(t.name)).join(", ") : ""} ${e.content ? `<div>${esc(e.content.slice(0, 300))}</div>` : ""}`;
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
        html = `⚙️ Initialization: ${esc(e.content)}`;
        break;
      case "final":
        cls = "final";
        html = `🏁 Finalized: ${esc(e.content)}`;
        break;
      default:
        return;
    }

    const div = document.createElement("div");
    div.className = `ev ${cls}`;
    div.dataset.agent = agent;
    div.innerHTML = `<span class="t">${time(e.t_ms)}</span>${html}`;

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

  // ------------------------------------------------------------------ Itinerary Results View
  const money = (c, cur) => (c && c.amount != null ? `${c.estimate ? "~" : ""}${Math.round(c.amount).toLocaleString()} ${esc(c.currency || cur || "")}` : "");
  const hm = (iso) => (iso || "").slice(11, 16);

  let activeOptionIdx = 0;
  let currentTimelineSort = "schedule";
  let currentAltSort = "default";

  function renderPlan(p) {
    currentPlan = p;
    activeOptionIdx = 0;
    currentTimelineSort = "schedule";
    currentAltSort = "default";

    const sortSelect = $("#timeline-sort-select");
    if (sortSelect) sortSelect.value = "schedule";

    const statusBadge = $("#plan-status");
    if (statusBadge) {
      statusBadge.textContent = p.status === "ok" ? "Optimized Plan" : p.status;
      statusBadge.className = `badge ${p.status === "ok" ? "ok" : "partial"}`;
    }

    // Curated Options tabs
    const optionsContainer = $("#itinerary-options-container");
    const optionsTabs = $("#itinerary-options-tabs");
    if (p.options && p.options.length > 1 && optionsTabs && optionsContainer) {
      optionsContainer.classList.remove("hidden");
      optionsTabs.innerHTML = p.options.map((opt, i) => `
        <button type="button" class="btn-option-tab ${i === activeOptionIdx ? "active" : ""}" data-opt-idx="${i}">
          <span class="option-title">${esc(opt.title || `Option ${i + 1}`)}</span>
          ${opt.badge ? `<span class="badge ${opt.badge === "Top Match" ? "ok" : "accent"}">${esc(opt.badge)}</span>` : ""}
          <span class="option-summary-stat">${opt.totals?.cost != null ? `${Math.round(opt.totals.cost)} ${esc(opt.totals.currency || "")}` : ""} · ${opt.totals?.travel_minutes ?? 0}m travel</span>
        </button>
      `).join("");

      $$(".btn-option-tab", optionsTabs).forEach((btn) => {
        btn.addEventListener("click", () => {
          activeOptionIdx = parseInt(btn.dataset.optIdx, 10);
          $$(".btn-option-tab", optionsTabs).forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          updateActiveOption();
        });
      });
    } else if (optionsContainer) {
      optionsContainer.classList.add("hidden");
    }

    if ($("#weather")) {
      $("#weather").innerHTML = (p.weather?.daily || []).map((w) =>
        `<span class="w">📅 <b>${esc(w.date)}:</b> ${esc(w.desc)}, ${esc(w.t_min)}–${esc(w.t_max)}°C${w.precip_prob_max != null ? `, rain ${esc(w.precip_prob_max)}%` : ""}</span>`
      ).join("") + (p.weather?.kind && p.weather.kind !== "forecast" ? `<span class="w">(${esc(p.weather.kind)})</span>` : "");
    }

    if ($("#conflicts")) {
      $("#conflicts").innerHTML = p.conflicts?.length ? `<div class="box conf"><b>⚡ Autonomous Conflict Resolutions (Agent Critique)</b><ul>${p.conflicts.map((c) =>
        `<li><b>${esc(c.type)}</b> (raised by ${esc(c.raised_by.join(", "))}): ${esc(c.description)}<br><span class="muted">→ Resolution: ${esc(c.resolution)}</span></li>`).join("")}</ul></div>` : "";
    }

    if ($("#warnings")) {
      $("#warnings").innerHTML = p.warnings?.length ? `<div class="box warn"><b>Notes & Feasibility Details</b><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
    }

    renderAlternatives();
    renderCostLedger(p);
    updateActiveOption();
  }

  function renderCostLedger(p) {
    const card = $("#cost-ledger-card");
    if (!card) return;
    const ledger = p.cost_ledger;
    if (!ledger) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");

    const cur = ledger.currency || p.currency || "INR";
    const totalEl = $("#ledger-total-cost");
    if (totalEl) totalEl.textContent = `${Number(ledger.total_cost).toLocaleString()} ${cur}`;

    const badge = $("#ledger-budget-badge");
    if (badge) {
      if (ledger.budget != null) {
        if (ledger.is_within_budget) {
          badge.textContent = `Within Budget (₹${ledger.total_cost} / ₹${ledger.budget})`;
          badge.className = "badge ok";
        } else {
          badge.textContent = `Over Budget by ₹${Math.abs(ledger.remaining_budget || 0)}`;
          badge.className = "badge danger";
        }
      } else {
        badge.textContent = "Estimated Expenses";
        badge.className = "badge accent";
      }
    }

    if ($("#ledger-acc")) $("#ledger-acc").textContent = `${Number(ledger.accommodation).toLocaleString()} ${cur}`;
    if ($("#ledger-intercity")) $("#ledger-intercity").textContent = `${Number(ledger.intercity_transport).toLocaleString()} ${cur}`;
    if ($("#ledger-local")) $("#ledger-local").textContent = `${Number(ledger.local_transport).toLocaleString()} ${cur}`;
    if ($("#ledger-dining")) $("#ledger-dining").textContent = `${Number(ledger.food_and_dining).toLocaleString()} ${cur}`;
    if ($("#ledger-activities")) $("#ledger-activities").textContent = `${Number(ledger.activities_and_entries).toLocaleString()} ${cur}`;
    if ($("#ledger-contingency")) $("#ledger-contingency").textContent = `${Number(ledger.contingency).toLocaleString()} ${cur}`;
    if ($("#ledger-remaining")) {
      const rem = ledger.remaining_budget;
      $("#ledger-remaining").textContent = rem != null ? `${Number(rem).toLocaleString()} ${cur}` : "N/A";
    }

    const notesContainer = $("#ledger-notes");
    if (notesContainer) {
      const notes = [];
      if (ledger.verified_items_count > 0 || ledger.estimated_items_count > 0) {
        notes.push(`<span>✓ ${ledger.verified_items_count} verified rates · ~ ${ledger.estimated_items_count} estimates</span>`);
      }
      (ledger.breakdown_notes || []).forEach((n) => notes.push(`<span>• ${esc(n)}</span>`));
      notesContainer.innerHTML = notes.join("");
    }
  }

  function updateActiveOption() {
    if (!currentPlan) return;
    const opt = (currentPlan.options && currentPlan.options[activeOptionIdx]) || {
      title: "Option 1: Recommended",
      summary: currentPlan.summary,
      totals: currentPlan.totals,
      days: currentPlan.days,
    };

    if ($("#plan-summary")) $("#plan-summary").textContent = opt.summary || currentPlan.summary;

    const optBadge = $("#active-option-badge");
    if (optBadge) {
      if (currentPlan.options && currentPlan.options.length > 1) {
        optBadge.textContent = opt.title || `Option ${activeOptionIdx + 1}`;
        optBadge.classList.remove("hidden");
      } else {
        optBadge.classList.add("hidden");
      }
    }

    const t = opt.totals || currentPlan.totals || {};
    const pct = t.budget ? Math.min(100, Math.round((t.cost / t.budget) * 100)) : null;
    const planParty = currentPlan.request?.party_size || +($("#party")?.value) || 1;
    const partySuffix = planParty > 1 ? ` (${planParty} people)` : "";

    if ($("#totals")) {
      $("#totals").innerHTML = `
        <div class="stat">
          <b>${esc(t.cost ?? 0)} ${esc(t.currency || "")}</b>
          <span>Estimated cost${partySuffix}${t.budget ? ` of ${esc(t.budget)} budget` : ""}</span>
          ${pct != null ? `<div class="budget-bar"><i style="width:${pct}%;background:${pct > 100 ? "var(--danger)" : "var(--ok)"}"></i></div>` : ""}
        </div>
        <div class="stat"><b>${esc(t.stops ?? 0)}</b><span>Scheduled Stops</span></div>
        <div class="stat"><b>${esc(t.travel_minutes ?? 0)} min</b><span>Travel Time</span></div>
        <div class="stat"><b>${esc(currentPlan.stats?.coordinator_steps ?? "–")}</b><span>Coordinator Steps (${esc(currentPlan.stats?.planner_revisions ?? 0)} revisions)</span></div>`;
    }

    renderTimeline();

    const opts = [];
    let idx = 0;
    (opt.days || []).forEach((d) => d.items.forEach((it) => {
      if (it.kind !== "busy") opts.push(`<option value="${idx}">${hm(it.start)} ${esc(it.title)}</option>`);
      idx++;
    }));
    if ($("#replan-item")) {
      $("#replan-item").innerHTML = `<option value="">(Select affected stop — optional)</option>` + opts.join("");
    }

    setTimeout(drawMap, 60);
  }

  function renderActivityCard(it, num, cur) {
    const isStop = ["meal", "snack", "activity", "event"].includes(it.kind);
    const isTravel = it.kind === "travel";
    let placeInfo = "";
    if (it.place) {
      const queryStr = encodeURIComponent((it.place.name || it.title) + " " + (it.place.address || ""));
      const navUrl = `https://www.google.com/maps/search/?api=1&query=${queryStr}`;
      placeInfo = `${it.place.address ? esc(it.place.address) : ""}${it.place.url ? ` · <a href="${esc(it.place.url)}" target="_blank" rel="noopener">Website</a>` : ""} · <a class="nav-link" href="${navUrl}" target="_blank" rel="noopener">📍 Directions</a>${it.place.source ? ` · <span class="muted">via ${esc(it.place.source)}</span>` : ""}`;
    }
    const tr = it.travel ? `${esc(it.travel.km)} km · ${esc(it.travel.source || "road network")}` : "";

    return `
      <div class="activity-card${isTravel ? " travel" : ""}">
        <div class="time">${hm(it.start)}–${hm(it.end)}</div>
        <div class="icon-box">${isTravel && it.travel ? (MODE_ICON[it.travel.mode] || ICON.travel) : (ICON[it.kind] || "•")}</div>
        <div>
          <div class="title">${isStop ? num + ". " : ""}${esc(it.title)}</div>
          <div class="meta">${placeInfo || tr}${it.notes ? " · " + esc(it.notes) : ""}</div>
        </div>
        <div class="cost">${money(it.cost, cur)}</div>
      </div>`;
  }

  function renderTimeline() {
    if (!currentPlan) return;
    const opt = (currentPlan.options && currentPlan.options[activeOptionIdx]) || { days: currentPlan.days, totals: currentPlan.totals };
    const timelineEl = $("#timeline");
    if (!timelineEl) return;

    const cur = opt.totals?.currency || currentPlan.totals?.currency || "INR";
    const days = opt.days || [];

    if (!days.length || days.every((d) => !d.items || !d.items.length)) {
      timelineEl.innerHTML = `<div class="empty">The agents could not schedule any items in the available window.</div>`;
      return;
    }

    let n = 0;
    timelineEl.innerHTML = days.map((d) => {
      // Priority 6: Items within each day strictly follow chronological order by start time!
      const sortedItems = [...(d.items || [])].sort((a, b) => (a.start > b.start ? 1 : (a.start < b.start ? -1 : 0)));
      return `
        <div class="day-group">
          <div class="day-date-title">📅 ${esc(new Date(d.date + "T00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric", year: "numeric" }))}</div>
          ${sortedItems.map((it) => renderActivityCard(it, ["meal", "snack", "activity", "event"].includes(it.kind) ? ++n : null, cur)).join("")}
        </div>
      `;
    }).join("");
  }

  // Alternatives rendering & sorting
  function renderAlternatives() {
    if (!currentPlan) return;
    const altsContainer = $("#alternatives");
    if (!altsContainer) return;
    const alts = [...(currentPlan.alternatives || [])];
    if (!alts.length) {
      $("#alternatives-wrapper")?.classList.add("hidden");
      return;
    }
    $("#alternatives-wrapper")?.classList.remove("hidden");

    if (currentAltSort === "price") {
      alts.sort((a, b) => (a.est_cost ?? 9999) - (b.est_cost ?? 9999));
    } else if (currentAltSort === "distance") {
      alts.sort((a, b) => (a.distance_km ?? 99) - (b.distance_km ?? 99));
    } else if (currentAltSort === "time") {
      alts.sort((a, b) => (a.travel_min ?? 99) - (b.travel_min ?? 99));
    }

    const cur = currentPlan.totals?.currency || "INR";
    altsContainer.innerHTML = `
      <div class="alt-card-grid">
        ${alts.map((a) => {
          const costStr = a.est_cost != null && a.est_cost > 0 ? `💵 ~${a.est_cost} ${esc(cur)}` : "Free entry";
          const distStr = a.distance_km != null ? `📍 ${a.distance_km} km` : "";
          const timeStr = a.travel_min != null ? `⚡ ${a.travel_min} min` : "";
          const queryStr = encodeURIComponent(a.name);
          const navUrl = `https://www.google.com/maps/search/?api=1&query=${queryStr}`;
          return `
            <div class="alt-item-card">
              <div class="alt-item-name">${esc(a.name)}</div>
              <div class="alt-item-meta">
                ${a.category ? `<span class="alt-badge">${esc(a.category)}</span>` : ""}
                ${costStr ? `<span class="alt-badge">${costStr}</span>` : ""}
                ${distStr ? `<span class="alt-badge">${distStr}</span>` : ""}
                ${timeStr ? `<span class="alt-badge">${timeStr}</span>` : ""}
                <a class="nav-link" href="${navUrl}" target="_blank" rel="noopener">Directions</a>
              </div>
            </div>`;
        }).join("")}
      </div>`;
  }

  // Alternatives sort pills listeners
  $$("[data-alt-sort]").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentAltSort = btn.dataset.altSort;
      $$("[data-alt-sort]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderAlternatives();
    });
  });

  // ------------------------------------------------------------------ Itinerary Actions
  // Edit Plan: return to wizard keeping inputs
  $("#btn-edit-plan")?.addEventListener("click", () => {
    $("#results-view")?.classList.add("hidden");
    $("#wizard-card")?.classList.remove("hidden");
    goToStep(1);
  });

  // New Plan: clear and return to wizard Step 1
  $("#btn-new-plan")?.addEventListener("click", () => {
    if ($("#query")) $("#query").value = "";
    if ($("#start")) $("#start").value = "";
    if ($("#end")) $("#end").value = "";
    commitments = [];
    renderCommitments();
    $("#results-view")?.classList.add("hidden");
    $("#wizard-card")?.classList.remove("hidden");
    goToStep(1);
    showToast("Ready for a new plan!");
  });

  // Calendar .ics export
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

  // Copy Plan Text
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

  // Print / PDF
  $("#btn-print-plan")?.addEventListener("click", () => {
    window.print();
  });

  // ------------------------------------------------------------------ Replan
  $$("[data-replan]").forEach((b) => b.addEventListener("click", () => {
    if (!currentPlan) return;
    const kind = b.dataset.replan;
    const text = $("#replan-text")?.value.trim();
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
    if (kind === "closed" && (!sel || sel === "")) {
      return alert("Pick the affected venue stop from the dropdown first");
    }
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
        liked: split($("#liked")?.value || ""),
        disliked: split($("#disliked")?.value || "")
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

  // ------------------------------------------------------------------ Leaflet Map
  function drawMap() {
    if (!window.L) {
      const mapEl = $("#map");
      if (mapEl) mapEl.innerHTML = "<div class='empty'>Map library could not load.</div>";
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
    const activeDays = (currentPlan.options && currentPlan.options[activeOptionIdx]?.days) || currentPlan.days || [];
    activeDays.forEach((d) => d.items.forEach((it) => {
      if (!it.place || it.place.lat == null) return;
      const isStop = ["meal", "snack", "activity", "event"].includes(it.kind);
      const label = isStop ? ++n : "•";
      const icon = L.divIcon({ className: "", html: `<div class="map-num-pin">${label}</div>`, iconSize: [26, 26] });
      L.marker([it.place.lat, it.place.lon], { icon })
        .bindPopup(`<b>${esc(it.title)}</b><br>${hm(it.start)}–${hm(it.end)}<br><small>${esc(it.place.address || "")}</small>`)
        .addTo(mapLayer);
      pts.push([it.place.lat, it.place.lon]);
    }));

    if (pts.length > 1) {
      L.polyline(pts, { color: "#6558E8", weight: 3.5, dashArray: "6 6" }).addTo(mapLayer);
    }
    if (pts.length) {
      map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
    }
  }

  // Initial step setup
  goToStep(1);
  checkAutoCalendar();
})();
