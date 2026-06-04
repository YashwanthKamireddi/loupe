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
  liveIndicator: document.getElementById("live-indicator"),
  emptyOnboard: document.getElementById("empty-onboard"),
  costSparkline: document.getElementById("cost-sparkline"),
  costSparklineSvg: document.getElementById("cost-sparkline-svg"),
  costSparklineTotal: document.getElementById("cost-sparkline-total"),
};

const state = {
  traceId: null,
  trace: null,
  stepIdx: -1,
  traces: [],
  filter: "",
  statusFilter: "all", // 'all' | 'failed' | 'tagged'
  initialLoad: true,
  // v0.0.59 — multi-select bulk operations
  selectedIds: new Set(),
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
  loadCostSparkline();
}

async function loadCostSparkline() {
  if (!els.costSparkline || !els.costSparklineSvg) return;
  try {
    const res = await fetch("/api/cost-timeseries?days=14");
    if (!res.ok) return;
    const body = await res.json();
    renderCostSparkline(body.days || []);
  } catch (_) { /* never break the dashboard for a chart */ }
}

function renderCostSparkline(days) {
  if (!els.costSparkline || !els.costSparklineSvg) return;
  const total = days.reduce((s, d) => s + (d.usd || 0), 0);
  const totalCalls = days.reduce((s, d) => s + (d.calls || 0), 0);
  // Hide the widget until at least one call is recorded — empty bars look
  // like a broken UI more than they look informative.
  if (totalCalls === 0) {
    els.costSparkline.hidden = true;
    return;
  }
  els.costSparkline.hidden = false;
  els.costSparklineTotal.textContent = formatUsd(total);

  // Draw bars on a 240×36 viewBox so it scales to whatever sidebar
  // width happens to be. Each bar gets equal width with a 1px gap.
  const w = 240, h = 36;
  const gap = 1;
  const n = days.length;
  const barW = (w - gap * (n - 1)) / n;
  const max = days.reduce((m, d) => Math.max(m, d.usd || 0), 0) || 1;
  const parts = [];
  days.forEach((d, i) => {
    const x = i * (barW + gap);
    const usd = d.usd || 0;
    const bh = Math.max(1, (usd / max) * h);
    const y = h - bh;
    const rl = d.rate_limited > 0;
    // Fill is set via CSS class, NOT a fill="var(--…)" attribute —
    // var() does not resolve inside SVG presentation attributes, which
    // is why bars used to render as a broken near-white block.
    const cls = rl ? "spark-bar-rl" : (usd > 0 ? "spark-bar" : "spark-bar-empty");
    parts.push(
      `<rect class="${cls}" x="${x.toFixed(2)}" y="${y.toFixed(2)}"`
      + ` width="${barW.toFixed(2)}" height="${bh.toFixed(2)}">`
      + `<title>${d.date} · ${formatUsd(usd)} · ${d.calls} call${d.calls === 1 ? "" : "s"}`
      + (rl ? ` · ${d.rate_limited} rate-limited` : "")
      + `</title></rect>`
    );
  });
  els.costSparklineSvg.innerHTML = parts.join("");
}

function formatUsd(value) {
  if (!isFinite(value) || value === 0) return "$0.00";
  if (value < 0.01) return "<$0.01";
  if (value < 1) return `$${value.toFixed(3)}`;
  if (value < 100) return `$${value.toFixed(2)}`;
  return `$${Math.round(value)}`;
}

function updateEmptyState() {
  // Tighten the copy when there genuinely aren't any traces yet vs when
  // there are some but nothing's selected. Also reveal the onboarding
  // walkthrough only in the truly-empty case.
  const sub = document.getElementById("empty-state-sub");
  if (sub) {
    if (state.traces.length === 0) {
      sub.textContent =
        "No captured runs yet. Below is the fastest way to put one here — plus what you'll see when it lands.";
    } else {
      sub.textContent = "Select a case file from the left.";
    }
  }
  if (els.emptyOnboard) {
    els.emptyOnboard.hidden = state.traces.length !== 0;
  }
  // First-trace one-shot teaching hint. Shows on the user's very FIRST
  // captured trace (exactly 1 trace) and only if they haven't dismissed
  // it before. Self-dismisses after 10s of dwell time OR on close click.
  maybeShowFirstTraceHint();
}

let _firstTraceHintTimer = null;
let _firstTraceHintShown = false;  // session-level latch — never re-show after one display
function maybeShowFirstTraceHint() {
  const hint = document.getElementById("first-trace-hint");
  if (!hint) return;
  // Two latches: (1) once shown this session, never re-show even if the
  // trace list refreshes back to length === 1; (2) localStorage so a
  // returning visitor never sees it again across reloads.
  const seen = _firstTraceHintShown
    || localStorage.getItem("loupe.first_trace_seen") === "1";
  if (seen || state.traces.length !== 1) {
    hint.hidden = true;
    return;
  }
  // Clear any pending timer before we set a new one — prevents stacking
  // multiple concurrent dismisses if updateEmptyState fires repeatedly
  // while a trace is loading.
  if (_firstTraceHintTimer) {
    clearTimeout(_firstTraceHintTimer);
    _firstTraceHintTimer = null;
  }
  _firstTraceHintShown = true;
  hint.hidden = false;

  const dismiss = () => {
    hint.hidden = true;
    localStorage.setItem("loupe.first_trace_seen", "1");
    if (_firstTraceHintTimer) {
      clearTimeout(_firstTraceHintTimer);
      _firstTraceHintTimer = null;
    }
  };
  const closeBtn = document.getElementById("first-trace-close");
  if (closeBtn) closeBtn.onclick = dismiss;
  _firstTraceHintTimer = setTimeout(dismiss, 10_000);
}

// Once at boot: ask the server which shell the user runs and rewrite the
// onboarding `export …` snippet to the right syntax. fish -> `set -Ux`,
// PowerShell -> `$env:NAME='…'`, cmd -> `set NAME=…`, otherwise leave the
// bash/zsh `export` default in place.
(async function adaptOnboardingShellSnippet() {
  try {
    const res = await fetch("/api/onboarding");
    if (!res.ok) return;
    const { example } = await res.json();
    if (!example) return;
    const el = document.getElementById("empty-env-cmd");
    if (!el) return;
    el.textContent = example;
    el.setAttribute("data-copy", example);
  } catch (_) {
    // never block the dashboard on a UX nicety
  }
})();

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
    if (state.selectedIds.has(t.trace_id)) li.classList.add("selected");
    const failed = t.metadata?.failed;
    const tagPill = t.annotation_count > 0
      ? `<span class="t-tag-pill">${t.annotation_count} tag${t.annotation_count > 1 ? "s" : ""}</span>`
      : "";
    const dur = formatDuration(traceDurationMs(t));
    const rel = formatRelative(t.started_at);
    const checked = state.selectedIds.has(t.trace_id) ? "checked" : "";
    li.innerHTML = `
      <input type="checkbox" class="t-select" data-id="${t.trace_id}" ${checked}
             aria-label="Select trace ${escapeHtml(t.name)}" tabindex="-1">
      <div class="t-body">
        <div class="t-name">${escapeHtml(t.name)}${tagPill}</div>
        <div class="t-meta">
          <span>${escapeHtml(t.framework || "—")}</span>
          <span>${t.step_count} ${t.step_count === 1 ? "step" : "steps"}</span>
          <span>${dur}</span>
          <span>${rel}</span>
        </div>
      </div>
      <div class="t-status ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</div>
    `;
    const checkbox = li.querySelector(".t-select");
    checkbox.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleSelection(t.trace_id);
    });
    li.addEventListener("click", (e) => {
      // Clicking the checkbox is already handled; clicking the row body
      // opens the trace as before.
      if (e.target.closest(".t-select")) return;
      openTrace(t.trace_id);
    });
    els.list.appendChild(li);
  });
  renderBulkActionBar();
}


/* ----- bulk selection ---------------------------------------------------- */

function toggleSelection(traceId) {
  if (state.selectedIds.has(traceId)) state.selectedIds.delete(traceId);
  else state.selectedIds.add(traceId);
  renderTraceList();
}

function clearSelection() {
  state.selectedIds.clear();
  renderTraceList();
}

function renderBulkActionBar() {
  let bar = document.getElementById("bulk-bar");
  const n = state.selectedIds.size;
  if (n === 0) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "bulk-bar";
    bar.className = "bulk-bar";
    document.body.appendChild(bar);
  }
  const label = `${n} selected`;
  // G3: "Diff" only makes sense for 2+ traces. Hide the button when n=1.
  const diffBtn = n >= 2
    ? `<button type="button" class="bulk-btn" id="bulk-diff">Diff</button>`
    : "";
  bar.innerHTML = `
    <span class="bulk-count">${label}</span>
    ${diffBtn}
    <button type="button" class="bulk-btn danger" id="bulk-delete">Delete</button>
    <button type="button" class="bulk-btn" id="bulk-clear">Clear</button>
  `;
  bar.querySelector("#bulk-clear").addEventListener("click", clearSelection);
  bar.querySelector("#bulk-delete").addEventListener("click", bulkDelete);
  bar.querySelector("#bulk-diff")?.addEventListener("click", openMultiDiff);
}

/* G3 — Side-by-side diff for 2+ selected traces.
   Opens a full-viewer overlay with one column per trace. Each column
   shows trace name + step list. Common steps (same kind+name in same
   position) are visually aligned. Different steps are highlighted. */
async function openMultiDiff() {
  const ids = Array.from(state.selectedIds);
  if (ids.length < 2) return;
  if (ids.length > 4) {
    alert("Diff supports up to 4 traces at a time. Reduce the selection.");
    return;
  }

  // Fetch every selected trace in parallel.
  let traces;
  try {
    traces = await Promise.all(
      ids.map((id) => fetch(`/api/traces/${id}`).then((r) => {
        if (!r.ok) throw new Error(`trace ${id.slice(0, 8)} not found`);
        return r.json();
      })),
    );
  } catch (err) {
    alert(`Could not load traces for diff: ${err.message}`);
    return;
  }

  renderMultiDiff(traces);
}

function renderMultiDiff(traces) {
  // Pin the diff to the viewer panel so the user can still see the
  // sidebar for picking new traces.
  const cols = traces.length;
  const maxSteps = Math.max(...traces.map((t) => t.steps.length));

  // Compute per-row alignment status: if every trace has a step at
  // this index with the SAME `kind` and `name`, mark it aligned;
  // otherwise highlight the column whose step differs.
  const rows = [];
  for (let i = 0; i < maxSteps; i++) {
    const stepsAtI = traces.map((t) => t.steps[i]);
    const sig = stepsAtI.map((s) => s ? `${s.kind}:${s.name}` : "");
    const allSame = sig.every((x) => x && x === sig[0]);
    rows.push({ index: i, stepsAtI, aligned: allSame });
  }

  const headerHtml = traces.map((t) => {
    const failed = t.metadata?.failed;
    return `
      <div class="diff-col-head">
        <div class="diff-col-name">${escapeHtml(t.name)}</div>
        <div class="diff-col-meta">
          <span>${escapeHtml(t.framework || "—")}</span>
          <span class="pill ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</span>
        </div>
        <div class="diff-col-id mono">${t.trace_id.slice(0, 12)}</div>
      </div>
    `;
  }).join("");

  const rowsHtml = rows.map((row) => {
    const cells = row.stepsAtI.map((s) => {
      if (!s) {
        return `<div class="diff-cell diff-cell-empty">—</div>`;
      }
      const kindClass = `diff-cell-${escapeHtml(s.kind || "custom")}`;
      const errBadge = s.error ? `<span class="diff-cell-err">×</span>` : "";
      const ms = stepDurationMs(s);
      return `
        <div class="diff-cell ${kindClass} ${row.aligned ? "" : "diff-cell-divergent"}">
          <div class="diff-cell-head">
            <span class="diff-cell-kind">${escapeHtml(s.kind)}</span>
            ${errBadge}
          </div>
          <div class="diff-cell-name">${escapeHtml(s.name)}</div>
          <div class="diff-cell-meta">${formatDuration(ms)}</div>
        </div>
      `;
    }).join("");
    return `
      <div class="diff-row" style="grid-template-columns: 40px repeat(${cols}, 1fr);">
        <div class="diff-row-num">${row.index + 1}</div>
        ${cells}
      </div>
    `;
  }).join("");

  els.viewer.innerHTML = `
    <div class="diff-view">
      <div class="diff-toolbar">
        <h2 class="diff-title">Diff · ${cols} traces</h2>
        <button type="button" class="btn-ghost" id="diff-close">Close ×</button>
      </div>
      <div class="diff-cols-head" style="grid-template-columns: 40px repeat(${cols}, 1fr);">
        <div></div>
        ${headerHtml}
      </div>
      <div class="diff-rows">${rowsHtml || '<div class="empty-state">No steps to compare.</div>'}</div>
    </div>
  `;
  document.getElementById("diff-close")?.addEventListener("click", () => {
    state.traceId = null;
    state.trace = null;
    renderEmptyViewer();
  });
}

function renderEmptyViewer() {
  // Restore the empty-state placeholder we shipped in index.html.
  els.viewer.innerHTML = `
    <div class="empty-state">
      <div class="empty-mark">◉</div>
      <h1 class="empty-title">Inspect agent runs.</h1>
      <p class="empty-sub" id="empty-state-sub">Pick a trace from the sidebar.</p>
    </div>
  `;
}

async function bulkDelete() {
  const ids = Array.from(state.selectedIds);
  if (ids.length === 0) return;
  const word = ids.length === 1 ? "trace" : "traces";
  if (!confirm(`Delete ${ids.length} ${word}? This cannot be undone.`)) return;
  try {
    const res = await fetch("/api/traces/bulk-delete", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ trace_ids: ids }),
    });
    if (!res.ok) {
      const detail = await res.text();
      alert(`Delete failed: ${res.status} ${detail.slice(0, 200)}`);
      return;
    }
    const body = await res.json();
    const deletedSet = new Set(body.deleted || []);
    // Filter out deleted (matched on prefix); the server returns full ids.
    state.traces = state.traces.filter((t) => {
      // Match by exact id OR by prefix that matches what user selected.
      if (deletedSet.has(t.trace_id)) return false;
      for (const sel of ids) {
        if (t.trace_id.startsWith(sel) && deletedSet.has(t.trace_id)) return false;
      }
      return true;
    });
    if (state.traceId && deletedSet.has(state.traceId)) {
      state.traceId = null;
      state.trace = null;
      if (els.viewer) els.viewer.innerHTML = "";
      if (els.summary) els.summary.innerHTML = "";
    }
    state.selectedIds.clear();
    renderTraceList();
  } catch (err) {
    alert(`Delete failed: ${err}`);
  }
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
  // G6: keep the URL in sync so a deep-link `#trace-<id>` survives reload.
  try {
    const hash = `#trace-${traceId}`;
    if (location.hash !== hash) {
      history.replaceState(null, "", hash);
    }
  } catch (_) { /* noop */ }
}

/* G6 — Resolve any deep-link on first paint. URL fragments we honour:
     #trace-<full-or-prefix>             open that trace
     #trace-<id>/step-<step-id>          open that trace, then that step
*/
function resolveDeepLink() {
  const hash = location.hash.replace(/^#/, "");
  if (!hash) return;
  const m = hash.match(/^trace-([^/]+)(?:\/step-(.+))?$/);
  if (!m) return;
  const [, tracePrefix, stepId] = m;
  const trace = state.traces.find(
    (t) => t.trace_id === tracePrefix || t.trace_id.startsWith(tracePrefix),
  );
  if (!trace) return;
  openTrace(trace.trace_id).then(() => {
    if (!stepId) return;
    const steps = Array.from(document.querySelectorAll(".step-list li"));
    const target = steps.find(
      (el) => el.dataset.stepId === stepId
            || el.dataset.stepId?.startsWith(stepId),
    );
    target?.click();
  });
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
    liEl.dataset.stepId = step.step_id;
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

  // G2: render messages (and the model's reply) as a chat thread when
  // we have the data. The raw JSON view is still shown below for power
  // users — collapsed by default so it doesn't fight the bubbles.
  const messages = step.inputs?.messages;
  const replyText = step.outputs?.text;
  const renderedAsChat = renderConversation(out, step.inputs, replyText, messages);

  if (step.inputs && Object.keys(step.inputs).length > 0) {
    out.push(`
      <details class="json-fold" ${renderedAsChat ? "" : "open"}>
        <summary class="section-h" title="The data your code passed to the model — prompts, messages, tool arguments, model name.">inputs (raw JSON)</summary>
        <pre>${escapeHtml(prettyJson(step.inputs))}</pre>
      </details>
    `);
  }
  if (step.outputs && Object.keys(step.outputs).length > 0) {
    out.push(`
      <details class="json-fold" ${renderedAsChat ? "" : "open"}>
        <summary class="section-h" title="What the model returned, plus token counts and any tool-call requests.">outputs (raw JSON)</summary>
        <pre>${escapeHtml(prettyJson(step.outputs))}</pre>
      </details>
    `);
  }
  if (step.metadata && Object.keys(step.metadata).length > 0) {
    out.push(`
      <details class="json-fold">
        <summary class="section-h" title="Loupe-added context: latency, HTTP status, framework, rate-limit signals.">metadata</summary>
        <pre>${escapeHtml(prettyJson(step.metadata))}</pre>
      </details>
    `);
  }

  return out.join("");
}

/* G2 — Conversation bubbles. Renders inputs.messages as a chat thread,
   with each role colored differently. The assistant reply (outputs.text)
   is appended as one more bubble below.

   Returns true iff we actually rendered bubbles, so the caller can decide
   whether to collapse the raw-JSON fallback. */
function renderConversation(out, inputs, replyText, messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    // Gemini-style alternative: inputs.contents = [{role, parts:[{text}]}]
    const contents = inputs?.contents;
    if (Array.isArray(contents) && contents.length > 0) {
      return renderGeminiContents(out, contents, replyText);
    }
    return false;
  }

  const bubbles = [];
  for (const m of messages) {
    if (!m || typeof m !== "object") continue;
    const role = (m.role || "user").toLowerCase();
    bubbles.push(renderBubble(role, m));
  }
  if (replyText) {
    bubbles.push(renderBubble("assistant",
      { role: "assistant", content: replyText, _is_reply: true }));
  }
  if (bubbles.length === 0) return false;

  out.push(`<div class="section-h">conversation</div>`);
  out.push(`<div class="convo">${bubbles.join("")}</div>`);
  return true;
}

function renderGeminiContents(out, contents, replyText) {
  const bubbles = [];
  for (const c of contents) {
    const role = (c.role || "user").toLowerCase();
    const parts = c.parts || [];
    const text = parts
      .filter((p) => typeof p?.text === "string")
      .map((p) => p.text)
      .join("\n");
    const hasMedia = parts.some(
      (p) => p?.inlineData?._loupe_media || p?.fileData,
    );
    bubbles.push(renderBubble(role, {
      role,
      content: text || (hasMedia ? "[image]" : "[empty]"),
    }));
  }
  if (replyText) {
    bubbles.push(renderBubble("model",
      { role: "model", content: replyText, _is_reply: true }));
  }
  if (bubbles.length === 0) return false;
  out.push(`<div class="section-h">conversation</div>`);
  out.push(`<div class="convo">${bubbles.join("")}</div>`);
  return true;
}

function renderBubble(role, m) {
  const norm = role === "model" ? "assistant"
             : role === "tool"  ? "tool"
             : role === "system" ? "system"
             : role === "user" ? "user"
             : "assistant";
  const text = bubbleText(m);
  const toolCalls = Array.isArray(m.tool_calls) ? m.tool_calls : null;
  const tcHtml = toolCalls
    ? `<div class="bubble-tools">${
        toolCalls.map((tc) => {
          const fn = tc.function || tc;
          const name = escapeHtml(fn.name || "?");
          const args = escapeHtml(
            typeof fn.arguments === "string"
              ? fn.arguments
              : prettyJson(fn.arguments || {}),
          );
          return `<div class="bubble-tool"><span class="bt-name">→ ${name}(</span><span class="bt-args">${args}</span><span class="bt-name">)</span></div>`;
        }).join("")
      }</div>`
    : "";
  const label = norm.charAt(0).toUpperCase() + norm.slice(1);
  const replyMark = m._is_reply ? '<span class="bubble-reply-mark"></span>' : "";
  return `
    <div class="bubble bubble-${norm}">
      <div class="bubble-role">${label}${replyMark}</div>
      <div class="bubble-body">${text ? escapeHtml(text) : ""}${tcHtml}</div>
    </div>
  `;
}

function bubbleText(m) {
  if (typeof m.content === "string") return m.content;
  if (Array.isArray(m.content)) {
    // Anthropic content blocks: text + tool_use + image(_loupe_media)
    const pieces = [];
    for (const block of m.content) {
      if (!block || typeof block !== "object") continue;
      if (block.type === "text" && typeof block.text === "string") {
        pieces.push(block.text);
      } else if (block.type === "tool_use") {
        pieces.push(`[tool_use ${block.name || "?"}]`);
      } else if (block.type === "tool_result") {
        const tr = typeof block.content === "string"
          ? block.content
          : prettyJson(block.content || {});
        pieces.push(`[tool_result] ${tr}`);
      } else if (block.type === "image" || block.type === "image_url") {
        const src = block.source || block.image_url || {};
        if (src._loupe_media) {
          pieces.push(`[image · ${src.media_type || "image"} · ${humanBytes(src.size_bytes)}]`);
        } else {
          pieces.push("[image]");
        }
      } else if (block.inlineData?._loupe_media) {
        pieces.push(`[media · ${block.inlineData.media_type} · ${humanBytes(block.inlineData.size_bytes)}]`);
      }
    }
    return pieces.join("\n");
  }
  return "";
}

function humanBytes(n) {
  if (typeof n !== "number" || !isFinite(n)) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
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

  // G6 — attach a copy-on-click button to every `<pre>` in the detail
  // panel. Hovers reveal the button; clicking copies the raw text.
  detail.querySelectorAll("pre").forEach((pre) => {
    if (pre.dataset.copyAttached) return;
    pre.dataset.copyAttached = "1";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pre-copy-btn";
    btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy this block");
    pre.appendChild(btn);
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyToClipboard(pre.textContent.replace(/Copy$/, "").trim(), btn);
    });
  });

}

function copyToClipboard(text, btn) {
  const setCopied = () => {
    const original = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = original; }, 1200);
  };
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(setCopied, () => fallbackCopy(text, setCopied));
  } else {
    fallbackCopy(text, setCopied);
  }
}

function fallbackCopy(text, ok) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); ok(); }
  catch (_) { /* clipboard simply unavailable */ }
  ta.remove();
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

// Status filter chips (skip the cluster button — it has no data-filter)
els.filterBar?.querySelectorAll(".filter-chip[data-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.statusFilter = btn.dataset.filter;
    els.filterBar.querySelectorAll(".filter-chip[data-filter]").forEach((b) => {
      b.classList.toggle("active", b === btn);
    });
    renderTraceList();
  });
});

// Cluster view: shared-feature analysis across tagged failures.
// Lives in the viewer pane (not the sidebar) since it's an N-trace
// aggregate, not a single case file.
async function openClusterView(category = "") {
  const viewer = document.getElementById("viewer");
  if (!viewer) return;
  viewer.innerHTML = `
    <article class="cluster-view">
      <header class="cluster-h">
        <h1 class="cluster-title">
          <span class="cluster-mark">◇</span> Cluster
          <span class="cluster-sub">shared-feature analysis across tagged failures</span>
        </h1>
        <div class="cluster-controls">
          <label class="cluster-cat-label">category
            <input id="cluster-cat" type="text" value="${escapeHtml(category)}"
                   placeholder="all" autocomplete="off" />
          </label>
          <button id="cluster-refresh" type="button" class="cluster-btn">refresh</button>
        </div>
      </header>
      <div id="cluster-body" class="cluster-body">
        <p class="cluster-loading">computing…</p>
      </div>
    </article>
  `;

  const refresh = async () => {
    const cat = (document.getElementById("cluster-cat")?.value || "").trim();
    const body = document.getElementById("cluster-body");
    if (body) body.innerHTML = '<p class="cluster-loading">computing…</p>';
    try {
      const q = cat ? `?category=${encodeURIComponent(cat)}&top_k=20` : `?top_k=20`;
      const res = await fetch(`/api/cluster${q}`);
      const data = await res.json();
      renderClusterBody(data);
    } catch (e) {
      if (body) body.innerHTML = `<p class="cluster-error">error: ${escapeHtml(String(e))}</p>`;
    }
  };

  document.getElementById("cluster-refresh")?.addEventListener("click", refresh);
  document.getElementById("cluster-cat")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") refresh();
  });
  await refresh();
}

function renderClusterBody(data) {
  const body = document.getElementById("cluster-body");
  if (!body) return;
  const cat = data.category || "(all categories)";
  if (data.in_category_count === 0) {
    body.innerHTML = `
      <p class="cluster-empty">
        No annotated steps in ${escapeHtml(cat)} with circuit attribution yet.
        Tag a failing step and run <code>loupe attribute &lt;trace&gt; --backend sae</code>
        to populate this view.
      </p>`;
    return;
  }
  const freqRows = (data.frequency || []).map((f) => `
    <tr>
      <td class="fid">#${f.feature_id}</td>
      <td class="hits">${f.hits}</td>
      <td class="share">${Math.round((f.share || 0) * 100)}%</td>
      <td class="expl">${escapeHtml(f.explanation || "")}</td>
    </tr>`).join("");
  const distRows = (data.distinctive || []).map((f) => `
    <tr>
      <td class="fid">#${f.feature_id}</td>
      <td class="hits">${f.hits_in}</td>
      <td class="hits-out">${f.hits_out}</td>
      <td class="score">+${(f.score || 0).toFixed(2)}</td>
      <td class="expl">${escapeHtml(f.explanation || "")}</td>
    </tr>`).join("");

  body.innerHTML = `
    <section class="cluster-section">
      <h2 class="cluster-h2">frequency
        <span class="cluster-meta">${data.in_category_count} annotation${data.in_category_count === 1 ? "" : "s"} · ${escapeHtml(cat)}</span>
      </h2>
      <table class="cluster-table">
        <thead><tr><th>feature</th><th>hits</th><th>share</th><th>explanation</th></tr></thead>
        <tbody>${freqRows}</tbody>
      </table>
    </section>
    ${distRows ? `
      <section class="cluster-section">
        <h2 class="cluster-h2">distinctive
          <span class="cluster-meta">vs ${data.out_category_count} other-category annotation${data.out_category_count === 1 ? "" : "s"} · smoothed log-ratio</span>
        </h2>
        <table class="cluster-table">
          <thead><tr><th>feature</th><th>in</th><th>out</th><th>score</th><th>explanation</th></tr></thead>
          <tbody>${distRows}</tbody>
        </table>
      </section>
    ` : ""}
  `;
}

document.getElementById("open-cluster-view")?.addEventListener("click", () => {
  document.getElementById("open-cluster-view")?.classList.add("active");
  document.querySelectorAll(".filter-chip[data-filter]").forEach((b) => b.classList.remove("active"));
  openClusterView();
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
loadTraces().then(resolveDeepLink);
subscribeToEvents();


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

/* =========================================================================
 * Opt-in dashboard tour (v0.0.78+).
 * Lives in the topbar 'tour' button. Coachmarks anchor to real UI
 * elements via getBoundingClientRect(); not modal — clicking outside or
 * pressing ESC dismisses. Designed to take ~30 seconds total.
 * ========================================================================= */
const TOUR_STEPS = [
  {
    sel: ".sidebar",
    title: "Case files",
    body: "Every captured agent run lands here. Click one to see its steps + evidence.",
  },
  {
    sel: "#filter-bar",
    title: "Filter + Cluster",
    body: "Filter by status. The ◇ Cluster chip on the right opens shared-feature analysis across all your tagged failures — the LoupeBench analytical primitive.",
  },
  {
    sel: ".viewer",
    title: "Evidence pane",
    body: "Click any step → full prompt, full reply, latency, tokens, errors, and (after `loupe attribute`) the top SAE features that fired.",
  },
  {
    sel: ".live-indicator",
    title: "Live capture",
    body: "Green dot = new traces stream in via SSE without a refresh. Pair with `loupe watch` in the terminal for a live in-terminal mirror.",
  },
  {
    sel: "#cost-sparkline",
    title: "14-day spend trend",
    body: "Captured cost + activity sparkline. Hidden until you've captured a few runs with known model prices.",
  },
];

let _tourIdx = 0;

function _positionCoachmark(target) {
  const cm = document.getElementById("tour-coachmark");
  const overlay = document.getElementById("tour-overlay");
  if (!cm || !overlay) return;
  if (!target) {
    cm.style.left = "50%";
    cm.style.top = "50%";
    cm.style.transform = "translate(-50%, -50%)";
    return;
  }
  const r = target.getBoundingClientRect();
  const pad = 12;
  const cmW = 340;
  const cmH = 160;
  // Prefer to the right of the target; fall back to below; then center.
  let left = r.right + pad;
  let top = r.top + (r.height / 2) - (cmH / 2);
  if (left + cmW > window.innerWidth - 16) {
    left = Math.max(16, r.left + (r.width / 2) - (cmW / 2));
    top  = r.bottom + pad;
  }
  if (top + cmH > window.innerHeight - 16) top = window.innerHeight - cmH - 16;
  if (top < 16) top = 16;
  cm.style.left = `${Math.max(16, left)}px`;
  cm.style.top = `${top}px`;
  cm.style.transform = "none";
}

function _renderTourStep() {
  const step = TOUR_STEPS[_tourIdx];
  if (!step) return _closeTour();
  document.getElementById("tour-title").textContent = step.title;
  document.getElementById("tour-body").textContent  = step.body;
  document.getElementById("tour-progress").textContent =
    `${_tourIdx + 1} / ${TOUR_STEPS.length}`;
  const nextBtn = document.getElementById("tour-next");
  nextBtn.textContent = _tourIdx === TOUR_STEPS.length - 1 ? "done" : "next →";
  // Highlight + position
  document.querySelectorAll(".tour-spotlight").forEach((el) =>
    el.classList.remove("tour-spotlight"),
  );
  const target = document.querySelector(step.sel);
  if (target) target.classList.add("tour-spotlight");
  _positionCoachmark(target);
}

function _openTour() {
  _tourIdx = 0;
  const overlay = document.getElementById("tour-overlay");
  if (!overlay) return;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  _renderTourStep();
}

function _closeTour() {
  const overlay = document.getElementById("tour-overlay");
  if (!overlay) return;
  overlay.hidden = true;
  overlay.setAttribute("aria-hidden", "true");
  document.querySelectorAll(".tour-spotlight").forEach((el) =>
    el.classList.remove("tour-spotlight"),
  );
}

document.getElementById("open-tour")?.addEventListener("click", _openTour);
document.getElementById("tour-next")?.addEventListener("click", () => {
  _tourIdx += 1;
  if (_tourIdx >= TOUR_STEPS.length) _closeTour();
  else _renderTourStep();
});
document.getElementById("tour-skip")?.addEventListener("click", _closeTour);
document.getElementById("tour-overlay")?.addEventListener("click", (e) => {
  if (e.target === e.currentTarget) _closeTour();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") _closeTour();
});
window.addEventListener("resize", () => {
  if (!document.getElementById("tour-overlay")?.hidden) {
    const step = TOUR_STEPS[_tourIdx];
    if (step) _positionCoachmark(document.querySelector(step.sel));
  }
});
