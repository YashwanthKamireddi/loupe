"""Single-file HTML render of a captured trace.

Produces a standalone .html file with the trace data inlined as JSON and a
tiny embedded viewer. No external assets, no fonts, no network calls — just
double-click to view. Designed for sharing a failure report over email or
Slack when running `loupe ui` isn't an option.

Mirror of `loupe.report.render_trace_markdown` but for the web.
"""

from __future__ import annotations

import html as _html
import json as _json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loupe.annotation import AnnotationStore


def render_trace_html(trace_path: Path) -> str:
    """Render a JSONL trace as a standalone single-file HTML document."""
    header, steps = _read_trace(trace_path)
    annotations = AnnotationStore().load(header["trace_id"])
    payload = {
        "header": header,
        "steps": steps,
        "annotations": [asdict(a) for a in annotations],
    }
    body = _SHELL.replace(
        "/*__LOUPE_DATA__*/null",
        _json.dumps(payload),
    ).replace(
        "<!-- TITLE -->",
        _html.escape(f"{header.get('name', 'trace')} · Loupe"),
    )
    return body


def _read_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            obj = _json.loads(line)
            kind = obj.pop("_type", None)
            if kind == "trace":
                header = obj
            elif kind == "step":
                steps.append(obj)
    return header, steps


_SHELL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title><!-- TITLE --></title>
<style>
  :root {
    --bg:#0c0b09; --bg-alt:#15130f; --surface:#1d1a14;
    --ink:#f5f0e3; --ink-2:#cabfa9; --ink-3:#8c8472;
    --amber:#e0a458; --red:#e25a47; --green:#7ea96b;
    --blue:#7cb4d4; --purple:#b193cb; --border:#2e2a22;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body {
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--bg); color: var(--ink);
    padding: 28px;
    -webkit-font-smoothing: antialiased;
  }
  header.case {
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px; margin-bottom: 20px;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  }
  .brand { color: var(--amber); font-size: 14px; letter-spacing: .2em; text-transform: uppercase; }
  h1 { font-size: 22px; font-weight: 600; color: var(--ink); }
  .meta { color: var(--ink-3); font-size: 12.5px; font-family: ui-monospace, "JetBrains Mono", monospace; }
  .pill {
    display: inline-block; padding: 2px 8px; border: 1px solid var(--border);
    border-radius: 999px; font-size: 11px; color: var(--ink-2);
  }
  .pill.failed { color: var(--red); border-color: rgba(226,90,71,.4); }
  .pill.ok { color: var(--green); border-color: rgba(126,169,107,.4); }
  .pill.amber { color: var(--amber); border-color: rgba(224,164,88,.4); }
  .annot {
    background: rgba(224,164,88,.08); border: 1px solid rgba(224,164,88,.3);
    border-left-width: 3px; padding: 12px 16px; border-radius: 4px; margin: 16px 0;
  }
  .annot-cat {
    display: inline-block; padding: 2px 10px; background: rgba(224,164,88,.18);
    border: 1px solid rgba(224,164,88,.4); border-radius: 999px;
    color: var(--amber); font-size: 12px; font-weight: 500;
  }
  .err {
    background: rgba(226,90,71,.08); border: 1px solid rgba(226,90,71,.35);
    border-left-width: 3px; padding: 10px 14px; border-radius: 4px;
    color: var(--red); font-family: ui-monospace, monospace; font-size: 12.5px;
    margin: 8px 0; word-break: break-word;
  }
  h2 {
    font-size: 14px; text-transform: uppercase; letter-spacing: .18em;
    color: var(--ink-3); margin: 24px 0 10px;
  }
  table { width: 100%; border-collapse: collapse; font-family: ui-monospace, monospace; font-size: 12.5px; }
  th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--ink-3); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .12em; }
  td.kind-llm-call { color: var(--blue); }
  td.kind-tool-call { color: var(--purple); }
  td.kind-error { color: var(--red); }
  td.kind-thought { color: var(--ink-3); }
  tr.failed td.name { color: var(--red); }
  tr.tagged td.name::before { content: "◉ "; color: var(--amber); }
  pre {
    background: var(--bg-alt); border: 1px solid var(--border); padding: 12px 14px;
    border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
    color: var(--ink-2); font-size: 11.5px; line-height: 1.5;
    font-family: ui-monospace, "JetBrains Mono", monospace;
  }
  details summary { cursor: pointer; padding: 6px 0; color: var(--ink-3); font-size: 12px; }
  details[open] summary { color: var(--ink-2); }
  footer { color: var(--ink-3); font-size: 11px; margin-top: 32px; text-align: center; }
  footer a { color: var(--amber); text-decoration: none; }
</style>
</head>
<body>

<header class="case">
  <span class="brand">◉ Loupe</span>
  <h1 id="trace-title">trace</h1>
  <span class="meta" id="trace-meta"></span>
</header>

<div id="annotations"></div>
<div id="top-error"></div>

<h2>Steps</h2>
<table id="steps">
  <thead><tr><th>#</th><th>kind</th><th class="name">name</th><th>duration</th></tr></thead>
  <tbody></tbody>
</table>

<h2>Failure detail</h2>
<div id="failures"></div>

<footer>
  Generated by <a href="https://loupe.dev" target="_blank">Loupe</a> · open-source forensics for AI agents.
</footer>

<script>
(function () {
  // Server inlines real JSON here:
  const data = /*__LOUPE_DATA__*/null;
  if (!data) { document.body.textContent = "No trace data."; return; }

  const { header, steps, annotations } = data;
  const failed = header.metadata && header.metadata.failed;
  const dur = (header.ended_at && header.started_at)
    ? `${Math.max(0, (header.ended_at - header.started_at) * 1000).toFixed(0)} ms`
    : "—";

  // Title + meta
  document.getElementById("trace-title").textContent = header.name || "(unnamed trace)";
  const meta = document.getElementById("trace-meta");
  const annCount = annotations.length;
  meta.innerHTML = [
    `<span class="pill">${esc(header.framework || "—")}</span>`,
    `<span class="pill">${steps.length} ${steps.length === 1 ? "step" : "steps"}</span>`,
    `<span class="pill">${dur}</span>`,
    annCount > 0 ? `<span class="pill amber">${annCount} tagged</span>` : "",
    `<span class="pill ${failed ? "failed" : "ok"}">${failed ? "failed" : "ok"}</span>`,
  ].join(" ");

  // Annotations
  const annContainer = document.getElementById("annotations");
  annotations.forEach(a => {
    const card = document.createElement("div");
    card.className = "annot";
    card.innerHTML = `
      <div style="margin-bottom:8px">
        <span class="annot-cat">${esc(a.failure_category)}</span>
        <span class="pill" style="margin-left:6px">${esc(a.severity || "")}</span>
      </div>
      ${a.notes ? `<div style="color:var(--ink); margin-bottom:6px">${esc(a.notes)}</div>` : ""}
      ${a.mitigation ? `<div style="color:var(--ink-2); font-size:12.5px">Mitigation — ${esc(a.mitigation)}</div>` : ""}
    `;
    annContainer.appendChild(card);
  });

  // Top-level error
  if (header.metadata && header.metadata.error) {
    const e = document.createElement("div");
    e.className = "err";
    e.textContent = header.metadata.error;
    document.getElementById("top-error").appendChild(e);
  }

  // Steps table
  const annByStep = new Map();
  annotations.forEach(a => annByStep.set(a.step_id, a));
  const tbody = document.querySelector("#steps tbody");
  steps.forEach((s, i) => {
    const sdur = (s.ended_at && s.started_at)
      ? `${Math.max(0, (s.ended_at - s.started_at) * 1000).toFixed(1)} ms`
      : "—";
    const tagged = annByStep.has(s.step_id);
    const tr = document.createElement("tr");
    if (s.error) tr.classList.add("failed");
    if (tagged) tr.classList.add("tagged");
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td class="kind-${s.kind}">${esc(s.kind)}</td>
      <td class="name">${esc(s.name)}</td>
      <td>${sdur}</td>
    `;
    tbody.appendChild(tr);
  });

  // Failure detail
  const fdiv = document.getElementById("failures");
  const failing = steps.filter(s => s.error);
  if (failing.length === 0) {
    fdiv.innerHTML = `<div style="color:var(--ink-3); font-style:italic">No failed steps in this trace.</div>`;
  }
  failing.forEach(s => {
    const block = document.createElement("div");
    block.innerHTML = `
      <h3 style="font-size:14px; color:var(--ink); margin:14px 0 6px">Step <code>${esc(s.name)}</code> (${esc(s.kind)})</h3>
      <div class="err">${esc(s.error)}</div>
      ${jsonBlock("inputs", s.inputs)}
      ${jsonBlock("outputs", s.outputs)}
      ${jsonBlock("metadata", s.metadata)}
    `;
    fdiv.appendChild(block);
  });

  function jsonBlock(label, obj) {
    if (!obj || Object.keys(obj).length === 0) return "";
    return `
      <details>
        <summary>${label}</summary>
        <pre>${esc(JSON.stringify(obj, null, 2))}</pre>
      </details>
    `;
  }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }
})();
</script>
</body>
</html>
"""
