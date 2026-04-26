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
        id: `op-${result.length + 1}`,
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

    return result
      .sort((a, b) => scoreCandidate(b) - scoreCandidate(a))
      .slice(0, 250);
  }

  function shouldSkipElement(element) {
    if (SKIP_TAGS.has(element.tagName) || SKIP_LANDMARKS.has(element.tagName)) return true;
    if (element.closest("[data-openpulse-overlay='true']")) return true;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return true;
    if (element.getAttribute("aria-hidden") === "true") return true;
    return false;
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

  function isUsefulRect(rect) {
    return rect.width >= 12 && rect.height >= 10 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  }

  function scoreCandidate(candidate) {
    const typeScore = { price: 50, status: 40, number: 30, button: 20, link: 12, text: 10 }[candidate.semanticType] || 0;
    const area = Math.min(30, (candidate.boundingBox.width * candidate.boundingBox.height) / 800);
    const lengthScore = Math.max(0, 24 - Math.abs(candidate.text.length - 18));
    return typeScore + area + lengthScore;
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
    banner.textContent = "OpenPulse monitor mode: click a highlighted fact, or drag a rectangle to narrow candidates. Press M to exit.";
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
      highlight.style.cssText = [
        "position:fixed",
        `left:${rect.x}px`,
        `top:${rect.y}px`,
        `width:${rect.width}px`,
        `height:${rect.height}px`,
        "border:2px solid #17a398",
        "background:rgba(23,163,152,.13)",
        "box-shadow:0 0 0 1px rgba(255,255,255,.85)",
        "border-radius:4px",
        "padding:0",
        "margin:0",
        "pointer-events:auto"
      ].join(";");
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

