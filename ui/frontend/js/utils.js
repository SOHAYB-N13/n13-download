/* ═══════════════════════════════════════════════════════════════════════════
   N13 Download Manager — Utilities & Icon System
   ═══════════════════════════════════════════════════════════════════════════ */

const Utils = {

  // ── Formatting ────────────────────────────────────────────────────

  formatSize(bytes) {
    if (bytes == null || isNaN(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let size = Math.max(0, Number(bytes));
    let i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return i === 0 ? `${Math.round(size)} ${units[i]}` : `${size.toFixed(size >= 100 ? 0 : 1)} ${units[i]}`;
  },

  formatSpeed(bps) {
    if (!bps || bps <= 0) return "0 B/s";
    return this.formatSize(bps) + "/s";
  },

  formatETA(seconds) {
    if (seconds == null || seconds < 0 || seconds > 359999) return "—";
    const t = Math.floor(seconds);
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const s = t % 60;
    if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
    if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  },

  formatDate(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  },

  formatDateTime(str) {
    // history entries arrive as "YYYY-MM-DD HH:MM:SS"
    if (!str) return "";
    const d = new Date(str.replace(" ", "T"));
    if (isNaN(d)) return str;
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (sameDay) return "Today · " + time;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " · " + time;
  },

  isToday(str) {
    if (!str) return false;
    const d = new Date(String(str).replace(" ", "T"));
    if (isNaN(d)) return false;
    return d.toDateString() === new Date().toDateString();
  },

  hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); }
    catch { return ""; }
  },

  fileName(task) {
    if (task.label) return task.label;
    try {
      const p = new URL(task.url).pathname;
      const n = decodeURIComponent(p.split("/").filter(Boolean).pop() || "");
      if (n) return n;
    } catch {}
    return task.url;
  },

  // ── Status ────────────────────────────────────────────────────────

  statusClass(state) {
    return {
      "Downloading": "downloading",
      "Paused": "paused",
      "Queued": "queued",
      "Complete": "complete",
      "Failed": "failed",
      "Stopped": "stopped",
      "Stopping": "stopping",
    }[state] || "queued";
  },

  statusLabel(state) {
    return {
      "Downloading": "Downloading",
      "Paused": "Paused",
      "Queued": "Queued",
      "Complete": "Completed",
      "Failed": "Failed",
      "Stopped": "Stopped",
      "Stopping": "Stopping",
    }[state] || state;
  },

  // ── File type detection ───────────────────────────────────────────

  fileType(name) {
    if (!name) return "file";
    const ext = String(name).split(".").pop()?.toLowerCase() || "";
    const map = {
      zip: "archive", rar: "archive", "7z": "archive", tar: "archive", gz: "archive", bz2: "archive", xz: "archive",
      mp4: "video", mkv: "video", avi: "video", mov: "video", webm: "video", flv: "video", m4v: "video",
      mp3: "audio", flac: "audio", wav: "audio", ogg: "audio", m4a: "audio", aac: "audio", opus: "audio",
      jpg: "image", jpeg: "image", png: "image", gif: "image", webp: "image", svg: "image", bmp: "image", ico: "image",
      pdf: "document", doc: "document", docx: "document", txt: "document", rtf: "document", odt: "document",
      xls: "document", xlsx: "document", ppt: "document", pptx: "document", csv: "document", md: "document",
      exe: "app", msi: "app", dmg: "app", deb: "app", rpm: "app", apk: "app", pkg: "app",
      iso: "disc", img: "disc",
    };
    return map[ext] || "file";
  },

  categoryFor(name) {
    const t = this.fileType(name);
    return {
      archive: "Compressed", video: "Videos", audio: "Music",
      image: "Images", document: "Documents", app: "Programs",
      disc: "Programs", file: "General",
    }[t] || "General";
  },

  // ── Icon system (24×24 stroke icons) ──────────────────────────────

  icons: {
    dashboard: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/>',
    download: '<path d="M12 3.5v11"/><path d="m7.5 10 4.5 4.5L16.5 10"/><path d="M4.5 20.5h15"/>',
    history: '<path d="M3.6 12a8.4 8.4 0 1 0 2.4-5.9L3.5 8.5"/><path d="M3.5 3.5v5h5"/><path d="M12 7.5V12l3.2 1.9"/>',
    batch: '<path d="m12 3.2 8.5 4.6-8.5 4.6L3.5 7.8 12 3.2Z"/><path d="m4.5 12.2 7.5 4 7.5-4"/><path d="m4.5 16.2 7.5 4 7.5-4"/>',
    browser: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.3 2.2 3.5 5.2 3.5 8.5s-1.2 6.3-3.5 8.5c-2.3-2.2-3.5-5.2-3.5-8.5s1.2-6.3 3.5-8.5Z"/>',
    settings: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.8c.6 0 1 .4 1.1 1l.3 1.5c.5.2 1 .5 1.5.8l1.4-.6c.6-.2 1.2 0 1.5.5l1 1.7c.3.5.2 1.2-.2 1.6l-1.2 1.1c.1.5.1.9.1 1.4s0 1-.1 1.4l1.2 1.1c.4.4.5 1 .2 1.6l-1 1.7c-.3.5-1 .7-1.5.5l-1.4-.6c-.5.3-1 .6-1.5.8l-.3 1.5c-.1.6-.6 1-1.1 1h-2c-.6 0-1-.4-1.1-1l-.3-1.5a7 7 0 0 1-1.5-.8l-1.4.6c-.6.2-1.2 0-1.5-.5l-1-1.7a1.2 1.2 0 0 1 .2-1.6l1.2-1.1a7.3 7.3 0 0 1 0-2.8L5.2 9.3a1.2 1.2 0 0 1-.2-1.6l1-1.7c.3-.5 1-.7 1.5-.5l1.4.6c.5-.3 1-.6 1.5-.8l.3-1.5c.1-.6.6-1 1.1-1h2Z"/>',
    logs: '<path d="m5 7.5 4.5 4.5L5 16.5"/><path d="M11.5 17h7"/><rect x="3" y="4" width="18" height="16" rx="2.5"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20.5 20.5-4.6-4.6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    paste: '<rect x="8" y="2.5" width="8" height="3.5" rx="1"/><path d="M16 4.5h2.5A1.5 1.5 0 0 1 20 6v14a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 20V6a1.5 1.5 0 0 1 1.5-1.5H8"/>',
    pause: '<rect x="6.5" y="4.5" width="3.6" height="15" rx="1.2"/><rect x="13.9" y="4.5" width="3.6" height="15" rx="1.2"/>',
    play: '<path d="M7.5 4.8v14.4c0 .8.9 1.3 1.6.9l11.2-7.2a1 1 0 0 0 0-1.7L9.1 4a1 1 0 0 0-1.6.8Z"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    check: '<path d="m4.5 12.5 5 5L19.5 7"/>',
    alert: '<path d="M12 8.5V13"/><path d="M12 16.8h.01"/><path d="M10.2 3.6 2.4 17.3a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.8 3.6a2 2 0 0 0-3.6 0Z"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.5h.01"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-10.5Z"/>',
    folderOpen: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v1"/><path d="M3.5 8.5h15.4a2 2 0 0 1 1.9 2.6l-1.6 5.4a2 2 0 0 1-1.9 1.4H5.5a2 2 0 0 1-2-2V8.5Z"/>',
    trash: '<path d="M4 6.5h16"/><path d="M9 6.5v-2a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M19 6.5 18.1 19a2 2 0 0 1-2 1.9H7.9a2 2 0 0 1-2-1.9L5 6.5"/><path d="M10 11v5.5M14 11v5.5"/>',
    copy: '<rect x="9" y="9" width="11.5" height="11.5" rx="2"/><path d="M5.5 15h-1a2 2 0 0 1-2-2V5.5a2 2 0 0 1 2-2H12a2 2 0 0 1 2 2v1"/>',
    more: '<circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5V4M12 20v1.5M4.6 4.6l1 1M18.4 18.4l1 1M2.5 12H4M20 12h1.5M4.6 19.4l1-1M18.4 5.6l1-1"/>',
    moon: '<path d="M20.5 13.2A8.5 8.5 0 1 1 10.8 3.5a7 7 0 0 0 9.7 9.7Z"/>',
    chevronDown: '<path d="m6 9.5 6 6 6-6"/>',
    chevronRight: '<path d="m9.5 6 6 6-6 6"/>',
    chevronLeft: '<path d="m14.5 6-6 6 6 6"/>',
    arrowUp: '<path d="M12 19V5"/><path d="m5.5 11.5 6.5-6.5 6.5 6.5"/>',
    arrowDown: '<path d="M12 5v14"/><path d="m5.5 12.5 6.5 6.5 6.5-6.5"/>',
    gauge: '<path d="m12 13.5 3.5-3.5"/><path d="M3.8 19.3a9 9 0 1 1 16.4 0"/>',
    activity: '<path d="M21.5 12h-3.8l-2.7 8L9.3 4l-2.7 8H2.5"/>',
    disk: '<path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-11Z"/><circle cx="12" cy="11.5" r="2.8"/><path d="M8 17.5h8"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="16" rx="2"/><path d="M8 3v4M16 3v4M3.5 10.5h17"/>',
    file: '<path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5L13.5 3Z"/><path d="M13.5 3v5.5H19"/>',
    archive: '<rect x="3.5" y="4" width="17" height="4.5" rx="1.2"/><path d="M5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19V8.5"/><path d="M10 12.5h4"/>',
    video: '<rect x="2.5" y="5.5" width="13.5" height="13" rx="2.2"/><path d="m16 10.5 5.5-3.2v9.4L16 13.5"/>',
    audio: '<path d="M9.5 18.5V6.8L20 4.5v12"/><circle cx="6.8" cy="18.5" r="2.7"/><circle cx="17.3" cy="16.5" r="2.7"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><circle cx="9" cy="10" r="1.6"/><path d="m4.8 17.8 4.7-4.8 3 3 3.4-3.5 4.3 4.4"/>',
    document: '<path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5L13.5 3Z"/><path d="M13.5 3v5.5H19"/><path d="M9 13h6M9 16.5h6"/>',
    app: '<path d="m5 8 4 4-4 4"/><path d="M12 16.5h7"/><rect x="3" y="4" width="18" height="16" rx="2.5"/>',
    disc: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.5"/>',
    link: '<path d="M10 13.5a5 5 0 0 0 7.5.5l2.8-2.8a5 5 0 0 0-7-7l-1.6 1.5"/><path d="M14 10.5a5 5 0 0 0-7.5-.5l-2.8 2.8a5 5 0 0 0 7 7l1.6-1.5"/>',
    retry: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6L20.5 8"/><path d="M20.5 3.5V8H16"/>',
    bolt: '<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12l1-8Z"/>',
    menu: '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    panelLeft: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M9.5 4.5v15"/>',
    min: '<path d="M5.5 12h13"/>',
    max: '<rect x="6" y="6" width="12" height="12" rx="1.6"/>',
    restore: '<rect x="8.5" y="8.5" width="10.5" height="10.5" rx="1.6"/><path d="M5.5 15.5v-9a1 1 0 0 1 1-1h9"/>',
    sortAsc: '<path d="M7 4.5v15"/><path d="m3.5 8 3.5-3.5L10.5 8"/><path d="M17 19.5v-15"/><path d="m13.5 16 3.5 3.5 3.5-3.5"/>',
    external: '<path d="M14 4.5h5.5V10"/><path d="M19.5 4.5 10.5 13.5"/><path d="M19.5 14v5a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V6A1.5 1.5 0 0 1 6 4.5h5"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    xCircle: '<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>',
    power: '<path d="M12 3v8"/><path d="M6.3 6.2a8 8 0 1 0 11.4 0"/>',
    server: '<rect x="3.5" y="4" width="17" height="6.5" rx="1.8"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="1.8"/><path d="M7 7.2h.01M7 16.7h.01"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M12 2.5V6M12 18v3.5M2.5 12H6M18 12h3.5M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/>',
    wifi: '<path d="M2.5 9a14 14 0 0 1 19 0"/><path d="M5.5 12.5a9.5 9.5 0 0 1 13 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M12 19.5h.01"/>',
    clearAll: '<path d="m4 7 5-5 5 5"/><path d="M9 2v12"/><path d="M4 17h16M4 21h10"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.8"/>',
    filter: '<path d="M4 5.5h16l-6.2 7.2v5.6l-3.6 2.2v-7.8L4 5.5Z"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h.01M18 14h.01M9 14h6"/>',
  },

  icon(name, size = 18, cls = "") {
    const body = this.icons[name] || this.icons.file;
    return `<svg class="ico ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
  },

  fileIcon(name, size = 20) {
    return this.icon(this.fileType(name), size);
  },

  // ── DOM helpers ───────────────────────────────────────────────────

  debounce(fn, ms) {
    let t;
    const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    wrapped.cancel = () => clearTimeout(t);
    return wrapped;
  },

  throttle(fn, ms) {
    let last = 0, timer = null;
    return (...args) => {
      const now = Date.now();
      const run = () => { last = Date.now(); timer = null; fn(...args); };
      if (now - last >= ms) run();
      else if (!timer) timer = setTimeout(run, ms - (now - last));
    };
  },

  escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  },

  $id(id) { return document.getElementById(id); },
  $q(sel, ctx) { return (ctx || document).querySelector(sel); },
  $qa(sel, ctx) { return Array.from((ctx || document).querySelectorAll(sel)); },

  clamp(v, min, max) { return Math.min(max, Math.max(min, v)); },
};
