// --- i18n ------------------------------------------------------------------
// Messages come from the server (app/i18n.py, keys "web.*") in the page's
// language; t("key", {name: ...}) fills {name} placeholders.

function t(key, vars = {}) {
  const text = (window.I18N || {})[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? vars[name] : match));
}

// API calls carry the page's language, so server-side labels and errors
// match the page even when it was opened with ?lang=.
// A 401 means the session expired (or the password changed): go to the
// login page and come back here afterwards.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (url, options = {}) => {
  const headers = new Headers(options.headers || {});
  headers.set("X-Lang", document.documentElement.lang);
  const response = await nativeFetch(url, { ...options, headers });
  if (response.status === 401) {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`;
  }
  return response;
};

// The choice is remembered in a cookie (read by the server to render the
// page and its messages in that language) for a year.
document.querySelectorAll(".lang-button").forEach((button) =>
  button.addEventListener("click", () => {
    document.cookie = `lang=${button.dataset.lang}; path=/; max-age=31536000; samesite=lax`;
    const url = new URL(location.href);
    if (url.searchParams.has("lang")) {
      // An explicit ?lang= would override the new choice — drop it. A changed
      // query string makes this a real navigation, so the page reloads.
      url.searchParams.delete("lang");
      location.replace(url);
    } else {
      // Same URL (possibly with a #tab): replace() would only jump to the
      // anchor without reloading, so reload explicitly.
      location.reload();
    }
  })
);

// --- Cookie helpers (shared with scan.js) -----------------------------------
// Print/scan settings are remembered per browser for a year, the same way
// as the language choice above — no server-side storage involved.

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

function setCookie(name, value) {
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=31536000; samesite=lax`;
}

function getJsonCookie(name, fallback) {
  try {
    const raw = getCookie(name);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

// FastAPI's `detail` is a plain string for our own errors, but an array of
// {msg, loc, ...} objects for its own request-validation (422) failures —
// e.g. a required field sent empty. `new Error(anArray)` stringifies it via
// Array.prototype.toString, which is "[object Object]" for each element.
function errorDetail(data, fallback) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((e) => (e && typeof e === "object" ? e.msg || JSON.stringify(e) : String(e))).join("; ");
  }
  return fallback;
}

const dropzone = document.getElementById("dropzone");
const dropzoneText = document.getElementById("dropzone-text");
const fileInput = document.getElementById("file-input");
const printerSelect = document.getElementById("printer-select");
const copiesInput = document.getElementById("copies-input");
const printerOptionsContainer = document.getElementById("printer-options");
const printButton = document.getElementById("print-button");
const form = document.getElementById("print-form");
const formMessage = document.getElementById("form-message");
const jobsBody = document.getElementById("jobs-body");
const refreshJobsButton = document.getElementById("refresh-jobs");
const previewButton = document.getElementById("preview-button");
const previewPanel = document.getElementById("preview-panel");
const previewInfo = document.getElementById("preview-info");
const previewPages = document.getElementById("preview-pages");
const previewClose = document.getElementById("preview-close");

// --- Remembered print settings ---------------------------------------------
// Printer, copies and each printer's own last-used options (keyed by printer
// name, since option keys differ between drivers). A saved value that no
// longer matches the current driver's choices is silently ignored — the
// browser just leaves the <select> on its first option.

const PRINT_PREFS_COOKIE = "print_prefs";
const printPrefs = getJsonCookie(PRINT_PREFS_COOKIE, { printer: null, copies: 1, options: {} });

function savePrintPrefs() {
  setCookie(PRINT_PREFS_COOKIE, JSON.stringify(printPrefs));
}

const STATUS_LABELS = {
  queued: t("status_queued"),
  sent: t("status_sent"),
  failed: t("status_failed"),
};

function updateDropzoneLabel() {
  const file = fileInput.files[0];
  dropzoneText.textContent = file ? file.name : t("dropzone");
  printButton.disabled = !file || printerSelect.options.length === 0;
  previewButton.disabled = printButton.disabled;
  if (!file) hidePreview();
}

dropzone.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  updateDropzoneLabel();
  schedulePreview();
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);

["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);

dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) {
    fileInput.files = e.dataTransfer.files;
    updateDropzoneLabel();
    schedulePreview();
  }
});

async function loadPrinters() {
  try {
    const res = await fetch("/api/printers");
    if (!res.ok) throw new Error(await res.text());
    const printers = await res.json();
    printerSelect.innerHTML = "";
    if (printers.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = t("no_printers");
      printerSelect.appendChild(opt);
    } else {
      printers.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.is_default ? t("default_suffix", { name: p.name }) : p.name;
        if (p.is_default) opt.selected = true;
        printerSelect.appendChild(opt);
      });
      // A remembered printer wins over the OS default, if it's still there.
      if (printPrefs.printer && printers.some((p) => p.name === printPrefs.printer)) {
        printerSelect.value = printPrefs.printer;
      }
    }
  } catch (err) {
    formMessage.textContent = t("printers_failed");
    formMessage.className = "message error";
  }
  copiesInput.value = printPrefs.copies || 1;
  updateDropzoneLabel();
  loadPrinterOptions();
}

async function loadPrinterOptions() {
  printerOptionsContainer.innerHTML = "";
  const printer = printerSelect.value;
  if (!printer) return;

  try {
    const res = await fetch(`/api/printers/${encodeURIComponent(printer)}/options`);
    if (!res.ok) throw new Error(await res.text());
    const options = await res.json();
    const savedOptions = printPrefs.options[printer] || {};
    options.forEach((opt) => {
      const row = document.createElement("div");
      row.className = "form-row";

      const label = document.createElement("label");
      label.textContent = opt.label;
      label.setAttribute("for", `opt-${opt.key}`);

      const select = document.createElement("select");
      select.id = `opt-${opt.key}`;
      select.dataset.optionKey = opt.key;
      const preferred = opt.key in savedOptions ? savedOptions[opt.key] : opt.default;
      opt.choices.forEach((choice) => {
        const el = document.createElement("option");
        el.value = choice.value;
        el.textContent = choice.label;
        if (choice.value === preferred) el.selected = true;
        select.appendChild(el);
      });

      row.appendChild(label);
      row.appendChild(select);
      printerOptionsContainer.appendChild(row);
    });
    optionLimits = options
      .filter((o) => o.limits)
      .map((o) => ({ key: o.key, limits: o.limits, fallbacks: o.fallbacks || {} }));
    optionDefaults = Object.fromEntries(options.map((o) => [o.key, o.default]));
  } catch (err) {
    // A printer with no exposed options (or a driver that can't be
    // introspected) just prints with its own defaults — non-fatal.
    optionLimits = [];
  }
  schedulePreview();
  loadMaintenance();
}

// --- Option limits (e.g. which paper sizes a media type allows) -----------
// Mirrors the server's own correction: the side the user just changed wins,
// the other option switches to a compatible value, and a hint says so.

let optionLimits = []; // [{key: "media_type", limits: {paper_size: {media: [sizes]}}}]
let optionDefaults = {}; // printer's own defaults, preferred when a value must change
const limitHint = document.createElement("p");
limitHint.className = "limit-hint";

function optionSelect(key) {
  return printerOptionsContainer.querySelector(`select[data-option-key="${key}"]`);
}

function applyLimits(changedKey) {
  limitHint.remove();
  for (const { key, limits, fallbacks } of optionLimits) {
    const owner = optionSelect(key);
    for (const [targetKey, byValue] of Object.entries(limits)) {
      const target = optionSelect(targetKey);
      if (!owner || !target) continue;
      const allowedFor = (ownerValue) => byValue[ownerValue] || null; // null = no restriction
      const fits = (ownerValue, targetValue) => !allowedFor(ownerValue) || allowedFor(ownerValue).includes(targetValue);
      if (fits(owner.value, target.value)) continue;

      let adjusted, from = null;
      if (changedKey === targetKey) {
        // User picked the size: switch to the media type the server itself
        // would use for it (a similar one — photo stays photo).
        const replacement = fallbacks[targetKey]?.[owner.value]?.[target.value];
        if (!replacement) continue;
        from = owner.value;
        owner.value = replacement;
        adjusted = owner;
      } else {
        from = target.value;
        const allowed = allowedFor(owner.value);
        target.value = allowed.includes(optionDefaults[targetKey]) ? optionDefaults[targetKey] : allowed[0];
        adjusted = target;
      }
      const label = adjusted.closest(".form-row").querySelector("label").textContent;
      limitHint.textContent = t("limit_hint", { label, from, to: adjusted.value });
      adjusted.closest(".form-row").after(limitHint);
    }
  }
}

printerOptionsContainer.addEventListener("change", (e) => {
  if (e.target.dataset.optionKey) applyLimits(e.target.dataset.optionKey);
});

// --- Maintenance (test page, nozzle check, head cleaning) ----------------

const maintenanceBox = document.getElementById("maintenance");
const maintenanceActions = document.getElementById("maintenance-actions");

async function loadMaintenance() {
  maintenanceBox.hidden = true;
  const printer = printerSelect.value;
  if (!printer) return;
  try {
    const res = await fetch(`/api/printers/${encodeURIComponent(printer)}/maintenance`);
    if (!res.ok) throw new Error();
    const actions = await res.json();
    if (printer !== printerSelect.value) return; // printer changed meanwhile
    maintenanceActions.innerHTML = actions
      .map((a) => `<button type="button" class="ghost-button" data-action="${escapeHtml(a.id)}"
        data-confirm="${escapeHtml(a.confirm || "")}" title="${escapeHtml(a.description)}">${escapeHtml(a.label)}</button>`)
      .join("");
    maintenanceBox.hidden = actions.length === 0;
  } catch (err) {
    // no maintenance for this printer — keep the section hidden
  }
}

maintenanceActions.addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.confirm && !confirm(button.dataset.confirm)) return;
  const printer = printerSelect.value;
  button.disabled = true;
  formMessage.textContent = t("action_running", { action: button.textContent });
  formMessage.className = "message";
  try {
    const res = await fetch(
      `/api/printers/${encodeURIComponent(printer)}/maintenance/${encodeURIComponent(button.dataset.action)}`,
      { method: "POST" }
    );
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("error")));
    if (data.status === "failed") throw new Error(data.error);
    formMessage.textContent = t("action_sent", { action: button.textContent });
    formMessage.className = "message success";
  } catch (err) {
    formMessage.textContent = err.message;
    formMessage.className = "message error";
  } finally {
    button.disabled = false;
    loadJobs();
  }
});

function collectSelectedOptions() {
  const values = {};
  printerOptionsContainer.querySelectorAll("select[data-option-key]").forEach((select) => {
    values[select.dataset.optionKey] = select.value;
  });
  return values;
}

printerSelect.addEventListener("change", loadPrinterOptions);
printerOptionsContainer.addEventListener("change", schedulePreview);

printerSelect.addEventListener("change", () => {
  printPrefs.printer = printerSelect.value;
  savePrintPrefs();
});
copiesInput.addEventListener("change", () => {
  printPrefs.copies = Number(copiesInput.value) || 1;
  savePrintPrefs();
});
printerOptionsContainer.addEventListener("change", () => {
  printPrefs.options[printerSelect.value] = collectSelectedOptions();
  savePrintPrefs();
});

// --- Preview -------------------------------------------------------------
// While the panel is open it re-renders on any change of file / printer /
// options; stale responses (from before the latest change) are dropped.

let previewTimer = null;
let previewSeq = 0;

function hidePreview() {
  previewPanel.hidden = true;
  previewButton.classList.remove("active");
  previewSeq++;
}

function schedulePreview() {
  if (previewPanel.hidden) return;
  clearTimeout(previewTimer);
  previewTimer = setTimeout(loadPreview, 300);
}

async function loadPreview() {
  const file = fileInput.files[0];
  if (!file) return;
  const seq = ++previewSeq;

  previewPages.classList.add("loading");
  previewInfo.textContent = t("preview_loading");

  const formData = new FormData();
  formData.append("file", file);
  formData.append("printer", printerSelect.value);
  formData.append("options", JSON.stringify(collectSelectedOptions()));

  try {
    const res = await fetch("/api/preview", { method: "POST", body: formData });
    const data = await res.json();
    if (seq !== previewSeq) return;
    if (!res.ok) throw new Error(errorDetail(data, t("preview_error")));
    renderPreview(data);
  } catch (err) {
    if (seq !== previewSeq) return;
    previewInfo.textContent = "";
    previewPages.innerHTML = `<p class="preview-empty">${escapeHtml(err.message)}</p>`;
  } finally {
    if (seq === previewSeq) previewPages.classList.remove("loading");
  }
}

function renderPreview(data) {
  if (!data.available) {
    previewInfo.textContent = "";
    previewPages.innerHTML = `<p class="preview-empty">${escapeHtml(data.reason)}</p>`;
    return;
  }

  const [w, h] = data.paper_mm;
  const shown = data.pages.length;
  let info = t("preview_info", { w: Math.round(w), h: Math.round(h), pages: data.total_pages });
  if (shown < data.total_pages) info += t("preview_first", { shown });
  if (!data.exact_layout) info += t("preview_approx");
  previewInfo.textContent = info;

  previewPages.innerHTML = data.pages
    .map((src, i) => `<div class="preview-page"><img src="${src}" alt="${t("page_n", { n: i + 1 })}" /><span>${i + 1}</span></div>`)
    .join("");
}

previewButton.addEventListener("click", () => {
  if (!previewPanel.hidden) {
    hidePreview();
    return;
  }
  previewPanel.hidden = false;
  previewButton.classList.add("active");
  previewPages.innerHTML = "";
  loadPreview();
});

previewClose.addEventListener("click", hidePreview);

function renderJobs(jobs) {
  if (jobs.length === 0) {
    jobsBody.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(t("no_jobs"))}</td></tr>`;
    return;
  }
  jobsBody.innerHTML = jobs
    .map((job) => {
      const created = new Date(job.created_at);
      const isToday = created.toDateString() === new Date().toDateString();
      // Older entries (history survives restarts) get their date too.
      const time = isToday
        ? created.toLocaleTimeString(document.documentElement.lang, { hour: "2-digit", minute: "2-digit" })
        : created.toLocaleString(document.documentElement.lang, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      const status = STATUS_LABELS[job.status] || job.status;
      const title = job.error ? ` title="${job.error.replace(/"/g, "&quot;")}"` : "";
      const optionsSummary = job.options
        ? Object.entries(job.options)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")
        : "—";
      const printerName = job.printer || t("default_printer");
      return `<tr>
        <td class="truncate" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</td>
        <td class="truncate" title="${escapeHtml(printerName)}">${escapeHtml(printerName)}</td>
        <td>${job.copies}</td>
        <td class="truncate options-cell" title="${escapeHtml(optionsSummary)}">${escapeHtml(optionsSummary)}</td>
        <td><span class="status-badge status-${job.status}"${title}>${status}</span>${job.note
          ? `<span class="status-note" title="${escapeHtml(job.note)}">⚠</span>` : ""}</td>
        <td>${time}</td>
        <td class="row-actions">${job.status === "queued" ? "" :
          `<button type="button" class="icon-button" data-delete-job="${job.id}" title="${escapeHtml(t("delete_from_history"))}" aria-label="${escapeHtml(t("delete_from_history"))}">✕</button>`}</td>
      </tr>`;
    })
    .join("");
}

// Safe for both element content and quoted attribute values.
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML.replace(/"/g, "&quot;");
}

async function loadJobs() {
  try {
    const res = await fetch("/api/jobs");
    if (!res.ok) throw new Error(await res.text());
    renderJobs(await res.json());
  } catch (err) {
    // silent — job list is non-critical
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  printButton.disabled = true;
  formMessage.textContent = t("sending");
  formMessage.className = "message";

  const formData = new FormData();
  formData.append("file", file);
  formData.append("printer", printerSelect.value);
  formData.append("copies", copiesInput.value || "1");
  formData.append("options", JSON.stringify(collectSelectedOptions()));

  try {
    const res = await fetch("/api/print", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(errorDetail(data, t("print_error")));

    if (data.status === "failed") {
      formMessage.textContent = t("error_with", { error: data.error });
      formMessage.className = "message error";
    } else {
      formMessage.textContent = data.note ? `${t("print_sent")}. ${data.note}` : t("print_sent");
      formMessage.className = "message success";
      fileInput.value = "";
    }
  } catch (err) {
    formMessage.textContent = err.message;
    formMessage.className = "message error";
  } finally {
    updateDropzoneLabel();
    loadJobs();
  }
});

refreshJobsButton.addEventListener("click", loadJobs);

jobsBody.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-delete-job]");
  if (!button) return;
  button.disabled = true;
  await fetch(`/api/jobs/${encodeURIComponent(button.dataset.deleteJob)}`, { method: "DELETE" });
  loadJobs();
});

document.getElementById("clear-jobs").addEventListener("click", async () => {
  if (!confirm(t("confirm_clear"))) return;
  await fetch("/api/jobs", { method: "DELETE" });
  loadJobs();
});

loadPrinters();
loadJobs();
setInterval(loadJobs, 5000);
