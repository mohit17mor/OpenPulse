const state = {
  view: "website",
  source: "website",
  selection: null,
  scriptPreview: null,
  scriptSelection: null,
  sampleMonitors: [],
  customScripts: [],
  workspace: null,
  destinations: [],
  destinationHealth: {},
  monitors: [],
  logs: []
};

const $ = (id) => document.getElementById(id);

const AGENT_PRESETS = {
  codex: {
    name: "Codex",
    port: 8765,
    command: "codex exec"
  },
  claude: {
    name: "Claude",
    port: 8766,
    command: "claude -p"
  }
};

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

function setSaveStatus(message, kind = "info") {
  const status = $("monitorSaveStatus");
  status.textContent = message;
  status.className = `saveStatus ${kind}`;
}

function setView(view) {
  state.view = view;
  const createViewActive = view === "website" || view === "script";
  $("createView").classList.toggle("hidden", !createViewActive);
  $("samplesView").classList.toggle("hidden", view !== "samples");
  $("monitorsView").classList.toggle("hidden", view !== "monitors");
  $("logsView").classList.toggle("hidden", view !== "logs");
  $("destinationsView").classList.toggle("hidden", view !== "destinations");

  $("websiteSourceButton").classList.toggle("active", view === "website");
  $("scriptSourceButton").classList.toggle("active", view === "script");
  $("samplesViewButton").classList.toggle("active", view === "samples");
  $("monitorsViewButton").classList.toggle("active", view === "monitors");
  $("logsViewButton").classList.toggle("active", view === "logs");
  $("destinationsViewButton").classList.toggle("active", view === "destinations");

  if (view === "website" || view === "script") {
    setSource(view);
  } else if (view === "samples") {
    $("mainCrumb").textContent = "Create";
    $("mainTitle").textContent = "Script library";
    $("mainSubtitle").textContent = "Load a starter script or keep your own scripts in scripts/custom.";
    refreshSamples();
  } else if (view === "monitors") {
    $("mainCrumb").textContent = "Workspace";
    $("mainTitle").textContent = "Saved monitors";
    $("mainSubtitle").textContent = "Review monitor health, run checks, and delete rules you no longer need.";
    refreshMonitors();
  } else {
    if (view === "destinations") {
      $("mainCrumb").textContent = "Workspace";
      $("mainTitle").textContent = "Agents";
      $("mainSubtitle").textContent = "Send matched monitor events to webhooks, bridges, or local commands.";
      refreshDestinations();
      return;
    }
    $("mainCrumb").textContent = "Workspace";
    $("mainTitle").textContent = "Event logs";
    $("mainSubtitle").textContent = "Inspect recent check outcomes, matches, missing targets, and script errors.";
    refreshLogs();
  }
}

function setSource(source) {
  state.source = source;
  $("websitePanel").classList.toggle("hidden", source !== "website");
  $("scriptPanel").classList.toggle("hidden", source !== "script");
  $("mainCrumb").textContent = "Create";
  $("mainTitle").textContent = source === "script" ? "Script monitor" : "Website monitor";
  $("mainSubtitle").textContent = source === "script"
    ? "Run a local command, select output, and save the rule."
    : "Open a page, select a signal, and save the rule.";
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
      $("selectionPreview").textContent = state.scriptSelection
        ? scriptSelectionSummary()
        : "Run a script preview to see the full output here.";
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

function selectionWithPreviewNode(selection, preview) {
  if (!selection || !preview?.ok) return null;
  if (selection.mode === "items") {
    const index = preview.nodes.findIndex((node) => node.kind === "array" && node.path === selection.arrayPath);
    return index >= 0 ? { ...selection, nodeIndex: index } : null;
  }
  const index = preview.nodes.findIndex((node) => node.kind === "scalar" && node.path === selection.path);
  if (index < 0) return null;
  const node = preview.nodes[index];
  return {
    ...selection,
    nodeIndex: index,
    initialValue: String(node.value),
    valueType: node.valueType
  };
}

function scriptSelectionSummary() {
  if (!state.scriptSelection) return "";
  if (state.scriptSelection.mode === "items") {
    return [
      "Starter item-list selection loaded.",
      `Array path: ${state.scriptSelection.arrayPath}`,
      `ID field: ${state.scriptSelection.idField}`,
      `Display field: ${state.scriptSelection.displayField || "-"}`,
      `URL field: ${state.scriptSelection.urlField || "-"}`,
      "",
      "Run preview to inspect the current output before saving."
    ].join("\n");
  }
  return [
    "Starter scalar selection loaded.",
    `Path: ${state.scriptSelection.path || "$stdout"}`,
    `Output type: ${state.scriptSelection.outputType || "json"}`,
    "",
    "Run preview to inspect the current output before saving."
  ].join("\n");
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

function renderSamples() {
  const container = $("samplesList");
  if (state.sampleMonitors.length === 0 && state.customScripts.length === 0) {
    container.innerHTML = '<p class="subtle">No scripts found yet. Add Python scripts to scripts/custom and refresh.</p>';
    return;
  }
  container.innerHTML = `
    ${renderScriptLibrarySection("Custom scripts", "Files detected from scripts/custom.", state.customScripts, "Use script")}
    ${renderScriptLibrarySection("Starter scripts", "Built-in examples you can load and edit.", state.sampleMonitors, "Load starter")}
  `;
  container.querySelectorAll("[data-use-script]").forEach((button) => {
    button.addEventListener("click", async () => {
      const script = [...state.customScripts, ...state.sampleMonitors].find((item) => item.id === button.dataset.useScript);
      if (script) await applyScriptLibraryItem(script);
    });
  });
}

function renderScriptLibrarySection(title, subtitle, scripts, buttonLabel) {
  const body = scripts.length === 0
    ? '<p class="subtle">No scripts found in this group.</p>'
    : `
      <div class="sampleGrid">
        ${scripts.map((sample) => renderScriptLibraryCard(sample, buttonLabel)).join("")}
      </div>
    `;
  return `
    <section class="librarySection">
      <div class="libraryHeader">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(subtitle)}</p>
        </div>
        <span>${scripts.length}</span>
      </div>
      ${body}
    </section>
  `;
}

function renderScriptLibraryCard(sample, buttonLabel) {
  const selectionLabel = sample.selection?.mode === "items" ? "new items" : sample.selection?.path || "choose after preview";
  return `
        <article class="sampleCard">
          <div>
            <div class="sampleCategory">${escapeHtml(sample.category || "Starter")}</div>
            <h3>${escapeHtml(sample.name)}</h3>
            <p>${escapeHtml(sample.description)}</p>
          </div>
          <div class="sampleMeta">
            <span>${escapeHtml(selectionLabel)}</span>
            <span>${escapeHtml(conditionLabel(sample.condition))}</span>
            <span>${escapeHtml(`${sample.intervalSeconds || 300}s`)}</span>
          </div>
          <button type="button" data-use-script="${escapeHtml(sample.id)}">${escapeHtml(buttonLabel)}</button>
        </article>
      `;
}

function conditionLabel(condition) {
  if (!condition) return "condition";
  if (condition.type === "new_item") return "new item appears";
  if (condition.value === undefined || condition.value === null || condition.value === "") return condition.type;
  return `${condition.type.replaceAll("_", " ")} ${condition.value}`;
}

async function applyScriptLibraryItem(sample) {
  const script = sample.script || {};
  state.scriptPreview = null;
  state.scriptSelection = sample.selection ? { ...sample.selection } : null;
  $("scriptCommand").value = script.command || "python3";
  $("scriptArgs").value = (script.args || []).join("\n");
  $("scriptCwd").value = script.cwd || "";
  $("scriptTimeout").value = script.timeoutSeconds || 10;
  $("monitorName").value = sample.name || "Script monitor";
  $("intervalSeconds").value = sample.intervalSeconds || 300;
  setView("script");
  applyCondition(sample.condition || { type: "changed" });
  setStatus(`${sample.name} loaded. Running preview...`);
  renderSelection();
  await runScriptPreview();
}

function applyCondition(condition) {
  updateConditionOptions();
  const select = $("conditionType");
  const option = Array.from(select.options).find((item) => item.value === condition.type);
  if (option) select.value = condition.type;
  $("conditionValue").value = condition.value === undefined || condition.value === null ? "" : String(condition.value);
  updateConditionValueVisibility();
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
              <button class="secondary" data-toggle-monitor="${monitor.id}">
                ${monitor.enabled ? "Pause" : "Resume"}
              </button>
              <button class="danger" data-delete-monitor="${monitor.id}">Delete</button>
            </span>
          </div>
          <div class="stateRow">
            <span class="statePill ${escapeHtml(statusClass(monitor.lastStatus))}">${escapeHtml(monitor.lastStatus || "pending")}</span>
            ${monitor.enabled ? "" : '<span class="statePill disabled">disabled</span>'}
            ${monitor.consecutiveFailures > 0 ? `<span class="statePill warning">${monitor.consecutiveFailures} failures</span>` : ""}
          </div>
          <div class="itemMeta">${escapeHtml(monitorSummary(monitor))}</div>
          <div class="itemMeta">Destinations: ${escapeHtml(destinationNames(monitor.destinationIds).join(", ") || "Log only")}</div>
          ${monitor.agentInstructions ? `<div class="itemMeta">Agent: ${escapeHtml(monitor.agentInstructions)}</div>` : ""}
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

  container.querySelectorAll("[data-toggle-monitor]").forEach((button) => {
    button.addEventListener("click", async () => {
      const monitor = state.monitors.find((item) => item.id === button.dataset.toggleMonitor);
      const action = monitor?.enabled ? "pause" : "resume";
      await api(`/api/monitors/${button.dataset.toggleMonitor}/${action}`, { method: "POST" });
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

function destinationNames(destinationIds = []) {
  const names = new Map(state.destinations.map((destination) => [destination.id, destination.name]));
  return destinationIds.map((id) => names.get(id) || id);
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
  if (monitor.target?.sourceType === "network") {
    const recipe = monitor.target.networkRecipe || {};
    const label = recipe.valueLabel || monitor.target.semanticType || "selected value";
    const identity = recipe.identity ? Object.keys(recipe.identity).join(", ") : "saved identity";
    return `Watching ${label} from network data. Recognizes the item by ${identity}.`;
  }
  return `Watching ${monitor.target?.semanticType || "selected target"} on ${monitor.url}`;
}

function statusClass(status) {
  return ["pending", "checking", "checked", "matched", "missing", "blocked", "error"].includes(status) ? status : "pending";
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
        <article class="eventItem ${escapeHtml(log.severity || "info")}">
          <div class="eventHeader">
            <div>
              <div class="eventTitle">${escapeHtml(log.title || log.message)}</div>
              <div class="eventTime">${escapeHtml(formatDateTime(log.createdAt))}</div>
            </div>
            <div class="eventBadges">
              <span class="statePill ${escapeHtml(log.severity || statusClass(log.status))}">${escapeHtml(log.severity || log.status)}</span>
              <span class="statePill">${escapeHtml(log.sourceType || "unknown")}</span>
            </div>
          </div>
          <div class="eventSummary">${escapeHtml(log.summary || log.message)}</div>
          <div class="eventFacts">
            <span>Type: ${escapeHtml(log.eventType || "check_completed")}</span>
            <span>Reason: ${escapeHtml(log.reasonCode || log.message || "-")}</span>
            <span>Previous: ${escapeHtml(log.previousValue || "-")}</span>
            <span>Current: ${escapeHtml(logDisplayValue(log))}</span>
          </div>
          ${log.actionHint ? `<div class="eventHint">${escapeHtml(log.actionHint)}</div>` : ""}
        </article>
      `
    )
    .join("");
}

function renderDestinations() {
  renderDestinationPicker();
  const container = $("destinationsList");
  if (state.destinations.length === 0) {
    container.innerHTML = '<p class="subtle">No destinations created yet.</p>';
    return;
  }
  container.innerHTML = state.destinations
    .map(
      (destination) => `
        <article class="item">
          <div class="itemTitle">
            <span>${escapeHtml(destination.name)}</span>
            <span class="actions">
              <button class="secondary" data-check-destination="${escapeHtml(destination.id)}">Check</button>
              <button class="danger" data-delete-destination="${escapeHtml(destination.id)}">Delete</button>
            </span>
          </div>
          <div class="stateRow">
            <span class="statePill">${escapeHtml(destination.type)}</span>
            ${destinationHealthPill(destination.id)}
            ${destination.enabled ? "" : '<span class="statePill disabled">disabled</span>'}
          </div>
          <div class="itemMeta">${escapeHtml(destinationSummary(destination))}</div>
          ${destination.config?.bridgeCommand ? `<div class="bridgeCommandSmall">${escapeHtml(destination.config.bridgeCommand)}</div>` : ""}
          ${destinationHealthMessage(destination.id)}
        </article>
      `
    )
    .join("");
  container.querySelectorAll("[data-check-destination]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.textContent = "Checking...";
      const result = await api(`/api/destinations/${button.dataset.checkDestination}/health`, { method: "POST" });
      state.destinationHealth[button.dataset.checkDestination] = result;
      renderDestinations();
    });
  });
  container.querySelectorAll("[data-delete-destination]").forEach((button) => {
    button.addEventListener("click", async () => {
      const destination = state.destinations.find((item) => item.id === button.dataset.deleteDestination);
      if (!window.confirm(`Delete ${destination?.name || "this destination"}?`)) return;
      await api(`/api/destinations/${button.dataset.deleteDestination}`, { method: "DELETE" });
      $("destinationStatus").textContent = "Destination deleted.";
      await refreshDestinations();
      await refreshMonitors();
    });
  });
}

function destinationSummary(destination) {
  if (destination.config?.agentKind === "codex") return `Codex bridge at ${destination.config?.url}`;
  if (destination.config?.agentKind === "claude") return `Claude bridge at ${destination.config?.url}`;
  if (destination.type === "webhook") return destination.config?.url || "Webhook";
  return [destination.config?.command, ...(destination.config?.args || [])].filter(Boolean).join(" ") || "Local command";
}

function destinationHealthPill(destinationId) {
  const health = state.destinationHealth[destinationId];
  if (!health) return '<span class="statePill disabled">unknown</span>';
  const klass = health.ok ? "success" : "error";
  return `<span class="statePill ${klass}">${escapeHtml(health.status || (health.ok ? "online" : "offline"))}</span>`;
}

function destinationHealthMessage(destinationId) {
  const health = state.destinationHealth[destinationId];
  if (!health || health.ok) return "";
  return `<div class="eventHint">Destination is not reachable: ${escapeHtml(health.message || "health check failed")}</div>`;
}

function renderDestinationPicker() {
  const container = $("destinationPicker");
  if (!container) return;
  if (state.destinations.length === 0) {
    container.innerHTML = '<p class="subtle">Log only. Add agents from the Agents view.</p>';
    return;
  }
  container.innerHTML = state.destinations
    .map(
      (destination) => `
        <label class="checkRow">
          <input type="checkbox" value="${escapeHtml(destination.id)}" />
          <span>
            <strong>${escapeHtml(destination.name)}</strong>
            <small>${escapeHtml(destination.type)} · ${escapeHtml(state.destinationHealth[destination.id]?.status || "unknown")}</small>
          </span>
        </label>
      `
    )
    .join("");
}

function selectedDestinationIds() {
  return Array.from($("destinationPicker").querySelectorAll("input[type='checkbox']:checked")).map((input) => input.value);
}

function agentInstructionsFromForm() {
  return $("agentInstructions").value.trim();
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

async function refreshDestinations() {
  state.destinations = await api("/api/destinations");
  renderDestinations();
  await refreshDestinationHealth();
}

async function refreshDestinationHealth() {
  if (state.destinations.length === 0) return;
  const results = await Promise.all(
    state.destinations.map(async (destination) => {
      try {
        return [destination.id, await api(`/api/destinations/${destination.id}/health`, { method: "POST" })];
      } catch (error) {
        return [destination.id, { ok: false, status: "offline", message: error.message }];
      }
    })
  );
  state.destinationHealth = Object.fromEntries(results);
  renderDestinations();
}

async function refreshSamples() {
  const [sampleMonitors, customScripts] = await Promise.all([
    api("/api/script-templates"),
    api("/api/scripts/custom")
  ]);
  state.sampleMonitors = sampleMonitors;
  state.customScripts = customScripts;
  renderSamples();
}

async function loadWorkspace() {
  state.workspace = await api("/api/workspace");
  if (!$("scriptCwd").value.trim()) {
    $("scriptCwd").value = state.workspace.projectRoot;
  }
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

$("websiteSourceButton").addEventListener("click", () => setView("website"));
$("scriptSourceButton").addEventListener("click", () => setView("script"));
$("samplesViewButton").addEventListener("click", () => setView("samples"));
$("monitorsViewButton").addEventListener("click", () => setView("monitors"));
$("logsViewButton").addEventListener("click", () => setView("logs"));
$("destinationsViewButton").addEventListener("click", () => setView("destinations"));

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

async function runScriptPreview() {
  const config = scriptConfigFromForm();
  if (!config.command) {
    setStatus("Enter a command before running preview.");
    return;
  }
  setStatus("Running script preview...");
  const previousSelection = state.scriptSelection;
  state.scriptPreview = await api("/api/scripts/preview", {
    method: "POST",
    body: JSON.stringify(config)
  });
  state.scriptSelection = selectionWithPreviewNode(previousSelection, state.scriptPreview);
  setStatus(state.scriptPreview.ok ? "Preview ready. Select a value or item list." : `Preview failed: ${state.scriptPreview.error}`);
  renderSelection();
}

$("runScriptPreviewButton").addEventListener("click", runScriptPreview);

$("conditionType").addEventListener("change", updateConditionValueVisibility);

$("monitorForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setSaveStatus("Saving monitor...", "info");
  try {
    if (state.source === "website") {
      await saveWebsiteMonitor();
    } else {
      await saveScriptMonitor();
    }
  } catch (error) {
    setSaveStatus(`Save failed: ${error.message}`, "error");
  }
});

async function saveWebsiteMonitor() {
  if (!state.selection) {
    setStatus("Select a target in the browser before saving a monitor.");
    setSaveStatus("Select a target before saving.", "error");
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
      destinationIds: selectedDestinationIds(),
      agentInstructions: agentInstructionsFromForm(),
      enabled: true
    })
  });
  setStatus("Monitor saved.");
  setSaveStatus("Monitor saved. It now appears in Saved monitors.", "success");
  await refreshMonitors();
}

async function saveScriptMonitor() {
  if (!state.scriptSelection) {
    setStatus("Run preview and select script output before saving.");
    setSaveStatus("Run preview and select script output before saving.", "error");
    return;
  }
  if (state.scriptSelection.mode === "items" && !state.scriptSelection.idField) {
    setStatus("Choose an ID field before saving an item-list monitor.");
    setSaveStatus("Choose an ID field before saving.", "error");
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
      destinationIds: selectedDestinationIds(),
      agentInstructions: agentInstructionsFromForm(),
      enabled: true
    })
  });
  setStatus("Script monitor saved.");
  setSaveStatus("Monitor saved. It now appears in Saved monitors.", "success");
  await refreshMonitors();
}

$("refreshMonitorsButton").addEventListener("click", refreshMonitors);
$("refreshLogsButton").addEventListener("click", refreshLogs);
$("refreshSamplesButton").addEventListener("click", refreshSamples);
$("refreshDestinationsButton").addEventListener("click", refreshDestinations);

function bridgeCommand() {
  const port = Number($("destinationPort").value || 8765);
  const command = $("destinationAgentCommand").value.trim();
  return `python3 bridges/openpulse_agent_bridge.py --port ${port} --prompt-mode arg -- ${command}`;
}

function updateDestinationSetup() {
  const preset = $("destinationPreset").value;
  const isBridge = preset === "codex" || preset === "claude";
  const isWebhook = preset === "webhook";
  const isCommand = preset === "command";
  $("destinationPortRow").classList.toggle("hidden", !isBridge);
  $("destinationAgentCommandRow").classList.toggle("hidden", !isBridge);
  $("bridgeSetupPanel").classList.toggle("hidden", !isBridge);
  $("destinationEndpointRow").classList.toggle("hidden", isBridge);
  $("destinationArgsRow").classList.toggle("hidden", !isCommand);
  $("destinationCwdRow").classList.toggle("hidden", !isCommand);
  $("destinationSecret").parentElement.classList.toggle("hidden", isCommand);
  $("destinationEndpoint").placeholder = isWebhook ? "https://example.com/openpulse-events" : "python3";
  if (isBridge) {
    const defaults = AGENT_PRESETS[preset];
    if (!$("destinationName").value.trim() || Object.values(AGENT_PRESETS).some((item) => item.name === $("destinationName").value.trim())) {
      $("destinationName").value = defaults.name;
    }
    $("destinationPort").value = defaults.port;
    $("destinationAgentCommand").value = defaults.command;
    $("bridgeCommandPreview").textContent = bridgeCommand();
  }
}

$("destinationPreset").addEventListener("change", updateDestinationSetup);
$("destinationPort").addEventListener("input", () => {
  $("bridgeCommandPreview").textContent = bridgeCommand();
});
$("destinationAgentCommand").addEventListener("input", () => {
  $("bridgeCommandPreview").textContent = bridgeCommand();
});
$("copyBridgeCommandButton").addEventListener("click", async () => {
  const command = bridgeCommand();
  try {
    await navigator.clipboard.writeText(command);
    $("destinationStatus").textContent = "Bridge command copied.";
  } catch (_error) {
    $("destinationStatus").textContent = command;
  }
});

$("destinationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const preset = $("destinationPreset").value;
  const isBridge = preset === "codex" || preset === "claude";
  const type = preset === "command" ? "command" : "webhook";
  const port = Number($("destinationPort").value || AGENT_PRESETS[preset]?.port || 8765);
  const endpoint = isBridge ? `http://127.0.0.1:${port}` : $("destinationEndpoint").value.trim();
  if (!endpoint) {
    $("destinationStatus").textContent = type === "webhook" ? "Enter a webhook URL." : "Enter a command.";
    return;
  }
  const config = isBridge
    ? {
        url: endpoint,
        healthUrl: `${endpoint}/health`,
        secret: $("destinationSecret").value.trim() || undefined,
        timeoutSeconds: Number($("destinationTimeout").value || 120),
        agentKind: preset,
        bridgeCommand: bridgeCommand()
      }
    : type === "webhook"
      ? {
          url: endpoint,
          secret: $("destinationSecret").value.trim() || undefined,
          timeoutSeconds: Number($("destinationTimeout").value || 10)
        }
      : {
          command: endpoint,
          args: $("destinationArgs").value.split("\n").map((line) => line.trim()).filter(Boolean),
          cwd: $("destinationCwd").value.trim() || null,
          timeoutSeconds: Number($("destinationTimeout").value || 30)
        };
  await api("/api/destinations", {
    method: "POST",
    body: JSON.stringify({
      name: $("destinationName").value.trim() || (isBridge ? AGENT_PRESETS[preset].name : type === "webhook" ? "Webhook agent" : "Local agent"),
      type,
      config,
      enabled: true
    })
  });
  $("destinationStatus").textContent = "Destination added. Select it when saving a monitor.";
  $("destinationName").value = "";
  $("destinationEndpoint").value = "";
  $("destinationArgs").value = "";
  $("destinationCwd").value = "";
  $("destinationSecret").value = "";
  updateDestinationSetup();
  await refreshDestinations();
});

updateDestinationSetup();
await loadWorkspace();
setView("website");
renderSelection();
await refreshSamples();
await refreshDestinations();
await refreshMonitors();
await refreshLogs();
setInterval(refreshSelection, 1500);
setInterval(async () => {
  await refreshLogs();
  await refreshMonitors();
}, 5000);
