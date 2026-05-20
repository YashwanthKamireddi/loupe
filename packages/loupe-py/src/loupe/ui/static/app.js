"use strict";

const els = {
  list: document.getElementById("trace-list"),
  count: document.getElementById("trace-count"),
  summary: document.getElementById("trace-summary"),
  stats: document.getElementById("stats"),
  search: document.getElementById("trace-search"),
  filterBar: document.getElementById("filter-bar"),
  viewer: document.getElementById("viewer"),
  template: document.getElementById("trace-view-tmpl"),
  tagForm: document.getElementById("tag-form-tmpl"),
  helpButton: document.getElementById("help-button"),
  helpModal: document.getElementById("help-modal"),
  liveIndicator: document.getElementById("live-indicator"),
  emptyOnboard: document.getElementById("empty-onboard"),
};

const state = {
  traceId: null,
  trace: null,
  stepIdx: -1,
  traces: [],
  filter: "",
  statusFilter: "all", // 'all' | 'failed' | 'tagged'
  initialLoad: true,
};

function setLiveState(kind) {
  if (!els.liveIndicator) return;
  els.liveIndicator.classList.remove("is-live", "is-stale");
  let label, title;
  if (kind === "live") {
    els.liveIndicator.classList.add("is-live");
    label = "live"; title = "Live: new traces stream in automatically";
  } else if (kind === "stale") {
    els.liveIndicator.classList.add("is-stale");
    label = "reconnecting"; title = "Reconnecting to the live stream…";
  } else {
    label = "connecting"; title = "Connecting to the live stream…";
  }
  const lbl = els.liveIndicator.querySelector(".live-label");
  if (lbl) lbl.textContent = label;
  els.liveIndicator.title = title;
}

function renderSkeleton() {
  els.list.innerHTML =
    '<div class="skeleton-list">' +
    '<div class="skeleton-row"></div>'.repeat(4) +
    "</div>";
  if (els.count) els.count.textContent = "…";
}

/* ----- formatting helpers ------------------------------------------------ */

const escapeHtml = (s) => {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
};

const prettyJson = (v) => {
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
};

function formatDuration(ms) {
  if (ms == null || !isFinite(ms) || ms < 0) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function formatRelative(unixSec) {
  if (unixSec == null) return "";
  const now = Date.now() / 1000;
  const delta = Math.max(0, now - unixSec);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d ago`;
  return new Date(unixSec * 1000).toISOString().slice(0, 10);
}

async function exportCurrentTraceMarkdown() {
  if (!state.traceId) return;
  try {
    const md = await (await fetch(`/api/traces/${state.traceId}/report`)).text();
    await navigator.clipboard?.writeText(md);
    flashToast("Markdown report copied to clipboard");
  } catch (err) {
    console.error(err);
    flashToast("Could not copy report");
  }
}

function flashToast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast" + (kind === "error" ? " toast-error" : "");
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add("fade"); }, kind === "error" ? 2200 : 1400);
  setTimeout(() => el.remove(), kind === "error" ? 2800 : 2000);
}

function traceDurationMs(t) {
  if (t?.ended_at && t?.started_at) {
    return Math.max(0, (t.ended_at - t.started_at) * 1000);
  }
  return null;
}

function stepDurationMs(s) {
  if (s?.ended_at && s?.started_at) {
    return Math.max(0, (s.ended_at - s.started_at) * 1000);
  }
  return null;
}

/* ----- top-level loaders ------------------------------------------------- */

async function loadStats() {
  try {
    const s = await (await fetch("/api/stats")).json();
    els.stats.innerHTML = `
      <span class="stat"><span class="stat-v">${s.trace_count}</span><span class="stat-k">traces</span></span>
      <span class="stat"><span class="stat-v">${s.failed_count}</span><span class="stat-k">failed</span></span>
      <span class="stat"><span class="stat-v">${s.step_count}</span><span class="stat-k">steps</span></span>
      <span class="stat"><span class="stat-v">${s.annotation_count}</span><span class="stat-k">tagged</span></span>
    `;
  } catch (_) { /* server still booting */ }
}

async function loadTraces() {
  if (state.initialLoad) renderSkeleton();
  try {
    state.traces = await (await fetch("/api/traces")).json();
  } catch (err) {
    console.error(err);
    flashToast("Could not reach the Loupe server.", "error");
    state.traces = [];
  }
  state.initialLoad = false;
  renderTraceList();
  updateEmptyState();
  if (state.traces.length > 0 && !state.traceId) {
    openTrace(state.traces[0].trace_id);
  }
  loadStats();
}

function updateEmptyState() {
  // Tighten the copy when there genuinely aren't any traces yet vs when
  // there are some but nothing's selected. Also reveal the onboarding
  // walkthrough only in the truly-empty case.
  const sub = document.getElementById("empty-state-sub");
  if (sub) {
    if (state.traces.length === 0) {
      sub.textContent =
        "No captured runs yet. Below is the fastest way to put one here.";
    } else {
      sub.textContent = "Select a case file from the left.";
    }
  }
  if (els.emptyOnboard) {
    els.emptyOnboard.hidden = state.traces.length !== 0;
  }
}

function renderTraceList() {
  const q = state.filter.toLowerCase().trim();
  const filtered = state.traces.filter((t) => {
    if (state.statusFilter === "failed" && !(t.metadata?.failed)) return false;
    if (state.statusFilter === "tagged" && !(t.annotation_count > 0)) return false;
    if (!q) return true;
    return (
      (t.name || "").toLowerCase().includes(q) ||
      (t.framework || "").toLowerCase().includes(q) ||
      (t.trace_id || "").toLowerCase().includes(q)
    );
  });

  els.count.textContent = `${filtered.length} ${filtered.length === 1 ? "run" : "runs"}`;
  els.list.innerHTML = "";

  if (filtered.length === 0) {
    const li = document.createElement("li");
    li.style.cursor = "default";
    li.style.color = "var(--ink-3)";
    li.style.fontSize = "var(--t-sm)";
    li.style.borderLeft = "none";
    if (state.traces.length === 0) {
      li.innerHTML = `<div style="line-height:1.55">No traces yet.<br><span style="color:var(--ink-4)">Scaffold one with <code style="color:var(--amber)">loupe init my-agent</code>.</span></div>`;
    } else {
      li.textContent = "No matches.";
    }
    els.list.appendChild(li);
    return;
  }

  filtered.forEach((t) => {
    const li = document.createElement("li");
    li.dataset.id = t.trace_id;
    if (t.trace_id === state.traceId) li.classList.add("active");
    const failed = t.metadata?.failed;
    const tagPill = t.annotation_count > 0
      ? `<span class="t-tag-pill">${t.annotation_count} tag${t.annotation_count > 1 ? "s" : ""}</span>`
      : "";
    const dur = formatDuration(traceDurationMs(t));
    const rel = formatRelative(t.started_at);
    li.innerHTML = `
      <div class="t-name">${escapeHtml(t.name)}${tagPill}</div>
      <div class="t-meta">
        <span>${escapeHtml(t.framework || "—")}</span>
        <span>${t.step_count} ${t.step_count === 1 ? "step" : "steps"}</span>
        <span>${dur}</span>
        <span>${rel}</span>
      </div>
      <div class="t-status ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</div>
    `;
    li.addEventListener("click", () => openTrace(t.trace_id));
    els.list.appendChild(li);
  });
}

async function openTrace(traceId) {
  if (state.traceId !== traceId) {
    state.traceId = traceId;
    state.stepIdx = -1;
  }
  document.querySelectorAll(".trace-list li").forEach((li) => {
    li.classList.toggle("active", li.dataset.id === traceId);
  });
  state.trace = await (await fetch(`/api/traces/${traceId}`)).json();
  renderTrace();
}

/* ----- main trace render ------------------------------------------------- */

function renderTrace() {
  const trace = state.trace;
  if (!trace) return;
  const failed = trace.metadata?.failed;
  const annCount = (trace.annotations || []).length;
  const dur = formatDuration(traceDurationMs(trace));

  els.summary.innerHTML = `
    <span class="meta-title">${escapeHtml(trace.name)}</span>
    <span class="pill">${escapeHtml(trace.framework || "—")}</span>
    <span class="pill">${trace.steps.length} ${trace.steps.length === 1 ? "step" : "steps"}</span>
    <span class="pill">${dur}</span>
    ${annCount > 0 ? `<span class="pill amber">${annCount} tagged</span>` : ""}
    <span class="pill ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</span>
    <button type="button" class="pill-action" id="export-md" title="Copy markdown report (e)">↗ Export</button>
  `;
  const exportBtn = document.getElementById("export-md");
  if (exportBtn) exportBtn.addEventListener("click", exportCurrentTraceMarkdown);

  els.viewer.innerHTML = "";
  els.viewer.appendChild(els.template.content.cloneNode(true));

  const timeline = els.viewer.querySelector("[data-track]");
  const stepList = els.viewer.querySelector("[data-steps]");

  const annByStep = new Map();
  (trace.annotations || []).forEach((a) => annByStep.set(a.step_id, a));

  trace.steps.forEach((step, idx) => {
    const tagged = annByStep.has(step.step_id);
    const stepDur = stepDurationMs(step);

    // Timeline cell
    const tlEl = document.createElement("div");
    tlEl.className = `tl-step kind-${step.kind}`;
    if (step.error) tlEl.classList.add("failed");
    if (tagged) tlEl.classList.add("tagged");
    tlEl.title = `${step.kind} · ${step.name}${stepDur != null ? ` · ${formatDuration(stepDur)}` : ""}`;
    tlEl.innerHTML = `
      <span class="tl-kind">${escapeHtml(step.kind)}</span>
      <span class="tl-name">${escapeHtml(step.name)}</span>
      <span class="tl-dur">${formatDuration(stepDur)}</span>
    `;
    tlEl.addEventListener("click", () => selectStep(idx));
    timeline.appendChild(tlEl);

    // Step list row
    const liEl = document.createElement("li");
    liEl.className = `kind-${step.kind}`;
    if (step.error) liEl.classList.add("failed");
    liEl.innerHTML = `
      <span class="step-main">
        <span class="kind-chip">${escapeHtml(step.kind)}</span>
        <span class="step-name">${escapeHtml(step.name)}</span>
      </span>
      <span class="step-side">
        <span class="step-dur">${formatDuration(stepDur)}</span>
        ${tagged ? '<span class="tag-dot" title="tagged for LoupeBench">◉</span>' : ""}
      </span>
    `;
    liEl.addEventListener("click", () => selectStep(idx));
    stepList.appendChild(liEl);
  });

  if (trace.steps.length > 0) {
    const firstFailing = trace.steps.findIndex((s) => s.error);
    const initial = state.stepIdx >= 0 && state.stepIdx < trace.steps.length
      ? state.stepIdx
      : (firstFailing >= 0 ? firstFailing : 0);
    selectStep(initial);
  }
}

function selectStep(idx) {
  if (!state.trace || !state.trace.steps[idx]) return;
  state.stepIdx = idx;
  const step = state.trace.steps[idx];

  els.viewer.querySelectorAll(".tl-step").forEach((el, i) =>
    el.classList.toggle("active", i === idx)
  );
  els.viewer.querySelectorAll(".step-list li").forEach((el, i) =>
    el.classList.toggle("active", i === idx)
  );

  // Scroll active step into view in step list
  const activeLi = els.viewer.querySelectorAll(".step-list li")[idx];
  if (activeLi) activeLi.scrollIntoView({ block: "nearest" });

  const detail = els.viewer.querySelector("[data-detail]");
  const ann = (state.trace.annotations || []).find((a) => a.step_id === step.step_id);
  detail.innerHTML = renderDetail(step, ann);
  attachDetailHandlers(detail, step, ann);
}

/* ----- evidence panel rendering ----------------------------------------- */

function renderDetail(step, ann) {
  const dur = formatDuration(stepDurationMs(step));
  const out = [];

  out.push(`
    <div class="detail-head">
      <div class="detail-eyebrow">${escapeHtml(step.kind)} step</div>
      <h2 class="detail-title">${escapeHtml(step.name)}</h2>
    </div>
  `);

  out.push(`
    <div class="kv">
      <div class="k">duration</div><div class="v">${dur}</div>
      <div class="k">step id</div><div class="v">${escapeHtml(step.step_id)}</div>
      ${step.parent_step_id ? `<div class="k">parent</div><div class="v">${escapeHtml(step.parent_step_id)}</div>` : ""}
    </div>
  `);

  if (step.error) {
    out.push(`<div class="error-banner">${escapeHtml(step.error)}</div>`);
  }

  if (ann) {
    out.push(renderAnnotationCard(ann));
  } else if (step.error) {
    out.push(`
      <div class="action-row">
        <button type="button" class="btn-primary" data-action="tag">Tag this failure</button>
        <button type="button" class="btn-ghost" data-action="copy-id">Copy step id</button>
      </div>
    `);
  } else {
    out.push(`
      <div class="action-row">
        <button type="button" class="btn-secondary" data-action="tag">Tag for LoupeBench</button>
        <button type="button" class="btn-ghost" data-action="copy-id">Copy step id</button>
      </div>
    `);
  }

  if (step.inputs && Object.keys(step.inputs).length > 0) {
    out.push(`<div class="section-h">inputs</div>`);
    out.push(`<pre>${escapeHtml(prettyJson(step.inputs))}</pre>`);
  }
  if (step.outputs && Object.keys(step.outputs).length > 0) {
    out.push(`<div class="section-h">outputs</div>`);
    out.push(`<pre>${escapeHtml(prettyJson(step.outputs))}</pre>`);
  }
  if (step.metadata && Object.keys(step.metadata).length > 0) {
    out.push(`<div class="section-h">metadata</div>`);
    out.push(`<pre>${escapeHtml(prettyJson(step.metadata))}</pre>`);
  }

  return out.join("");
}

function renderAnnotationCard(ann) {
  return `
    <div class="annot-card">
      <div class="annot-head">
        <span class="annot-eyebrow">Tagged for LoupeBench<button class="term-help" type="button" data-term="loupebench" aria-label="What is LoupeBench?" aria-expanded="false">?</button></span>
        <span class="annot-cat">${escapeHtml(ann.failure_category)}</span>
        <span class="annot-sev">${escapeHtml(ann.severity || "")}</span>
      </div>
      ${ann.notes ? `<div class="annot-notes">${escapeHtml(ann.notes)}</div>` : ""}
      ${ann.mitigation ? `<div class="annot-mit">${escapeHtml(ann.mitigation)}</div>` : ""}
      <div class="annot-actions">
        <button type="button" class="btn-secondary" data-action="retag">Edit</button>
        <button type="button" class="btn-ghost" data-action="untag">Remove tag</button>
      </div>
    </div>
    ${renderAttributionCard(ann.circuit_attribution)}
  `;
}

function renderAttributionCard(attr) {
  // Defensive: the field may be empty {}, undefined, or a full shape from
  // loupe.attribution.AttributionResult.to_json_dict(). Render only when
  // we have at least one feature to show.
  if (!attr || typeof attr !== "object") return "";
  const features = Array.isArray(attr.top_features) ? attr.top_features : [];
  if (features.length === 0) return "";

  const maxAct = features.reduce(
    (m, f) => (typeof f.activation === "number" && f.activation > m ? f.activation : m),
    0,
  ) || 1;

  const rows = features.map((f) => {
    const pct = Math.max(0, Math.min(100, (Number(f.activation) / maxAct) * 100));
    const desc = f.description ? escapeHtml(f.description) : "";
    const layerLabel = f.layer ? escapeHtml(f.layer) : "";
    // When we have a Neuronpedia explanation we surface it instead of the
    // raw layer name — humans can read "phrases related to legal rulings"
    // but no one's brain matches blocks.6.hook_resid_pre to anything useful.
    const trailing = desc
      ? `<span class="attr-desc">${desc}</span>`
      : `<span class="attr-layer">${layerLabel}</span>`;
    return `
      <li class="attr-row${desc ? " has-desc" : ""}">
        <span class="attr-id">#${escapeHtml(String(f.feature_id))}</span>
        <span class="attr-bar"><span class="attr-bar-fill" style="width:${pct.toFixed(1)}%"></span></span>
        <span class="attr-act">${Number(f.activation).toFixed(3)}</span>
        ${trailing}
      </li>
    `;
  }).join("");

  const summary = attr.summary ? `<div class="attr-summary">${escapeHtml(attr.summary)}</div>` : "";
  const provenance = [attr.model, attr.sae, attr.method]
    .filter(Boolean)
    .map(escapeHtml)
    .join(" · ");

  return `
    <div class="attr-card">
      <div class="attr-head">
        <span class="attr-eyebrow">Circuit attribution<button class="term-help" type="button" data-term="circuit-attribution" aria-label="What is circuit attribution?" aria-expanded="false">?</button></span>
        <span class="attr-prov">${provenance}</span>
      </div>
      ${summary}
      <ol class="attr-list">${rows}</ol>
    </div>
  `;
}

function attachDetailHandlers(detail, step, ann) {
  detail.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      if (action === "tag" || action === "retag") openTagForm(step, ann);
      else if (action === "untag") removeTag(step);
      else if (action === "copy-id") copyToClipboard(step.step_id, btn);
    });
  });
}

function copyToClipboard(text, btn) {
  navigator.clipboard?.writeText(text).then(() => {
    const original = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = original; }, 1200);
  });
}

function openTagForm(step, ann) {
  const detail = els.viewer.querySelector("[data-detail]");
  detail.querySelectorAll(".tag-form").forEach((el) => el.remove());

  const node = els.tagForm.content.cloneNode(true);
  detail.appendChild(node);
  const form = detail.querySelector("[data-tag-form]");

  if (ann) {
    form.querySelector("[data-cat]").value = ann.failure_category;
    form.querySelector("[data-sev]").value = ann.severity || "medium";
    form.querySelector("[data-notes]").value = ann.notes || "";
    form.querySelector("[data-mit]").value = ann.mitigation || "";
  }
  form.querySelector("[data-cancel]").addEventListener("click", () => form.remove());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = "Saving…";
    try {
      const res = await fetch(`/api/traces/${state.traceId}/annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          step_id: step.step_id,
          failure_category: form.querySelector("[data-cat]").value,
          severity: form.querySelector("[data-sev]").value,
          notes: form.querySelector("[data-notes]").value,
          mitigation: form.querySelector("[data-mit]").value,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      flashToast(ann ? "Tag updated" : "Tagged for LoupeBench");
      await refresh();
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Save tag";
      flashToast("Save failed — check the server log", "error");
      console.error("save failed", err);
    }
  });
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  form.querySelector("[data-cat]").focus();
}

async function removeTag(step) {
  try {
    const res = await fetch(
      `/api/traces/${state.traceId}/annotations/${step.step_id}`,
      { method: "DELETE" },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    flashToast("Tag removed");
  } catch (err) {
    flashToast("Could not remove tag", "error");
    console.error(err);
    return;
  }
  await refresh();
}

async function refresh() {
  const idx = state.stepIdx;
  state.trace = await (await fetch(`/api/traces/${state.traceId}`)).json();
  renderTrace();
  if (idx >= 0) selectStep(idx);
  loadStats();
  state.traces = await (await fetch("/api/traces")).json();
  renderTraceList();
}

/* ----- input + keyboard -------------------------------------------------- */

// Debounced server-side search. The local `state.filter` still drives
// instant client-side rendering against the currently-loaded trace list,
// AND we re-fetch from /api/traces?q=... after a 200ms quiet window so
// step-content matches (kind / name / error) show up too.
let _searchTimer = 0;
els.search.addEventListener("input", (e) => {
  state.filter = e.target.value || "";
  renderTraceList();
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(async () => {
    const q = state.filter.trim();
    const url = q ? `/api/traces?q=${encodeURIComponent(q)}` : "/api/traces";
    try {
      state.traces = await (await fetch(url)).json();
    } catch (err) {
      console.error(err);
      return;
    }
    renderTraceList();
    updateEmptyState();
  }, 200);
});

// Status filter chips
els.filterBar?.querySelectorAll(".filter-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.statusFilter = btn.dataset.filter;
    els.filterBar.querySelectorAll(".filter-chip").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    renderTraceList();
  });
});

// Help modal
function openHelp() { els.helpModal.hidden = false; }
function closeHelp() { els.helpModal.hidden = true; }
els.helpButton?.addEventListener("click", openHelp);
els.helpModal?.addEventListener("click", (e) => {
  if (e.target === els.helpModal || e.target.matches("[data-close-modal]")) closeHelp();
});

document.addEventListener("keydown", (e) => {
  const activeTag = document.activeElement?.tagName;
  const inInput = activeTag && ["INPUT", "TEXTAREA", "SELECT"].includes(activeTag);

  // Esc always closes modal
  if (e.key === "Escape") { closeHelp(); return; }
  if (inInput) return;

  // Step navigation: ↑ / ↓ / j / k
  if (["ArrowDown", "ArrowUp", "j", "k"].includes(e.key)) {
    const items = Array.from(document.querySelectorAll(".step-list li"));
    if (!items.length) return;
    const cur = items.findIndex((el) => el.classList.contains("active"));
    const dir = (e.key === "ArrowDown" || e.key === "j") ? 1 : -1;
    const next = Math.max(0, Math.min(items.length - 1, cur + dir));
    if (items[next]) {
      items[next].click();
      e.preventDefault();
    }
    return;
  }

  // Tag selected step: t
  if (e.key === "t" && state.trace && state.stepIdx >= 0) {
    const detail = els.viewer.querySelector("[data-detail]");
    const tagBtn = detail?.querySelector('[data-action="tag"], [data-action="retag"]');
    if (tagBtn) { tagBtn.click(); e.preventDefault(); }
    return;
  }

  // Export markdown: e
  if (e.key === "e" && state.traceId) {
    exportCurrentTraceMarkdown();
    e.preventDefault();
    return;
  }

  // Focus search: /
  if (e.key === "/" && !e.metaKey && !e.ctrlKey) {
    els.search.focus();
    e.preventDefault();
    return;
  }

  // Help: ?
  if (e.key === "?" || (e.shiftKey && e.key === "/")) {
    openHelp();
    e.preventDefault();
  }
});

/* ----- live updates via SSE --------------------------------------------- */

let _eventSource = null;
function subscribeToEvents() {
  if (typeof EventSource === "undefined") return;
  try {
    _eventSource = new EventSource("/api/events");
  } catch (_) {
    return;
  }
  _eventSource.onopen = () => setLiveState("live");
  _eventSource.addEventListener("new_trace", async (e) => {
    let payload;
    try { payload = JSON.parse(e.data); } catch { return; }
    state.traces = await (await fetch("/api/traces")).json();
    renderTraceList();
    loadStats();
    flashToast(`New trace captured (${payload.trace_id.slice(0, 8)})`);
  });
  _eventSource.addEventListener("annotation_changed", async (_e) => {
    // Refresh sidebar + active trace if it matches
    state.traces = await (await fetch("/api/traces")).json();
    renderTraceList();
    loadStats();
    if (state.traceId) {
      state.trace = await (await fetch(`/api/traces/${state.traceId}`)).json();
      // Don't yank the user's focus — only re-render if no tag form is open
      if (!els.viewer.querySelector(".tag-form")) {
        renderTrace();
        if (state.stepIdx >= 0) selectStep(state.stepIdx);
      }
    }
  });
  _eventSource.onerror = () => {
    // Browser auto-reconnects with backoff; reflect it in the topbar so the
    // user can tell whether they're looking at a frozen snapshot.
    setLiveState("stale");
  };
}

// Wire up copy-on-click for the onboarding commands. Delegated so the
// listener works even if we re-render the empty state.
document.addEventListener("click", (e) => {
  const cmd = e.target.closest?.("[data-copy]");
  if (!cmd) return;
  navigator.clipboard?.writeText(cmd.dataset.copy).then(() => {
    cmd.classList.add("copied");
    setTimeout(() => cmd.classList.remove("copied"), 1400);
  });
});

setLiveState("connecting");
loadTraces();
subscribeToEvents();

/* =========================================================================
   First-visit guided tour
   =========================================================================
   Shown ONCE per browser (gated by localStorage["loupe.tour.seen"]).
   Re-triggerable via the "Tour" button in the sidebar footer.
   ========================================================================= */

const TOUR_STEPS = [
  {
    target: ".brand",
    title: "Welcome to Loupe",
    body: "This is your local forensic dashboard for AI agents. Every captured agent run lives in a single JSONL file under ~/.loupe/traces — and shows up here.",
    placement: "below",
  },
  {
    target: "#trace-list",
    title: "Case files",
    body: "Every captured run is a case file in this sidebar. The pulse on the right marks live updates streaming in.",
    placement: "right",
  },
  {
    target: "#filter-bar",
    title: "Filter chips",
    body: "Cut to what matters: only failed runs, only tagged runs, or everything. Combine with the search box above for step-content match.",
    placement: "right",
  },
  {
    target: "#viewer",
    title: "Evidence pane",
    body: "Click a case file to see its timeline, the step-by-step trail, every input / output, and — when you've run `loupe attribute` — the SAE circuit features that fired during the LLM call.",
    placement: "left",
  },
  {
    target: "#tour-button",
    title: "Need this again?",
    body: "Tap the Tour button down here any time to replay. Press ? for the keyboard-shortcut cheat sheet.",
    placement: "above",
  },
];

const TOUR_KEY = "loupe.tour.seen";
const tour = {
  index: 0,
  active: false,
  els: {
    overlay: document.getElementById("tour"),
    spot: document.getElementById("tour-spot"),
    title: document.getElementById("tour-title"),
    body: document.getElementById("tour-body"),
    progress: document.getElementById("tour-progress"),
    stepLabel: document.getElementById("tour-step-label"),
    next: document.getElementById("tour-next"),
    back: document.getElementById("tour-back"),
    skip: document.getElementById("tour-skip"),
    card: document.querySelector(".tour-card"),
    trigger: document.getElementById("tour-button"),
  },
};

function startTour(opts = {}) {
  tour.active = true;
  tour.index = 0;
  tour.els.overlay.hidden = false;
  // Reflow then flip the attribute so the CSS transition fires.
  requestAnimationFrame(() => tour.els.overlay.setAttribute("aria-hidden", "false"));
  // Build the progress dots once per session.
  tour.els.progress.innerHTML = TOUR_STEPS.map(() => "<span></span>").join("");
  renderTourStep();
  if (!opts.silent) localStorage.setItem(TOUR_KEY, "1");
}

function endTour() {
  tour.active = false;
  tour.els.overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { tour.els.overlay.hidden = true; }, 200);
  localStorage.setItem(TOUR_KEY, "1");
}

function renderTourStep() {
  const step = TOUR_STEPS[tour.index];
  const target = document.querySelector(step.target);
  if (!target) {
    // Skip steps whose anchor isn't on the page (e.g. trace-list empty).
    if (tour.index < TOUR_STEPS.length - 1) {
      tour.index += 1;
      renderTourStep();
      return;
    }
    endTour();
    return;
  }
  // Place the spotlight over the target.
  const rect = target.getBoundingClientRect();
  const PAD = 6;
  Object.assign(tour.els.spot.style, {
    top: `${rect.top - PAD}px`,
    left: `${rect.left - PAD}px`,
    width: `${rect.width + 2 * PAD}px`,
    height: `${rect.height + 2 * PAD}px`,
  });
  // Place the card adjacent to the spot.
  const cardW = 360;
  const cardH = 220;
  const gap = 16;
  let top = rect.bottom + gap;
  let left = rect.left;
  switch (step.placement) {
    case "above":
      top = rect.top - cardH - gap;
      left = Math.max(16, rect.left);
      break;
    case "right":
      top = rect.top;
      left = rect.right + gap;
      break;
    case "left":
      top = rect.top;
      left = Math.max(16, rect.left - cardW - gap);
      break;
    default: /* below */
      top = rect.bottom + gap;
      left = Math.max(16, rect.left);
  }
  // Keep within viewport
  top = Math.min(top, window.innerHeight - cardH - 16);
  top = Math.max(top, 16);
  left = Math.min(left, window.innerWidth - cardW - 16);
  left = Math.max(left, 16);
  Object.assign(tour.els.card.style, {
    top: `${top}px`,
    left: `${left}px`,
  });
  // Content
  tour.els.title.textContent = step.title;
  tour.els.body.textContent = step.body;
  tour.els.stepLabel.textContent = `Step ${tour.index + 1} of ${TOUR_STEPS.length}`;
  tour.els.back.hidden = tour.index === 0;
  tour.els.next.textContent =
    tour.index === TOUR_STEPS.length - 1 ? "Done" : "Next →";
  // Progress dots
  [...tour.els.progress.children].forEach((dot, i) => {
    dot.classList.toggle("is-done",  i <  tour.index);
    dot.classList.toggle("is-active", i === tour.index);
  });
}

tour.els.next.addEventListener("click", () => {
  if (tour.index >= TOUR_STEPS.length - 1) {
    endTour();
    return;
  }
  tour.index += 1;
  renderTourStep();
});
tour.els.back.addEventListener("click", () => {
  if (tour.index === 0) return;
  tour.index -= 1;
  renderTourStep();
});
tour.els.skip.addEventListener("click", endTour);
tour.els.trigger?.addEventListener("click", () => startTour());

window.addEventListener("resize", () => {
  if (tour.active) renderTourStep();
});

// Auto-launch on first visit, after the page has settled.
if (!localStorage.getItem(TOUR_KEY)) {
  setTimeout(() => startTour(), 600);
}

/* =========================================================================
   Inline "?" tooltips on technical terms
   =========================================================================
   Click a .term-help button to open a small popover with a one-paragraph
   plain-English explanation. Single popover at a time; click anywhere
   else to dismiss.
   ========================================================================= */

const TERM_EXPLANATIONS = {
  "circuit-attribution": {
    eyebrow: "Circuit attribution",
    body: "When you run `loupe attribute`, Loupe re-runs each LLM call through a small open model and projects the activations through a Sparse Autoencoder. The top features by activation magnitude are the interpretable concepts that fired most strongly for this turn.",
  },
  "sae-feature": {
    eyebrow: "SAE feature",
    body: "Sparse Autoencoder feature — a single dimension in a dictionary of human-interpretable concepts learned from a model's hidden states. Each feature corresponds to a specific concept (e.g. \"phrases about legal rulings\").",
  },
  "loupebench": {
    eyebrow: "LoupeBench",
    body: "The benchmark you build by tagging failing steps. Run `loupe export` to bundle every tagged failure into a single JSONL file you can publish, share, or run regression tests against.",
  },
  "tagged-step": {
    eyebrow: "Tagged step",
    body: "A step you've marked as a benchmark-worthy failure via Tag this step. Tagged steps participate in `loupe bench` (regression replay) and `loupe cluster` (feature analysis across failures).",
  },
};

let _activePopover = null;
function closeTermPopover() {
  if (_activePopover) {
    _activePopover.popover.remove();
    _activePopover.btn.setAttribute("aria-expanded", "false");
    _activePopover = null;
  }
}
function openTermPopover(btn, termKey) {
  closeTermPopover();
  const def = TERM_EXPLANATIONS[termKey];
  if (!def) return;
  const popover = document.createElement("div");
  popover.className = "term-help-popover";
  popover.innerHTML =
    `<span class="term-help-eyebrow">${escapeHtml(def.eyebrow)}</span>` +
    `${escapeHtml(def.body)}`;
  document.body.appendChild(popover);

  const rect = btn.getBoundingClientRect();
  const top = Math.min(rect.bottom + 6, window.innerHeight - 200);
  let left = rect.left - 8;
  left = Math.max(16, Math.min(left, window.innerWidth - 296));
  Object.assign(popover.style, { top: `${top}px`, left: `${left}px` });
  btn.setAttribute("aria-expanded", "true");
  _activePopover = { btn, popover };
}
document.addEventListener("click", (e) => {
  const helpBtn = e.target.closest?.(".term-help");
  if (helpBtn) {
    const k = helpBtn.dataset.term;
    if (_activePopover && _activePopover.btn === helpBtn) {
      closeTermPopover();
    } else {
      openTermPopover(helpBtn, k);
    }
    e.stopPropagation();
    return;
  }
  // Click anywhere else dismisses.
  closeTermPopover();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeTermPopover();
});
