// Scanning tab (loaded after app.js — reuses its escapeHtml and printerSelect).

const tabs = document.querySelectorAll(".tab");
const scannerSelect = document.getElementById("scanner-select");
const scanArea = document.getElementById("scan-area");
const scanMode = document.getElementById("scan-mode");
const scanResolution = document.getElementById("scan-resolution");
const scanBrightness = document.getElementById("scan-brightness");
const scanContrast = document.getElementById("scan-contrast");
const scanFormat = document.getElementById("scan-format");
const scanEstimate = document.getElementById("scan-estimate");
const scanPreviewButton = document.getElementById("scan-preview-button");
const scanButton = document.getElementById("scan-button");
const scanMessage = document.getElementById("scan-message");
const scanBed = document.getElementById("scan-bed");
const scanPreviewImg = document.getElementById("scan-preview-img");
const scanBedHint = document.getElementById("scan-bed-hint");
const scanSelection = document.getElementById("scan-selection");
const scanList = document.getElementById("scan-list");
const scanMergeButton = document.getElementById("scan-merge");
const scanRefreshButton = document.getElementById("scan-refresh");

const MIN_AREA_MM = 5;

let bedMm = [215.9, 297];
let selection = null; // {x, y, w, h} in mm on the glass, null = whole glass
let scanBusy = false;
let scannersLoaded = false;
let checkedScans = []; // names in the order they were ticked (= PDF page order)

// --- Remembered scan settings ------------------------------------------------
// Scanner, area preset, mode, resolution, brightness/contrast and format —
// getCookie/setCookie/getJsonCookie come from app.js, loaded before this file.
// A custom hand-drawn area isn't remembered (only named presets / "whole
// glass"), since the physical document position differs scan to scan.

const SCAN_PREFS_COOKIE = "scan_prefs";
const scanPrefs = getJsonCookie(SCAN_PREFS_COOKIE, {
  scanner: null, area: "", mode: null, resolution: null, format: "jpeg", brightness: 0, contrast: 0,
});

function saveScanPrefs() {
  setCookie(SCAN_PREFS_COOKIE, JSON.stringify(scanPrefs));
}

scanFormat.value = scanPrefs.format || "jpeg";
scanBrightness.value = scanPrefs.brightness || 0;
scanContrast.value = scanPrefs.contrast || 0;
document.getElementById("scan-brightness-value").value = scanBrightness.value;
document.getElementById("scan-contrast-value").value = scanContrast.value;

// --- Tabs ------------------------------------------------------------------

function showTab(name) {
  tabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
  document.getElementById("tab-print").hidden = name !== "print";
  document.getElementById("tab-scan").hidden = name !== "scan";
  if (name === "scan" && !scannersLoaded) {
    scannersLoaded = true;
    loadScanners();
    loadScans();
  }
}

tabs.forEach((tab) =>
  tab.addEventListener("click", () => {
    history.replaceState(null, "", tab.dataset.tab === "scan" ? "#scan" : "#");
    showTab(tab.dataset.tab);
  })
);

// --- Scanner & capabilities -------------------------------------------------

function setScanMessage(text, kind = "") {
  scanMessage.textContent = text;
  scanMessage.className = `message ${kind}`;
}

function setScanBusy(busy) {
  scanBusy = busy;
  const ready = !busy && scannerSelect.value;
  scanPreviewButton.disabled = !ready;
  scanButton.disabled = !ready;
}

async function loadScanners() {
  setScanMessage(t("searching_scanners"));
  try {
    const res = await fetch("/api/scanners");
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("scanners_failed")));
    scannerSelect.innerHTML = "";
    if (data.length === 0) {
      scannerSelect.innerHTML = `<option value=''>${escapeHtml(t("no_scanners"))}</option>`;
      setScanMessage(t("scanner_missing"), "error");
      setScanBusy(false); // no scanner selected — keep Preview/Scan disabled
      return;
    }
    data.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      scannerSelect.appendChild(opt);
    });
    if (scanPrefs.scanner && data.some((s) => s.id === scanPrefs.scanner)) {
      scannerSelect.value = scanPrefs.scanner;
    }
    setScanMessage("");
    await loadCapabilities();
  } catch (err) {
    setScanMessage(err.message, "error");
    setScanBusy(false); // failed to even list scanners — nothing to scan with
  }
}

function fillSelect(select, items, selected) {
  select.innerHTML = "";
  items.forEach(({ value, label }) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (String(value) === String(selected)) opt.selected = true;
    select.appendChild(opt);
  });
}

async function loadCapabilities() {
  setScanBusy(true);
  try {
    const res = await fetch(`/api/scanners/capabilities?scanner_id=${encodeURIComponent(scannerSelect.value)}`);
    const caps = await res.json();
    if (!res.ok) throw new Error(caps.detail || t("caps_failed"));

    bedMm = caps.bed_mm;
    scanBed.style.aspectRatio = `${bedMm[0]} / ${bedMm[1]}`;
    const preferredMode = caps.modes.some((m) => m.value === scanPrefs.mode) ? scanPrefs.mode : "color";
    fillSelect(scanMode, caps.modes, preferredMode);
    const defaultDpi = caps.resolutions.includes(300) ? 300 : caps.resolutions[Math.floor(caps.resolutions.length / 2)];
    const preferredDpi = caps.resolutions.includes(Number(scanPrefs.resolution)) ? scanPrefs.resolution : defaultDpi;
    fillSelect(scanResolution, caps.resolutions.map((r) => ({ value: r, label: `${r} dpi` })), preferredDpi);
    document.getElementById("scan-brightness-row").hidden = !caps.brightness;
    document.getElementById("scan-contrast-row").hidden = !caps.contrast;
    if ([...scanArea.options].some((o) => o.value === scanPrefs.area)) {
      scanArea.value = scanPrefs.area;
      scanArea.dispatchEvent(new Event("change"));
    }
    updateEstimate();
  } catch (err) {
    setScanMessage(err.message, "error");
  } finally {
    setScanBusy(false);
  }
}

scannerSelect.addEventListener("change", loadCapabilities);
scannerSelect.addEventListener("change", () => {
  scanPrefs.scanner = scannerSelect.value;
  saveScanPrefs();
});

// --- Area selection ----------------------------------------------------------

function clampSelection(sel) {
  const w = Math.min(sel.w, bedMm[0]);
  const h = Math.min(sel.h, bedMm[1]);
  return {
    x: Math.min(Math.max(sel.x, 0), bedMm[0] - w),
    y: Math.min(Math.max(sel.y, 0), bedMm[1] - h),
    w,
    h,
  };
}

function renderSelection() {
  if (!selection) {
    scanSelection.hidden = true;
  } else {
    scanSelection.hidden = false;
    scanSelection.style.left = `${(selection.x / bedMm[0]) * 100}%`;
    scanSelection.style.top = `${(selection.y / bedMm[1]) * 100}%`;
    scanSelection.style.width = `${(selection.w / bedMm[0]) * 100}%`;
    scanSelection.style.height = `${(selection.h / bedMm[1]) * 100}%`;
  }
  updateEstimate();
}

function pointToMm(e) {
  const rect = scanBed.getBoundingClientRect();
  return {
    x: Math.min(Math.max(((e.clientX - rect.left) / rect.width) * bedMm[0], 0), bedMm[0]),
    y: Math.min(Math.max(((e.clientY - rect.top) / rect.height) * bedMm[1], 0), bedMm[1]),
  };
}

let drag = null; // {kind: "draw"|"move", start: {x,y}, origin: selection}

scanBed.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  scanBed.setPointerCapture(e.pointerId);
  const start = pointToMm(e);
  drag = e.target === scanSelection
    ? { kind: "move", start, origin: { ...selection } }
    : { kind: "draw", start, origin: null };
});

scanBed.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const p = pointToMm(e);
  if (drag.kind === "move") {
    selection = clampSelection({
      ...drag.origin,
      x: drag.origin.x + (p.x - drag.start.x),
      y: drag.origin.y + (p.y - drag.start.y),
    });
  } else {
    selection = {
      x: Math.min(drag.start.x, p.x),
      y: Math.min(drag.start.y, p.y),
      w: Math.abs(p.x - drag.start.x),
      h: Math.abs(p.y - drag.start.y),
    };
  }
  renderSelection();
});

scanBed.addEventListener("pointerup", () => {
  if (!drag) return;
  if (drag.kind === "draw") {
    if (!selection || selection.w < MIN_AREA_MM || selection.h < MIN_AREA_MM) {
      selection = null; // a click (or tiny box) resets to the whole glass
      scanArea.value = "";
    } else {
      scanArea.querySelector('option[value="custom"]').hidden = false;
      scanArea.value = "custom";
    }
  }
  drag = null;
  renderSelection();
});

scanArea.addEventListener("change", () => {
  if (!scanArea.value) {
    selection = null;
  } else if (scanArea.value !== "custom") {
    // Documents are placed in the glass corner, so presets start at 0,0.
    const [w, h] = scanArea.value.split("x").map(Number);
    selection = clampSelection({ x: 0, y: 0, w, h });
  }
  renderSelection();
  if (scanArea.value !== "custom") {
    scanPrefs.area = scanArea.value;
    saveScanPrefs();
  }
});

// --- Estimate ------------------------------------------------------------------

function updateEstimate() {
  const dpi = Number(scanResolution.value) || 300;
  const w = selection ? selection.w : bedMm[0];
  const h = selection ? selection.h : bedMm[1];
  const px = (mm) => Math.round((mm / 25.4) * dpi);
  const bytesPerPixel = { color: 3, gray: 1, lineart: 1 / 8 }[scanMode.value] || 3;
  const rawMb = (px(w) * px(h) * bytesPerPixel) / 1024 / 1024;
  scanEstimate.textContent = t("estimate", { w: Math.round(w), h: Math.round(h), pw: px(w), ph: px(h) }) +
    (rawMb > 1 ? t("estimate_raw", { mb: rawMb.toFixed(0) }) : "");
}

[scanResolution, scanMode].forEach((el) => el.addEventListener("change", updateEstimate));
[[scanBrightness, "scan-brightness-value"], [scanContrast, "scan-contrast-value"]].forEach(([input, out]) =>
  input.addEventListener("input", () => (document.getElementById(out).value = input.value))
);

scanMode.addEventListener("change", () => {
  scanPrefs.mode = scanMode.value;
  saveScanPrefs();
});
scanResolution.addEventListener("change", () => {
  scanPrefs.resolution = scanResolution.value;
  saveScanPrefs();
});
scanFormat.addEventListener("change", () => {
  scanPrefs.format = scanFormat.value;
  saveScanPrefs();
});
[[scanBrightness, "brightness"], [scanContrast, "contrast"]].forEach(([input, key]) =>
  input.addEventListener("change", () => {
    scanPrefs[key] = Number(input.value) || 0;
    saveScanPrefs();
  })
);

// --- Preview & scan -----------------------------------------------------------

scanPreviewButton.addEventListener("click", async () => {
  setScanBusy(true);
  scanBed.classList.add("loading");
  setScanMessage(t("scan_previewing"));
  const form = new FormData();
  form.append("scanner_id", scannerSelect.value);
  try {
    const res = await fetch("/api/scan/preview", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("preview_error")));
    scanPreviewImg.src = data.image;
    scanPreviewImg.hidden = false;
    scanBedHint.hidden = true;
    setScanMessage("");
  } catch (err) {
    setScanMessage(err.message, "error");
  } finally {
    scanBed.classList.remove("loading");
    setScanBusy(false);
  }
});

scanButton.addEventListener("click", async () => {
  setScanBusy(true);
  setScanMessage(t("scanning"));
  const form = new FormData();
  form.append("scanner_id", scannerSelect.value);
  form.append("resolution", scanResolution.value);
  form.append("mode", scanMode.value);
  form.append("brightness", scanBrightness.value);
  form.append("contrast", scanContrast.value);
  form.append("format", scanFormat.value);
  if (selection) {
    form.append("x", selection.x.toFixed(2));
    form.append("y", selection.y.toFixed(2));
    form.append("width", selection.w.toFixed(2));
    form.append("height", selection.h.toFixed(2));
  }
  try {
    const res = await fetch("/api/scan", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("scan_error")));
    setScanMessage(t("scan_done", { name: data.name }), "success");
    loadScans();
  } catch (err) {
    setScanMessage(err.message, "error");
  } finally {
    setScanBusy(false);
  }
});

// --- Scan list ------------------------------------------------------------------

function formatSize(bytes) {
  if (bytes >= 1024 * 1024) return t("mb", { n: (bytes / 1024 / 1024).toFixed(1) });
  return t("kb", { n: Math.max(1, Math.round(bytes / 1024)) });
}

function renderScans(scans) {
  checkedScans = checkedScans.filter((n) => scans.some((s) => s.name === n));
  scanMergeButton.disabled = checkedScans.length === 0;
  if (scans.length === 0) {
    scanList.innerHTML = `<p class="empty">${escapeHtml(t("no_scans"))}</p>`;
    return;
  }
  scanList.innerHTML = scans
    .map((s) => {
      const url = `/api/scans/${encodeURIComponent(s.name)}`;
      const order = checkedScans.indexOf(s.name);
      const details = s.pages > 1
        ? t("pages_n", { n: s.pages })
        : s.width_px ? `${s.width_px}×${s.height_px}, ${s.dpi} dpi` : "";
      return `<div class="scan-item${order >= 0 ? " selected" : ""}" data-name="${escapeHtml(s.name)}">
        <label class="scan-check" title="${escapeHtml(t("select_for_pdf"))}">
          <input type="checkbox" ${order >= 0 ? "checked" : ""} />${order >= 0 ? `&nbsp;${order + 1}` : ""}
        </label>
        <a class="scan-thumb" href="${url}?inline=true" target="_blank" rel="noopener">
          <img src="${url}/thumb?v=${encodeURIComponent(s.created)}" alt="" loading="lazy" />
        </a>
        <div class="scan-meta">
          <strong title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</strong>
          ${s.format.toUpperCase()} · ${formatSize(s.size)}${details ? ` · ${details}` : ""}
        </div>
        <div class="scan-actions">
          <a class="ghost-button" href="${url}" download>${escapeHtml(t("download"))}</a>
          <button type="button" class="ghost-button" data-action="print" title="${escapeHtml(t("print_actual_size"))}">${escapeHtml(t("print"))}</button>
          <button type="button" class="ghost-button danger" data-action="delete">${escapeHtml(t("delete"))}</button>
        </div>
      </div>`;
    })
    .join("");
}

async function loadScans() {
  try {
    const res = await fetch("/api/scans");
    if (!res.ok) throw new Error();
    renderScans(await res.json());
  } catch (err) {
    // non-critical
  }
}

scanList.addEventListener("change", (e) => {
  if (e.target.type !== "checkbox") return;
  const name = e.target.closest(".scan-item").dataset.name;
  checkedScans = checkedScans.filter((n) => n !== name);
  if (e.target.checked) checkedScans.push(name);
  loadScans();
});

scanList.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  const name = button.closest(".scan-item").dataset.name;
  const url = `/api/scans/${encodeURIComponent(name)}`;

  if (button.dataset.action === "delete") {
    if (!confirm(t("confirm_delete", { name }))) return;
    await fetch(url, { method: "DELETE" });
    loadScans();
    return;
  }

  if (button.dataset.action === "print") {
    const printer = printerSelect.value;
    if (!confirm(t("confirm_print_scan", { name, printer }))) return;
    const form = new FormData();
    form.append("printer", printer);
    button.disabled = true;
    try {
      const res = await fetch(`${url}/print`, { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(errorDetail(data, t("print_error")));
      if (data.status === "failed") throw new Error(data.error);
      setScanMessage(t("scan_printed", { name }), "success");
    } catch (err) {
      setScanMessage(err.message, "error");
    } finally {
      button.disabled = false;
    }
  }
});

scanMergeButton.addEventListener("click", async () => {
  scanMergeButton.disabled = true;
  try {
    const res = await fetch("/api/scans/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names: checkedScans }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("merge_error")));
    checkedScans = [];
    setScanMessage(t("merged", { name: data.name, pages: data.pages }), "success");
  } catch (err) {
    setScanMessage(err.message, "error");
  } finally {
    loadScans();
  }
});

scanRefreshButton.addEventListener("click", loadScans);

showTab(location.hash === "#scan" ? "scan" : "print");
