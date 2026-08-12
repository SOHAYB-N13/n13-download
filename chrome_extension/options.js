/** N13 extension options page. */
(function () {
  "use strict";

  let i18n;
  let settings = {};

  const $ = (id) => document.getElementById(id);

  async function init() {
    const res = await sendMessage({ action: "settings_get" });
    settings = res?.settings || {};
    i18n = new N13_I18N(settings.language || "en");
    document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
    applyTranslations();
    populateForm();
    bindEvents();
    $("versionText").textContent = chrome.runtime.getManifest().version;
  }

  function sendMessage(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (response) => {
        if (chrome.runtime.lastError) return resolve({});
        resolve(response || {});
      });
    });
  }

  function applyTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      el.textContent = i18n.t(key);
    });
  }

  function populateForm() {
    $("enabled").checked = settings.enabled !== false;
    $("contextMenu").checked = settings.contextMenu !== false;
    $("detection").checked = settings.detection !== false;
    $("showButton").checked = settings.showButton !== false;
    $("openN13").checked = settings.openN13 !== false;
    $("language").value = settings.language || "en";
  }

  function readForm() {
    return {
      enabled: $("enabled").checked,
      contextMenu: $("contextMenu").checked,
      detection: $("detection").checked,
      showButton: $("showButton").checked,
      openN13: $("openN13").checked,
      language: $("language").value,
    };
  }

  async function save() {
    const next = readForm();
    await sendMessage({ action: "settings_set", settings: next });
    settings = next;
    i18n = new N13_I18N(settings.language);
    document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
    applyTranslations();

    const hint = $("saveHint");
    hint.textContent = i18n.t("saved");
    hint.classList.add("visible");
    setTimeout(() => hint.classList.remove("visible"), 2000);
  }

  function bindEvents() {
    $("saveBtn").addEventListener("click", save);
    $("language").addEventListener("change", () => {
      // Preview language immediately before saving.
      i18n = new N13_I18N($("language").value);
      document.documentElement.setAttribute("dir", i18n.isRtl() ? "rtl" : "ltr");
      applyTranslations();
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
