"use strict";

const els = {
  list: document.getElementById("trace-list"),
  count: document.getElementById("trace-count"),
  summary: document.getElementById("trace-summary"),
  stats: document.getElementById("stats"),
  search: document.getElementById("trace-search"),
  viewer: document.getElementById("viewer"),
  template: document.getElementById("trace-view-tmpl"),
  tagForm: document.getElementById("tag-form-tmpl"),
};

let activeTraceId = null;
let activeTrace = null; // full data including annotations
let activeStepIdx = -1;
let allTraces = [];
let filterText = "";

async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    const s = await r.json();
    els.stats.innerHTML = `
      <span class="stat"><span class="stat-k">traces</span><span class="stat-v">${s.trace_count}</span></span>
      <span class="stat"><span class="stat-k">failed</span><span class="stat-v">${s.failed_count}</span></span>
      <span class="stat"><span class="stat-k">steps</span><span class="stat-v">${s.step_count}</span></span>
      <span class="stat"><span class="stat-k">tagged</span><span class="stat-v">${s.annotation_count}</span></span>
    `;
  } catch (_) {
    /* no-op on first render races */
  }
}

async function loadTraces() {
  const res = await fetch("/api/traces");
  allTraces = await res.json();
  renderTraceList();
  if (allTraces.length > 0 && !activeTraceId) {
    openTrace(allTraces[0].trace_id);
  }
  loadStats();
}

function renderTraceList() {
  const filtered = allTraces.filter((t) => {
    if (!filterText) return true;
    const q = filterText.toLowerCase();
    return (
      (t.name || "").toLowerCase().includes(q) ||
      (t.framework || "").toLowerCase().includes(q) ||
      (t.trace_id || "").toLowerCase().includes(q)
    );
  });

  els.count.textContent = `${filtered.length} run${filtered.length === 1 ? "" : "s"}`;
  els.list.innerHTML = "";

  filtered.forEach((t) => {
    const li = document.createElement("li");
    li.dataset.id = t.trace_id;
    if (t.trace_id === activeTraceId) li.classList.add("active");
    const failed = t.metadata && t.metadata.failed;
    const tagPill =
      t.annotation_count > 0
        ? `<span class="t-tag-pill">${t.annotation_count} tag${t.annotation_count > 1 ? "s" : ""}</span>`
        : "";
    li.innerHTML = `
      <div class="t-name">${escapeHtml(t.name)}${tagPill}</div>
      <div class="t-meta">
        <span>${t.framework || "—"}</span>
        <span>${t.step_count} steps</span>
        <span>${durationOf(t)}</span>
      </div>
      <div class="t-status ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</div>
    `;
    li.addEventListener("click", () => openTrace(t.trace_id));
    els.list.appendChild(li);
  });
}

async function openTrace(traceId) {
  activeTraceId = traceId;
  activeStepIdx = -1;
  document.querySelectorAll(".trace-list li").forEach((li) => {
    li.classList.toggle("active", li.dataset.id === traceId);
  });

  const res = await fetch(`/api/traces/${traceId}`);
  activeTrace = await res.json();
  renderTrace();
}

function renderTrace() {
  const trace = activeTrace;
  if (!trace) return;
  const failed = trace.metadata && trace.metadata.failed;
  const annCount = (trace.annotations || []).length;
  els.summary.innerHTML = `
    <span>${escapeHtml(trace.name)}</span>
    <span class="pill">${trace.framework || "—"}</span>
    <span class="pill">${trace.steps.length} steps</span>
    <span class="pill">${durationOf(trace)}</span>
    ${annCount > 0 ? `<span class="pill" style="color:var(--amber); border-color:rgba(224,164,88,.4)">${annCount} tagged</span>` : ""}
    <span class="pill ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</span>
  `;

  const node = els.template.content.cloneNode(true);
  els.viewer.innerHTML = "";
  els.viewer.appendChild(node);

  const timeline = els.viewer.querySelector("[data-track]");
  const stepList = els.viewer.querySelector("[data-steps]");

  const annByStep = new Map();
  (trace.annotations || []).forEach((a) => annByStep.set(a.step_id, a));

  trace.steps.forEach((step, idx) => {
    const tagged = annByStep.has(step.step_id);
    const tlEl = document.createElement("div");
    tlEl.className = "tl-step";
    if (step.error) tlEl.classList.add("failed");
    if (tagged) tlEl.classList.add("tagged");
    tlEl.innerHTML = `
      <span class="tl-kind">${step.kind}</span>
      <span class="tl-name">${escapeHtml(step.name)}</span>
    `;
    tlEl.addEventListener("click", () => selectStep(idx));
    timeline.appendChild(tlEl);

    const liEl = document.createElement("li");
    liEl.className = `kind-${step.kind}`;
    if (step.error) liEl.classList.add("failed");
    liEl.innerHTML = `
      <span class="kind-chip">${step.kind}</span>
      <span class="step-name">${escapeHtml(step.name)}</span>
      ${tagged ? '<span class="tag-dot" title="tagged">◉</span>' : ""}
    `;
    liEl.addEventListener("click", () => selectStep(idx));
    stepList.appendChild(liEl);
  });

  if (trace.steps.length > 0) {
    const firstFailing = trace.steps.findIndex((s) => s.error);
    selectStep(firstFailing >= 0 ? firstFailing : 0);
  }
}

function selectStep(idx) {
  if (!activeTrace || !activeTrace.steps[idx]) return;
  activeStepIdx = idx;
  const step = activeTrace.steps[idx];
  els.viewer.querySelectorAll(".tl-step").forEach((el, i) =>
    el.classList.toggle("active", i === idx)
  );
  els.viewer.querySelectorAll(".step-list li").forEach((el, i) =>
    el.classList.toggle("active", i === idx)
  );

  const detail = els.viewer.querySelector("[data-detail]");
  const ann = (activeTrace.annotations || []).find((a) => a.step_id === step.step_id);
  detail.innerHTML = renderDetail(step, ann);
  attachDetailHandlers(detail, step, ann);
}

function renderDetail(step, ann) {
  const dur =
    step.ended_at && step.started_at
      ? `${((step.ended_at - step.started_at) * 1000).toFixed(1)} ms`
      : "—";
  const parts = [
    `<h2>${escapeHtml(step.name)}</h2>`,
    `<div class="kv">
      <div class="k">kind</div><div class="v">${step.kind}</div>
      <div class="k">duration</div><div class="v">${dur}</div>
      <div class="k">step id</div><div class="v">${step.step_id}</div>
    </div>`,
  ];

  if (step.error) {
    parts.push(`<div class="error-banner">${escapeHtml(step.error)}</div>`);
  }

  if (ann) {
    parts.push(`
      <div class="annot-card">
        <div class="annot-title">tagged for loupebench</div>
        <span class="annot-cat">${escapeHtml(ann.failure_category)}</span>
        <span class="annot-sev">${escapeHtml(ann.severity)}</span>
        ${ann.notes ? `<div style="margin-top:8px; color:var(--ink-mid)">${escapeHtml(ann.notes)}</div>` : ""}
        ${ann.mitigation ? `<div style="margin-top:6px; color:var(--ink-dim); font-size:11.5px">mitigation — ${escapeHtml(ann.mitigation)}</div>` : ""}
        <div class="action-row">
          <button type="button" class="btn-ghost" data-action="retag">edit</button>
          <button type="button" class="btn-ghost" data-action="untag">remove tag</button>
        </div>
      </div>
    `);
  } else if (step.error) {
    parts.push(`<div class="action-row">
      <button type="button" class="btn-primary" data-action="tag">tag this failure</button>
    </div>`);
  } else {
    parts.push(`<div class="action-row">
      <button type="button" class="btn-ghost" data-action="tag">tag for loupebench</button>
    </div>`);
  }

  if (step.inputs && Object.keys(step.inputs).length > 0) {
    parts.push(`<div class="section-h">inputs</div>`);
    parts.push(`<pre>${escapeHtml(prettyJson(step.inputs))}</pre>`);
  }
  if (step.outputs && Object.keys(step.outputs).length > 0) {
    parts.push(`<div class="section-h">outputs</div>`);
    parts.push(`<pre>${escapeHtml(prettyJson(step.outputs))}</pre>`);
  }
  if (step.metadata && Object.keys(step.metadata).length > 0) {
    parts.push(`<div class="section-h">metadata</div>`);
    parts.push(`<pre>${escapeHtml(prettyJson(step.metadata))}</pre>`);
  }

  return parts.join("");
}

function attachDetailHandlers(detail, step, ann) {
  detail.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const a = btn.dataset.action;
      if (a === "tag" || a === "retag") openTagForm(step, ann);
      else if (a === "untag") removeTag(step);
    });
  });
}

function openTagForm(step, ann) {
  const detail = els.viewer.querySelector("[data-detail]");
  // Remove any existing form
  detail.querySelectorAll(".tag-form").forEach((el) => el.remove());

  const node = els.tagForm.content.cloneNode(true);
  detail.appendChild(node);
  const form = detail.querySelector("[data-tag-form]");
  if (ann) {
    form.querySelector("[data-cat]").value = ann.failure_category;
    form.querySelector("[data-sev]").value = ann.severity;
    form.querySelector("[data-notes]").value = ann.notes || "";
    form.querySelector("[data-mit]").value = ann.mitigation || "";
  }
  form.querySelector("[data-cancel]").addEventListener("click", () => form.remove());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    await fetch(`/api/traces/${activeTraceId}/annotations`, {
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
    await refresh();
  });
  form.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function removeTag(step) {
  await fetch(`/api/traces/${activeTraceId}/annotations/${step.step_id}`, {
    method: "DELETE",
  });
  await refresh();
}

async function refresh() {
  const idx = activeStepIdx;
  const res = await fetch(`/api/traces/${activeTraceId}`);
  activeTrace = await res.json();
  renderTrace();
  if (idx >= 0) selectStep(idx);
  loadStats();
  // Also refresh sidebar annotation counts
  const res2 = await fetch("/api/traces");
  allTraces = await res2.json();
  renderTraceList();
}

/* ----- helpers ----- */

function durationOf(t) {
  if (t.ended_at && t.started_at) {
    return `${Math.max(0, (t.ended_at - t.started_at) * 1000).toFixed(0)} ms`;
  }
  return "—";
}
function prettyJson(v) {
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}
function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/* ----- input handlers ----- */
els.search.addEventListener("input", (e) => {
  filterText = e.target.value || "";
  renderTraceList();
});

document.addEventListener("keydown", (e) => {
  if (document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    return;
  }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const items = Array.from(document.querySelectorAll(".step-list li"));
  if (!items.length) return;
  const activeIdx = items.findIndex((el) => el.classList.contains("active"));
  const next =
    e.key === "ArrowDown"
      ? Math.min(items.length - 1, activeIdx + 1)
      : Math.max(0, activeIdx - 1);
  if (items[next]) {
    items[next].click();
    e.preventDefault();
  }
});

loadTraces();
