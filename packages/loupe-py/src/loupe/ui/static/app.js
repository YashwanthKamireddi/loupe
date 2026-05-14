"use strict";

const els = {
  list: document.getElementById("trace-list"),
  count: document.getElementById("trace-count"),
  summary: document.getElementById("trace-summary"),
  viewer: document.getElementById("viewer"),
  template: document.getElementById("trace-view-tmpl"),
};

let activeTraceId = null;
let activeStepId = null;

async function loadTraces() {
  const res = await fetch("/api/traces");
  const traces = await res.json();
  els.count.textContent = `${traces.length} run${traces.length === 1 ? "" : "s"}`;
  els.list.innerHTML = "";
  traces.forEach((t) => {
    const li = document.createElement("li");
    li.dataset.id = t.trace_id;
    const failed = t.metadata && t.metadata.failed;
    li.innerHTML = `
      <div class="t-name">${escapeHtml(t.name)}</div>
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
  if (traces.length > 0 && !activeTraceId) {
    openTrace(traces[0].trace_id);
  }
}

async function openTrace(traceId) {
  activeTraceId = traceId;
  activeStepId = null;
  document.querySelectorAll(".trace-list li").forEach((li) => {
    li.classList.toggle("active", li.dataset.id === traceId);
  });

  const res = await fetch(`/api/traces/${traceId}`);
  const trace = await res.json();

  const failed = trace.metadata && trace.metadata.failed;
  els.summary.innerHTML = `
    <span>${escapeHtml(trace.name)}</span>
    <span class="pill">${trace.framework || "—"}</span>
    <span class="pill">${trace.steps.length} steps</span>
    <span class="pill">${durationOf(trace)}</span>
    <span class="pill ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</span>
  `;

  const node = els.template.content.cloneNode(true);
  els.viewer.innerHTML = "";
  els.viewer.appendChild(node);

  const timeline = els.viewer.querySelector("[data-track]");
  const stepList = els.viewer.querySelector("[data-steps]");
  const detail = els.viewer.querySelector("[data-detail]");

  trace.steps.forEach((step, idx) => {
    const tlEl = document.createElement("div");
    tlEl.className = "tl-step";
    if (step.error) tlEl.classList.add("failed");
    tlEl.innerHTML = `
      <span class="tl-kind">${step.kind}</span>
      <span class="tl-name">${escapeHtml(step.name)}</span>
    `;
    tlEl.addEventListener("click", () => selectStep(step, idx));
    timeline.appendChild(tlEl);

    const liEl = document.createElement("li");
    liEl.className = `kind-${step.kind}`;
    if (step.error) liEl.classList.add("failed");
    liEl.innerHTML = `
      <span class="kind-chip">${step.kind}</span>
      <span class="step-name">${escapeHtml(step.name)}</span>
    `;
    liEl.addEventListener("click", () => selectStep(step, idx));
    stepList.appendChild(liEl);
  });

  function selectStep(step, idx) {
    activeStepId = step.step_id;
    timeline.querySelectorAll(".tl-step").forEach((el, i) =>
      el.classList.toggle("active", i === idx)
    );
    stepList.querySelectorAll("li").forEach((el, i) =>
      el.classList.toggle("active", i === idx)
    );
    detail.innerHTML = renderDetail(step);
  }

  if (trace.steps.length > 0) {
    const firstFailing = trace.steps.findIndex((s) => s.error);
    selectStep(
      trace.steps[firstFailing >= 0 ? firstFailing : 0],
      firstFailing >= 0 ? firstFailing : 0
    );
  } else {
    detail.innerHTML = '<p class="dim">No steps captured.</p>';
  }
}

function renderDetail(step) {
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

function durationOf(t) {
  if (t.ended_at && t.started_at) {
    return `${Math.max(0, (t.ended_at - t.started_at) * 1000).toFixed(0)} ms`;
  }
  return "—";
}

function prettyJson(v) {
  try {
    return JSON.stringify(v, null, 2);
  } catch (_) {
    return String(v);
  }
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

document.addEventListener("keydown", (e) => {
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
