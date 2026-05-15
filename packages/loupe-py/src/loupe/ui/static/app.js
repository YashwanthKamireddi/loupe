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
};

const state = {
  traceId: null,
  trace: null,
  stepIdx: -1,
  traces: [],
  filter: "",
  statusFilter: "all", // 'all' | 'failed' | 'tagged'
};

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

function flashToast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add("fade"); }, 1400);
  setTimeout(() => el.remove(), 2000);
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
  state.traces = await (await fetch("/api/traces")).json();
  renderTraceList();
  if (state.traces.length > 0 && !state.traceId) {
    openTrace(state.traces[0].trace_id);
  }
  loadStats();
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
      li.innerHTML = `<div style="line-height:1.55">No traces yet.<br><span style="color:var(--ink-4)">Try <code style="color:var(--amber)">loupe demo</code> to seed samples.</span></div>`;
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
        <span class="annot-eyebrow">Tagged for LoupeBench</span>
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
      await refresh();
    } catch (err) {
      submitBtn.disabled = false;
      submitBtn.textContent = "Save tag";
      console.error("save failed", err);
    }
  });
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  form.querySelector("[data-cat]").focus();
}

async function removeTag(step) {
  await fetch(`/api/traces/${state.traceId}/annotations/${step.step_id}`, { method: "DELETE" });
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

els.search.addEventListener("input", (e) => {
  state.filter = e.target.value || "";
  renderTraceList();
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
    // Browser auto-reconnects with backoff; nothing to do here.
  };
}

loadTraces();
subscribeToEvents();
