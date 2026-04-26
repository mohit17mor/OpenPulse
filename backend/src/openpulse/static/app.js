const state = {
  selection: null,
  monitors: [],
  logs: []
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

function setStatus(message) {
  $("browserStatus").textContent = message;
}

function conditionFromForm() {
  const type = $("conditionType").value;
  const rawValue = $("conditionValue").value.trim();
  if (["changed", "appears", "disappears"].includes(type)) {
    return { type };
  }
  if (["greater_than", "less_than"].includes(type)) {
    return { type, value: Number(rawValue) };
  }
  return { type, value: rawValue };
}

function updateConditionValueVisibility() {
  const type = $("conditionType").value;
  $("conditionValueRow").style.display = ["changed", "appears", "disappears"].includes(type) ? "none" : "grid";
}

function renderSelection() {
  $("selectionPreview").textContent = state.selection
    ? JSON.stringify(state.selection, null, 2)
    : "No target selected yet.";
}

function renderMonitors() {
  const container = $("monitorsList");
  if (state.monitors.length === 0) {
    container.innerHTML = '<p class="subtle">No monitors saved yet.</p>';
    return;
  }
  container.innerHTML = state.monitors
    .map(
      (monitor) => `
        <article class="item">
          <div class="itemTitle">
            <span>${escapeHtml(monitor.name)}</span>
            <span class="actions">
              <button class="secondary" data-run-check="${monitor.id}">Run Check</button>
              <button class="danger" data-delete-monitor="${monitor.id}">Delete</button>
            </span>
          </div>
          <div class="itemMeta">${escapeHtml(monitor.url)}</div>
          <div class="itemMeta">${escapeHtml(monitor.target.semanticType)}: ${escapeHtml(monitor.target.initialValue || "")}</div>
          <div class="itemMeta">Condition: ${escapeHtml(JSON.stringify(monitor.condition))}</div>
          <div class="itemMeta">Interval: ${monitor.intervalSeconds}s · Last checked: ${monitor.lastCheckedAt ? new Date(monitor.lastCheckedAt).toLocaleString() : "not yet"}</div>
        </article>
      `
    )
    .join("");

  container.querySelectorAll("[data-run-check]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/monitors/${button.dataset.runCheck}/check`, { method: "POST" });
      await refreshLogs();
      await refreshMonitors();
    });
  });

  container.querySelectorAll("[data-delete-monitor]").forEach((button) => {
    button.addEventListener("click", async () => {
      const monitor = state.monitors.find((item) => item.id === button.dataset.deleteMonitor);
      const label = monitor ? monitor.name : "this monitor";
      if (!window.confirm(`Delete ${label}? Its logs will be removed too.`)) return;
      await api(`/api/monitors/${button.dataset.deleteMonitor}`, { method: "DELETE" });
      setStatus("Monitor deleted.");
      await refreshLogs();
      await refreshMonitors();
    });
  });
}

function renderLogs() {
  const container = $("logsList");
  if (state.logs.length === 0) {
    container.innerHTML = '<p class="subtle">No check logs yet.</p>';
    return;
  }
  container.innerHTML = state.logs
    .map(
      (log) => `
        <article class="item">
          <div class="itemTitle">
            <span class="${log.status}">${escapeHtml(log.status)}</span>
            <span>${new Date(log.createdAt).toLocaleString()}</span>
          </div>
          <div class="itemMeta">${escapeHtml(log.message)}</div>
          <div class="itemMeta">Previous: ${escapeHtml(log.previousValue || "-")}</div>
          <div class="itemMeta">Current: ${escapeHtml(log.currentValue || "-")}</div>
        </article>
      `
    )
    .join("");
}

async function refreshSelection() {
  const selection = await api("/api/selection");
  if (selection && JSON.stringify(selection) !== JSON.stringify(state.selection)) {
    state.selection = selection;
    $("monitorName").value = `${selection.semanticType} watch`;
    if (selection.semanticType === "price" || selection.semanticType === "number") {
      $("conditionType").value = "less_than";
      $("conditionValue").value = "";
    } else {
      $("conditionType").value = "changed";
    }
    updateConditionValueVisibility();
    renderSelection();
  }
}

async function refreshMonitors() {
  state.monitors = await api("/api/monitors");
  renderMonitors();
}

async function refreshLogs() {
  state.logs = await api("/api/logs");
  renderLogs();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$("launchButton").addEventListener("click", async () => {
  setStatus("Launching browser...");
  const result = await api("/api/browser/launch", { method: "POST" });
  setStatus(`Browser ${result.status}. Navigate to a page, then press M in the browser.`);
});

$("navigateButton").addEventListener("click", async () => {
  setStatus("Navigating...");
  const result = await api("/api/browser/navigate", {
    method: "POST",
    body: JSON.stringify({ url: $("urlInput").value })
  });
  $("urlInput").value = result.url;
  setStatus(`Navigated to ${result.url}. Press M in the browser or use Enable Monitor Mode.`);
});

$("monitorModeButton").addEventListener("click", async () => {
  await api("/api/browser/monitor-mode", { method: "POST" });
  setStatus("Monitor mode enabled. Click a highlighted fact or drag a rectangle to narrow candidates.");
});

$("clearSelectionButton").addEventListener("click", async () => {
  await api("/api/selection/clear", { method: "POST" });
  state.selection = null;
  renderSelection();
});

$("conditionType").addEventListener("change", updateConditionValueVisibility);

$("monitorForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selection) {
    setStatus("Select a target in the browser before saving a monitor.");
    return;
  }
  await api("/api/monitors", {
    method: "POST",
    body: JSON.stringify({
      name: $("monitorName").value.trim() || "OpenPulse monitor",
      url: state.selection.url,
      target: state.selection,
      condition: conditionFromForm(),
      intervalSeconds: Number($("intervalSeconds").value || 300),
      enabled: true
    })
  });
  setStatus("Monitor saved.");
  await refreshMonitors();
});

$("refreshMonitorsButton").addEventListener("click", refreshMonitors);
$("refreshLogsButton").addEventListener("click", refreshLogs);

updateConditionValueVisibility();
renderSelection();
await refreshMonitors();
await refreshLogs();
setInterval(refreshSelection, 1500);
setInterval(async () => {
  await refreshLogs();
  await refreshMonitors();
}, 5000);
