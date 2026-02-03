from __future__ import annotations

import json
from typing import Any


def dashboard_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload)

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tanuki • Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100">
  <div class="max-w-6xl mx-auto p-6">
    <div class="flex items-end justify-between gap-4">
      <div>
        <h1 class="text-3xl font-semibold tracking-tight">Tanuki</h1>
        <p class="text-slate-400 text-sm mt-1">Local dashboard (projects + backlog)</p>
      </div>
      <div class="text-xs text-slate-400">Press Ctrl+C in terminal to stop</div>
    </div>

    <div class="mt-8">
      <h2 class="text-lg font-medium">Projects</h2>
      <div id="projects" class="mt-3 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"></div>
    </div>

    <div class="mt-10">
      <h2 class="text-lg font-medium">Active project</h2>
      <div id="active" class="mt-3"></div>
    </div>
  </div>

<script>
const DATA = __DATA__;

function badge(text) {
  return `<span class="px-2 py-1 rounded-md bg-slate-800 text-slate-200 text-xs border border-slate-700">${text}</span>`;
}

function projectCard(p, isActive) {
  return `
  <div class="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
    <div class="flex items-start justify-between gap-3">
      <div>
        <div class="text-base font-semibold">${p.name}</div>
        <div class="text-xs text-slate-400 mt-1 break-all">${p.repo_path}</div>
      </div>
      <div class="flex flex-col items-end gap-2">
        ${isActive ? badge("ACTIVE") : ""}
        ${badge(p.id)}
      </div>
    </div>
    <div class="mt-3 text-xs text-slate-500">
      created: ${p.created_at}<br/>
      last used: ${p.last_used_at || "-"}
    </div>
  </div>`;
}

function render() {
  const container = document.getElementById("projects");
  const activeBox = document.getElementById("active");
  container.innerHTML = "";

  if (!DATA.projects.length) {
    container.innerHTML = `
      <div class="rounded-xl border border-slate-800 bg-slate-900/40 p-6 text-slate-300">
        No projects yet.<br/>
        <span class="text-slate-500">Create one with:</span>
        <code class="block mt-2 text-sm bg-slate-950 border border-slate-800 rounded-lg p-3">tanuki project up --path /path/to/repo</code>
      </div>`;
  } else {
    DATA.projects.forEach(p => {
      container.insertAdjacentHTML("beforeend", projectCard(p, p.id === DATA.active_id));
    });
  }

  if (!DATA.active_id) {
    activeBox.innerHTML = `<div class="text-slate-400">No active project.</div>`;
  } else {
    const ap = DATA.projects.find(x => x.id === DATA.active_id);
    activeBox.innerHTML = ap
      ? `<div class="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
           <div class="text-base font-semibold">${ap.name}</div>
           <div class="text-xs text-slate-400 mt-1 break-all">${ap.repo_path}</div>
         </div>`
      : `<div class="text-slate-400">Active project not found in registry.</div>`;
  }
}

render();
</script>
</body>
</html>
"""
    return html.replace("__DATA__", data)