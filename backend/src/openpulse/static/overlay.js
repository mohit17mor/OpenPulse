(function () {
  if (window.OpenPulseOverlay) {
    return;
  }

  const CURRENCY_RE = /(?:[$€£₹¥]\s?\d|\d[\d,.]*\s?(?:USD|EUR|GBP|INR))/i;
  const NUMBER_RE = /^[-+]?\d[\d,]*(?:\.\d+)?(?:\s?[%x])?$/i;
  const STATUS_RE = /\b(in stock|out of stock|sold out|available|unavailable|only \d+ left|preorder|backorder|ships|delivery)\b/i;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "META", "LINK", "SVG", "PATH", "HEAD"]);
  const SKIP_LANDMARKS = new Set(["NAV", "FOOTER"]);

  let enabled = false;
  let candidates = [];
  let visibleCandidates = [];
  let layer = null;
  let box = null;
  let startPoint = null;
  let startCandidateId = null;

  function classifyText(text, element) {
    const value = text.trim();
    if (element?.tagName === "BUTTON" || element?.getAttribute("role") === "button") return "button";
    if (element?.tagName === "A") return "link";
    if (CURRENCY_RE.test(value)) return "price";
    if (STATUS_RE.test(value)) return "status";
    if (NUMBER_RE.test(value)) return "number";
    return "text";
  }

  function getCandidates() {
    const listCandidates = getListCandidates();
    const textCandidates = getTextCandidates();
    return [...listCandidates, ...textCandidates]
      .sort((a, b) => scoreCandidate(b) - scoreCandidate(a))
      .slice(0, 300);
  }

  function getTextCandidates() {
    const result = [];
    const seen = new Set();
    const elements = Array.from(document.body.querySelectorAll("body *"));

    for (const element of elements) {
      if (shouldSkipElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (!isUsefulText(text)) continue;
      if (hasUsefulChild(element)) continue;

      const rect = element.getBoundingClientRect();
      if (!isUsefulRect(rect)) continue;

      const key = `${Math.round(rect.x)}:${Math.round(rect.y)}:${text}`;
      if (seen.has(key)) continue;
      seen.add(key);

      result.push({
        id: `op-text-${result.length + 1}`,
        text,
        semanticType: classifyText(text, element),
        selector: buildSelector(element),
        domPath: buildDomPath(element),
        nearbyText: nearbyText(element),
        boundingBox: {
          x: Math.round(rect.x + window.scrollX),
          y: Math.round(rect.y + window.scrollY),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      });
    }

    return result;
  }

  function getListCandidates() {
    const result = [];
    const seen = new Set();
    const parents = Array.from(document.body.querySelectorAll("body *"));

    for (const parent of parents) {
      if (shouldSkipElement(parent)) continue;
      if (isContentItemElement(parent)) continue;
      if (isNestedInsideContentItem(parent)) continue;
      const groups = new Map();
      for (const child of Array.from(parent.children || [])) {
        if (shouldSkipElement(child)) continue;
        const text = normalizeText(child.innerText || child.textContent || "");
        if (!isUsefulListItemText(text)) continue;
        const rect = child.getBoundingClientRect();
        if (!isUsefulRect(rect)) continue;
        const key = listGroupKey(child);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(child);
      }

      for (const group of groups.values()) {
        if (group.length < 2) continue;
        const items = group.map((element, index) => extractVisibleItem(element, index)).filter((item) => item.id);
        if (items.length < 2) continue;
        const selector = buildSelector(parent);
        const itemSelector = `:scope > ${childSelector(group[0])}`;
        const duplicateKey = `${selector}:${itemSelector}`;
        if (seen.has(duplicateKey)) continue;
        seen.add(duplicateKey);

        const boundingBox = unionBoundingBox(group.map((element) => element.getBoundingClientRect()));
        if (!boundingBox || boundingBox.width < 80 || boundingBox.height < 40) continue;
        result.push({
          id: `op-list-${result.length + 1}`,
          text: `${items.length} visible items`,
          semanticType: "item_list",
          selector,
          itemSelector,
          domPath: buildDomPath(parent),
          nearbyText: nearbyText(parent),
          items,
          boundingBox: {
            x: Math.round(boundingBox.x + window.scrollX),
            y: Math.round(boundingBox.y + window.scrollY),
            width: Math.round(boundingBox.width),
            height: Math.round(boundingBox.height)
          }
        });
      }
    }

    return result.slice(0, 30);
  }

  function shouldSkipElement(element) {
    if (SKIP_TAGS.has(element.tagName) || SKIP_LANDMARKS.has(element.tagName)) return true;
    if (element.closest("[data-openpulse-overlay='true']")) return true;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return true;
    if (element.getAttribute("aria-hidden") === "true") return true;
    return false;
  }

  function isNestedInsideContentItem(element) {
    const item = element.closest("article,[role='article']");
    return item && item !== element;
  }

  function isContentItemElement(element) {
    return element.tagName === "ARTICLE" || element.getAttribute("role") === "article";
  }

  function hasUsefulChild(element) {
    return Array.from(element.children).some((child) => {
      if (shouldSkipElement(child)) return false;
      const text = normalizeText(child.innerText || child.textContent || "");
      return isUsefulText(text) && isUsefulRect(child.getBoundingClientRect());
    });
  }

  function normalizeText(text) {
    return text.replace(/\s+/g, " ").trim();
  }

  function isUsefulText(text) {
    if (!text || text.length > 140) return false;
    if (/^(home|menu|search|close)$/i.test(text)) return false;
    return /[a-zA-Z0-9$€£₹¥]/.test(text);
  }

  function isUsefulListItemText(text) {
    if (!text || text.length < 8 || text.length > 1200) return false;
    return /[a-zA-Z0-9$€£₹¥]/.test(text);
  }

  function isUsefulRect(rect) {
    return rect.width >= 12 && rect.height >= 10 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  }

  function scoreCandidate(candidate) {
    if (candidate.semanticType === "item_list") {
      const countScore = Math.min(70, (candidate.items?.length || 0) * 8);
      const area = Math.min(24, (candidate.boundingBox.width * candidate.boundingBox.height) / 8000);
      return 80 + countScore + area;
    }
    const typeScore = { price: 50, status: 40, number: 30, button: 20, link: 12, text: 10 }[candidate.semanticType] || 0;
    const area = Math.min(30, (candidate.boundingBox.width * candidate.boundingBox.height) / 800);
    const lengthScore = Math.max(0, 24 - Math.abs(candidate.text.length - 18));
    return typeScore + area + lengthScore;
  }

  function listGroupKey(element) {
    const role = element.getAttribute("role") || "";
    return `${element.tagName}:${role}`;
  }

  function childSelector(element) {
    return element.tagName.toLowerCase();
  }

  function extractVisibleItem(element, index) {
    const link = element.matches("a[href]") ? element : element.querySelector("a[href]");
    const url = absoluteUrl(link?.getAttribute("href") || "");
    const titleElement = element.querySelector("h1,h2,h3,[role='heading'],a[href]");
    const title = normalizeText(titleElement?.innerText || titleElement?.textContent || "").slice(0, 180);
    const text = normalizeText(element.innerText || element.textContent || "").slice(0, 500);
    const attrId = element.getAttribute("data-id") || element.getAttribute("data-testid") || element.id || "";
    const id = url || attrId || title || text || `item-${index + 1}`;
    return {
      id: String(id),
      item: {
        id: String(id),
        title: title || text.slice(0, 120) || String(id),
        url,
        text,
        index: index + 1
      }
    };
  }

  function absoluteUrl(href) {
    if (!href) return "";
    try {
      return new URL(href, window.location.href).href;
    } catch (_error) {
      return href;
    }
  }

  function unionBoundingBox(rects) {
    const usefulRects = rects.filter(isUsefulRect);
    if (usefulRects.length === 0) return null;
    const left = Math.min(...usefulRects.map((rect) => rect.left));
    const top = Math.min(...usefulRects.map((rect) => rect.top));
    const right = Math.max(...usefulRects.map((rect) => rect.right));
    const bottom = Math.max(...usefulRects.map((rect) => rect.bottom));
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  function buildSelector(element) {
    if (element.id) {
      return `#${cssEscape(element.id)}`;
    }
    const attr = ["data-testid", "data-test", "aria-label", "name"].find((name) => element.getAttribute(name));
    if (attr) {
      return `${element.tagName.toLowerCase()}[${attr}="${cssEscape(element.getAttribute(attr))}"]`;
    }
    return buildDomPath(element);
  }

  function buildDomPath(element) {
    const parts = [];
    let node = element;
    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document) {
      const tag = node.tagName.toLowerCase();
      if (tag === "html") {
        parts.unshift("html");
        break;
      }
      const siblings = Array.from(node.parentElement?.children || []).filter((child) => child.tagName === node.tagName);
      const index = siblings.indexOf(node) + 1;
      parts.unshift(`${tag}:nth-of-type(${index})`);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function nearbyText(element) {
    const parent = element.parentElement;
    if (!parent) return "";
    return normalizeText(parent.innerText || parent.textContent || "").slice(0, 220);
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    notifyMonitorModeEnabled();
    candidates = getCandidates();
    visibleCandidates = candidates;
    render();
  }

  function disable() {
    enabled = false;
    candidates = [];
    visibleCandidates = [];
    startPoint = null;
    startCandidateId = null;
    layer?.remove();
    layer = null;
    box = null;
  }

  function render() {
    layer?.remove();
    layer = document.createElement("div");
    layer.dataset.openpulseOverlay = "true";
    layer.style.cssText = [
      "position:fixed",
      "inset:0",
      "z-index:2147483647",
      "cursor:crosshair",
      "font-family:Inter,system-ui,sans-serif",
      "pointer-events:auto"
    ].join(";");

    const banner = document.createElement("div");
    banner.textContent = "OpenPulse monitor mode: click a highlighted fact or repeated list. Drag a rectangle to narrow candidates. Press M to exit.";
    banner.style.cssText = [
      "position:fixed",
      "top:12px",
      "left:50%",
      "transform:translateX(-50%)",
      "background:#172026",
      "color:#fff",
      "padding:9px 12px",
      "border-radius:6px",
      "box-shadow:0 6px 18px rgba(0,0,0,.22)",
      "font-size:13px",
      "pointer-events:none"
    ].join(";");
    layer.appendChild(banner);

    for (const candidate of visibleCandidates) {
      const highlight = document.createElement("button");
      highlight.type = "button";
      highlight.dataset.candidateId = candidate.id;
      const rect = viewportRect(candidate.boundingBox);
      highlight.title = `${candidate.semanticType}: ${candidate.text}`;
      const isList = candidate.semanticType === "item_list";
      highlight.style.cssText = [
        "position:fixed",
        `left:${rect.x}px`,
        `top:${rect.y}px`,
        `width:${rect.width}px`,
        `height:${rect.height}px`,
        `border:2px ${isList ? "dashed" : "solid"} ${isList ? "#4f46e5" : "#17a398"}`,
        `background:${isList ? "rgba(79,70,229,.08)" : "rgba(23,163,152,.13)"}`,
        "box-shadow:0 0 0 1px rgba(255,255,255,.85)",
        "border-radius:4px",
        "padding:0",
        "margin:0",
        "pointer-events:auto"
      ].join(";");
      if (isList) {
        const label = document.createElement("span");
        label.textContent = `${candidate.items?.length || 0} items`;
        label.style.cssText = [
          "position:absolute",
          "top:0",
          "left:0",
          "transform:translateY(-100%)",
          "background:#4f46e5",
          "color:#fff",
          "font-size:11px",
          "font-weight:700",
          "line-height:1",
          "padding:5px 7px",
          "border-radius:4px 4px 0 0",
          "pointer-events:none"
        ].join(";");
        highlight.appendChild(label);
      }
      layer.appendChild(highlight);
    }

    box = document.createElement("div");
    box.style.cssText = [
      "position:fixed",
      "display:none",
      "border:2px dashed #db7c00",
      "background:rgba(219,124,0,.12)",
      "pointer-events:none"
    ].join(";");
    layer.appendChild(box);

    layer.addEventListener("mousedown", onMouseDown);
    layer.addEventListener("mousemove", onMouseMove);
    layer.addEventListener("mouseup", onMouseUp);
    document.body.appendChild(layer);
  }

  function viewportRect(boundingBox) {
    return {
      x: boundingBox.x - window.scrollX,
      y: boundingBox.y - window.scrollY,
      width: boundingBox.width,
      height: boundingBox.height
    };
  }

  function onMouseDown(event) {
    event.preventDefault();
    startPoint = { x: event.clientX, y: event.clientY };
    startCandidateId = event.target?.dataset?.candidateId || null;
  }

  function onMouseMove(event) {
    if (!startPoint || !box) return;
    const rect = rectFromPoints(startPoint, { x: event.clientX, y: event.clientY });
    if (rect.width < 4 && rect.height < 4) return;
    box.style.display = "block";
    box.style.left = `${rect.x}px`;
    box.style.top = `${rect.y}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
  }

  async function onMouseUp(event) {
    if (!startPoint) return;
    const rect = rectFromPoints(startPoint, { x: event.clientX, y: event.clientY });
    const wasClick = rect.width < 6 && rect.height < 6;
    const candidateId = event.target?.dataset?.candidateId || startCandidateId;
    startPoint = null;
    startCandidateId = null;

    if (box) box.style.display = "none";

    if (wasClick && candidateId) {
      await selectCandidate(candidateId);
      return;
    }

    if (!wasClick) {
      const documentRect = {
        x: rect.x + window.scrollX,
        y: rect.y + window.scrollY,
        width: rect.width,
        height: rect.height
      };
      visibleCandidates = filterCandidatesByRect(candidates, documentRect);
      render();
    }
  }

  function rectFromPoints(a, b) {
    const x = Math.min(a.x, b.x);
    const y = Math.min(a.y, b.y);
    return {
      x,
      y,
      width: Math.abs(a.x - b.x),
      height: Math.abs(a.y - b.y)
    };
  }

  function filterCandidatesByRect(source, rect) {
    return source.filter((candidate) => intersects(candidate.boundingBox, rect));
  }

  function intersects(a, b) {
    return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  }

  async function selectCandidate(candidateId) {
    const candidate = candidates.find((item) => item.id === candidateId);
    if (!candidate) return;
    if (candidate.semanticType === "item_list") {
      await selectItemList(candidate);
      return;
    }
    const selection = {
      url: window.location.href,
      semanticType: candidate.semanticType,
      initialValue: candidate.text,
      selector: candidate.selector,
      domPath: candidate.domPath,
      nearbyText: candidate.nearbyText,
      boundingBox: candidate.boundingBox,
      selectedAt: new Date().toISOString()
    };
    if (window.openPulseSelectCandidate) {
      await window.openPulseSelectCandidate(selection);
    }
    disable();
  }

  async function selectItemList(candidate) {
    const selection = {
      sourceType: "website",
      mode: "items",
      url: window.location.href,
      semanticType: "item_list",
      selector: candidate.selector,
      itemSelector: candidate.itemSelector,
      domPath: candidate.domPath,
      nearbyText: candidate.nearbyText,
      boundingBox: candidate.boundingBox,
      selection: {
        mode: "items",
        idField: "id",
        displayField: "title",
        urlField: "url"
      },
      items: candidate.items || [],
      selectedAt: new Date().toISOString()
    };
    if (window.openPulseSelectCandidate) {
      await window.openPulseSelectCandidate(selection);
    }
    disable();
  }

  function notifyMonitorModeEnabled() {
    if (!window.openPulseMonitorModeEnabled) return;
    try {
      const result = window.openPulseMonitorModeEnabled({
        url: window.location.href,
        enabledAt: new Date().toISOString()
      });
      if (result?.catch) result.catch(() => {});
    } catch (_error) {
      // Monitor mode can still work if the app binding is unavailable.
    }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key.toLowerCase() !== "m" || event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = document.activeElement?.tagName;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
    if (enabled) disable();
    else enable();
  });

  window.OpenPulseOverlay = {
    enable,
    disable,
    getCandidates,
    filterCandidatesByRect
  };
})();
