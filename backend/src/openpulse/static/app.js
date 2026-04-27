const state = {
  source: "website",
  selection: null,
  scriptPreview: null,
  scriptSelection: null,
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
  if (state.source === "script") $("scriptStatus").textContent = message;
  else $("browserStatus").textContent = message;
}

function setSource(source) {
  state.source = source;
  $("websiteSourceButton").classList.toggle("active", source === "website");
  $("scriptSourceButton").classList.toggle("active", source === "script");
  $("websitePanel").classList.toggle("hidden", source !== "website");
  $("scriptPanel").classList.toggle("hidden", source !== "script");
  renderSelection();
  updateConditionOptions();
}

function conditionFromForm() {
  if (state.source === "script" && state.scriptSelection?.mode === "items") {
    return { type: "new_item" };
  }
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

function updateConditionOptions() {
  const select = $("conditionType");
  if (state.source === "script" && state.scriptSelection?.mode === "items") {
    select.innerHTML = '<option value="new_item">new item appears</option>';
    $("conditionValueRow").style.display = "none";
    return;
  }
  const numeric = state.source === "website"
    ? state.selection?.semanticType === "price" || state.selection?.semanticType === "number"
    : state.scriptSelection?.valueType === "number";
  select.innerHTML = numeric
    ? `
      <option value="changed">changes</option>
      <option value="less_than">less than</option>
      <option value="greater_than">greater than</option>
      <option value="equals">equals</option>
    `
    : `
      <option value="changed">changes</option>
      <option value="equals">equals</option>
      <option value="contains">contains</option>
      <option value="appears">appears</option>
      <option value="disappears">disappears</option>
    `;
  updateConditionValueVisibility();
}

function updateConditionValueVisibility() {
  const type = $("conditionType").value;
  $("conditionValueRow").style.display = ["changed", "appears", "disappears", "new_item"].includes(type) ? "none" : "grid";
}

function renderSelection() {
  $("scriptOutputPicker").classList.toggle("hidden", state.source !== "script");
  if (state.source === "script") {
    if (state.scriptPreview?.ok) {
      $("selectionPreview").textContent = formatScriptOutputPreview();
    } else if (state.scriptPreview && !state.scriptPreview.ok) {
      $("selectionPreview").textContent = state.scriptPreview.execution?.stderr || state.scriptPreview.error || "Script preview failed.";
    } else {
      $("selectionPreview").textContent = "Run a script preview to see the full output here.";
    }
    renderScriptOutputPicker();
    return;
  }
  $("selectionPreview").textContent = state.selection
    ? JSON.stringify(state.selection, null, 2)
    : "No target selected yet.";
}

function renderScriptOutputPicker() {
  const container = $("scriptOutputPicker");
  if (!state.scriptPreview) {
    container.innerHTML = "";
    return;
  }
  if (!state.scriptPreview.ok) {
    container.innerHTML = `
      <article class="item">
        <div class="itemTitle"><span class="missing">${escapeHtml(state.scriptPreview.error)}</span></div>
        <div class="itemMeta">${escapeHtml(state.scriptPreview.execution?.stderr || "Script preview failed.")}</div>
      </article>
    `;
    return;
  }
  container.innerHTML = `
    <div class="pickerSection">
      <div class="sectionTitle">Select what to monitor from the preview above</div>
      ${state.scriptPreview.nodes.map((node, index) => renderOutputNode(node, index)).join("")}
    </div>
  `;
  container.querySelectorAll("[data-node-index]").forEach((button) => {
    button.addEventListener("click", () => selectScriptNode(Number(button.dataset.nodeIndex)));
  });
  container.querySelectorAll("[data-item-field]").forEach((select) => {
    select.addEventListener("change", updateScriptItemSelectionFromFields);
  });
}

function formatScriptOutputPreview() {
  if (!state.scriptPreview) return "";
  if (state.scriptPreview.outputType === "json") {
    return JSON.stringify(state.scriptPreview.parsed, null, 2);
  }
  return state.scriptPreview.stdout || "";
}

function renderOutputNode(node, index) {
  const selected = state.scriptSelection?.nodeIndex === index;
  if (node.kind === "array") {
    const fields = node.idFieldOptions || [];
    const optionHtml = (selectedValue) => fields
      .map((field) => {
        const selectedAttr = field === selectedValue ? " selected" : "";
        return `<option value="${escapeHtml(field)}"${selectedAttr}>${escapeHtml(field)}</option>`;
      })
      .join("");
    const fieldSelectors = selected
      ? `
        <div class="itemFieldGrid">
          <label>
            Unique item field
            <select data-item-field="idField">${optionHtml(state.scriptSelection?.idField || "")}</select>
            <span class="helpText">Used to decide whether an item is new. For Jira, use key.</span>
          </label>
          <label>
            Shown in logs
            <select data-item-field="displayField"><option value=""${state.scriptSelection?.displayField ? "" : " selected"}>Use unique field</option>${optionHtml(state.scriptSelection?.displayField || "")}</select>
            <span class="helpText">Human-readable label for matched logs. For Jira, summary is usually best.</span>
          </label>
          <label>
            Link field
            <select data-item-field="urlField"><option value=""${state.scriptSelection?.urlField ? "" : " selected"}>None</option>${optionHtml(state.scriptSelection?.urlField || "")}</select>
            <span class="helpText">Optional URL to include in event details when the script provides one.</span>
          </label>
        </div>
      `
      : "";
    return `
      <div>
        <button type="button" class="nodeButton ${selected ? "selected" : ""}" data-node-index="${index}">
          ${escapeHtml(arrayNodeLabel(node))}
          <div class="nodeMeta">Path: ${escapeHtml(node.path)} · ${node.length} item(s), fields: ${escapeHtml(fields.join(", ") || "none")}</div>
        </button>
        ${fieldSelectors}
      </div>
    `;
  }
  return `
    <button type="button" class="nodeButton ${selected ? "selected" : ""}" data-node-index="${index}">
      ${escapeHtml(node.path)} = ${escapeHtml(String(node.value))}
      <div class="nodeMeta">${escapeHtml(node.valueType)}</div>
    </button>
  `;
}

function arrayNodeLabel(node) {
  if (node.path === "$") {
    return `Monitor the top-level list`;
  }
  return `Monitor the list field "${node.path}"`;
}

function selectScriptNode(index) {
  const node = state.scriptPreview.nodes[index];
  if (node.kind === "array") {
    const fields = node.idFieldOptions || [];
    const idField = preferredField(fields, ["key", "id", "guid", "uuid"]) || fields[0] || "";
    const displayField = preferredField(fields, ["summary", "title", "name", "label", "key"]) || "";
    const urlField = preferredField(fields, ["url", "link", "href", "webUrl", "htmlUrl"]) || "";
    state.scriptSelection = {
      nodeIndex: index,
      mode: "items",
      outputType: "json",
      arrayPath: node.path,
      idField,
      displayField,
      urlField
    };
    $("monitorName").value = node.path === "$" ? "new script items" : `new items in ${node.path}`;
  } else {
    state.scriptSelection = {
      nodeIndex: index,
      mode: "scalar",
      outputType: state.scriptPreview.outputType,
      path: node.path,
      initialValue: String(node.value),
      valueType: node.valueType
    };
    $("monitorName").value = `${node.path} watch`;
  }
  updateConditionOptions();
  renderSelection();
}

function preferredField(fields, candidates) {
  const lowerToOriginal = new Map(fields.map((field) => [field.toLowerCase(), field]));
  for (const candidate of candidates) {
    const exact = lowerToOriginal.get(candidate.toLowerCase());
    if (exact) return exact;
  }
  return "";
}

function updateScriptItemSelectionFromFields() {
  if (!state.scriptSelection || state.scriptSelection.mode !== "items") return;
  $("scriptOutputPicker").querySelectorAll("[data-item-field]").forEach((select) => {
    state.scriptSelection[select.dataset.itemField] = select.value;
  });
  renderSelection();
}

function scriptConfigFromForm() {
  return {
    command: $("scriptCommand").value.trim(),
    args: $("scriptArgs").value.split("\n").map((line) => line.trim()).filter(Boolean),
    cwd: $("scriptCwd").value.trim() || null,
    timeoutSeconds: Number($("scriptTimeout").value || 10)
  };
}

function scriptBaselineItems() {
  if (!state.scriptPreview || state.scriptSelection?.mode !== "items") return [];
  const items = getPath(state.scriptPreview.parsed, state.scriptSelection.arrayPath);
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && Object.prototype.hasOwnProperty.call(item, state.scriptSelection.idField))
    .map((item) => ({ id: String(item[state.scriptSelection.idField]), item }));
}

function getPath(value, path) {
  if (!path || path === "$") return value;
  return path.split(".").reduce((current, part) => current && current[part], value);
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
          <div class="stateRow">
            <span class="statePill ${escapeHtml(statusClass(monitor.lastStatus))}">${escapeHtml(monitor.lastStatus || "pending")}</span>
            ${monitor.enabled ? "" : '<span class="statePill disabled">disabled</span>'}
            ${monitor.consecutiveFailures > 0 ? `<span class="statePill warning">${monitor.consecutiveFailures} failures</span>` : ""}
          </div>
          <div class="itemMeta">${escapeHtml(monitorSummary(monitor))}</div>
          <div class="itemMeta">Condition: ${escapeHtml(JSON.stringify(monitor.condition))}</div>
          <div class="stateGrid">
            <div>
              <span>Last checked</span>
              <strong>${escapeHtml(formatDateTime(monitor.lastCheckedAt, "Not yet"))}</strong>
            </div>
            <div>
              <span>Next check</span>
              <strong>${escapeHtml(monitor.enabled ? formatRelativeTime(monitor.nextCheckAt) : "Disabled")}</strong>
            </div>
            <div>
              <span>Last value</span>
              <strong>${escapeHtml(monitor.lastValue || "-")}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong>${escapeHtml(formatDuration(monitor.lastDurationMs))}</strong>
            </div>
          </div>
          ${monitor.lastError ? `<div class="itemMeta error">Reason: ${escapeHtml(monitor.lastError)}</div>` : ""}
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

function monitorSummary(monitor) {
  if (monitor.target?.sourceType === "script") {
    const selection = monitor.target.selection || {};
    if (selection.mode === "items") {
      const source = selection.arrayPath === "$" ? "script output" : `${selection.arrayPath}`;
      return `Watching for new items in ${source}. Recognizes items by ${selection.idField}.`;
    }
    return `Watching script value: ${selection.path || "full output"}`;
  }
  return `Watching ${monitor.target?.semanticType || "selected target"} on ${monitor.url}`;
}

function statusClass(status) {
  return ["pending", "checked", "matched", "missing", "blocked", "error"].includes(status) ? status : "pending";
}

function formatDateTime(value, fallback = "-") {
  if (!value) return fallback;
  return new Date(value).toLocaleString();
}

function formatRelativeTime(value) {
  if (!value) return "Not scheduled";
  const diffMs = new Date(value).getTime() - Date.now();
  const absSeconds = Math.max(0, Math.round(Math.abs(diffMs) / 1000));
  if (diffMs <= 0) return "Due now";
  if (absSeconds < 60) return `in ${absSeconds}s`;
  const minutes = Math.round(absSeconds / 60);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  return `in ${hours}h`;
}

function formatDuration(value) {
  if (value === null || value === undefined) return "-";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
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
          <div class="itemMeta">Current: ${escapeHtml(logDisplayValue(log))}</div>
        </article>
      `
    )
    .join("");
}

function logDisplayValue(log) {
  return log.details?.display || log.currentValue || "-";
}

async function refreshSelection() {
  if (state.source !== "website") return;
  const selection = await api("/api/selection");
  if (selection && JSON.stringify(selection) !== JSON.stringify(state.selection)) {
    state.selection = selection;
    $("monitorName").value = `${selection.semanticType} watch`;
    $("conditionType").value = selection.semanticType === "price" || selection.semanticType === "number" ? "less_than" : "changed";
    updateConditionOptions();
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

$("websiteSourceButton").addEventListener("click", () => setSource("website"));
$("scriptSourceButton").addEventListener("click", () => setSource("script"));

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

$("runScriptPreviewButton").addEventListener("click", async () => {
  const config = scriptConfigFromForm();
  if (!config.command) {
    setStatus("Enter a command before running preview.");
    return;
  }
  setStatus("Running script preview...");
  state.scriptPreview = await api("/api/scripts/preview", {
    method: "POST",
    body: JSON.stringify(config)
  });
  state.scriptSelection = null;
  setStatus(state.scriptPreview.ok ? "Preview ready. Select a value or item list." : `Preview failed: ${state.scriptPreview.error}`);
  renderSelection();
});

$("conditionType").addEventListener("change", updateConditionValueVisibility);

$("monitorForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.source === "website") {
    await saveWebsiteMonitor();
  } else {
    await saveScriptMonitor();
  }
});

async function saveWebsiteMonitor() {
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
}

async function saveScriptMonitor() {
  if (!state.scriptSelection) {
    setStatus("Run preview and select script output before saving.");
    return;
  }
  if (state.scriptSelection.mode === "items" && !state.scriptSelection.idField) {
    setStatus("Choose an ID field before saving an item-list monitor.");
    return;
  }
  const config = scriptConfigFromForm();
  const selection = { ...state.scriptSelection };
  delete selection.nodeIndex;
  delete selection.valueType;
  const target = {
    sourceType: "script",
    script: config,
    selection
  };
  if (selection.mode === "items") {
    target._baselineItems = scriptBaselineItems();
  }
  await api("/api/monitors", {
    method: "POST",
    body: JSON.stringify({
      name: $("monitorName").value.trim() || "Script monitor",
      url: `script://${config.command}`,
      target,
      condition: conditionFromForm(),
      intervalSeconds: Number($("intervalSeconds").value || 300),
      enabled: true
    })
  });
  setStatus("Script monitor saved.");
  await refreshMonitors();
}

$("refreshMonitorsButton").addEventListener("click", refreshMonitors);
$("refreshLogsButton").addEventListener("click", refreshLogs);

setSource("website");
renderSelection();
await refreshMonitors();
await refreshLogs();
setInterval(refreshSelection, 1500);
setInterval(async () => {
  await refreshLogs();
  await refreshMonitors();
}, 5000);
