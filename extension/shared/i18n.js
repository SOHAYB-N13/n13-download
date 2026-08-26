/** Simple i18n layer for the N13 extension. English default + Persian RTL. */
(function (global) {
  const DEFAULT_LOCALE = "en";

  const MESSAGES = {
    en: {
      extName: "N13 Download Manager",
      extDescription: "Send browser downloads to N13 Download Manager",
      popupTitle: "N13 Download Manager",
      statusConnected: "N13 Connected",
      statusNotRunning: "N13 Not Running",
      statusConnecting: "Connecting…",
      statusAuthError: "N13 authorization error",
      connectionError: "Could not load the scanned links.",
      openN13: "Open N13",
      settings: "Settings",
      about: "About",
      downloadPage: "Download page",
      downloadLink: "Download link",
      downloadMedia: "Download media",
      downloadSelection: "Download selected links",
      downloadAll: "Download all links",
      downloadWithN13: "Download with N13",
      noLinksFound: "No links found",
      noLinksFoundDetail: "No downloadable links were detected.",
      unsupportedLink: "Unsupported link",
      onlyHttpSupported: "Only http(s) links can be sent to N13.",
      sentToQueue: "Sent to queue",
      toastSent: "Added to N13 queue ✓",
      toastReloadPage: "N13 was updated — please reload this page.",
      toastFailed: "Could not send to N13",
      batchQueued: "Batch queued",
      failedToSend: "Failed to send",
      failedToSendDetail: "Start N13 or register the dldm:// protocol.",
      optionsTitle: "N13 Extension Settings",
      enableIntegration: "Enable browser integration",
      enableContextMenu: "Show context menu",
      enableDetection: "Enable download detection",
      showDownloadButton: "Show floating download button",
      openN13WhenNeeded: "Open N13 when needed",
      language: "Language",
      save: "Save",
      saved: "Saved",
      shortcutDownloadPage: "Download current page with N13",
      aboutText: "N13 Download Manager browser companion.",
      version: "Version",
      currentPage: "Current page",
      recent: "Recent",
      clearRecent: "Clear recent",
      noRecent: "No recent downloads",
      // Link Grabber
      grabLinks: "Grab Download Links",
      linkGrabber: "N13 Link Grabber",
      scanning: "Scanning…",
      foundLinks: "Found {n} downloadable links",
      selectAll: "Select All",
      deselectAll: "Clear",
      selectedCount: "Selected: {n} / {total}",
      sendToN13: "Send to N13",
      addToQueue: "Add to Download Queue",
      close: "Close",
      back: "Back",
      grabUnavailable: "This page cannot be scanned",
      grabUnavailableDetail: "N13 can only scan http(s) web pages.",
      noGroup: "No download group could be determined.",
      grabbedNone: "No downloadable links found.",
      grabberQueued: "Links sent to N13",
      addingToN13: "Adding to N13…",
      addedToN13: "{n} downloads added to N13.",
      grabberPartial: "Only {n} of {total} links were added to N13 (rejected: {rejected}).",
      authFailed: "N13 connection authorization failed.",
      grabberSendFailed: "Could not send links to N13",
      connectingN13: "Connecting to N13…",
      authenticatingN13: "Authenticating…",
      sendingLinks: "Sending {n} links…",
      startingN13: "Starting N13…",
      serverUnavailable: "N13 server unavailable",
      n13NotRunning: "N13 is not running",
      startN13Manually: "Please start N13 Download Manager first.",
      allRejected: "0 of {total} links were added ({rejected} rejected).",
    },
    fa: {
      extName: "مدیریت دانلود N13",
      extDescription: "ارسال دانلودهای مرورگر به مدیریت دانلود N13",
      popupTitle: "مدیریت دانلود N13",
      statusConnected: "N13 متصل است",
      statusNotRunning: "N13 اجرا نیست",
      statusConnecting: "در حال اتصال…",
      statusAuthError: "خطای احراز هویت N13",
      connectionError: "لینک‌های اسکن‌شده بارگیری نشد.",
      openN13: "باز کردن N13",
      settings: "تنظیمات",
      about: "درباره",
      downloadPage: "دانلود صفحه",
      downloadLink: "دانلود لینک",
      downloadMedia: "دانلود رسانه",
      downloadSelection: "دانلود لینک‌های انتخابی",
      downloadAll: "دانلود همه لینک‌ها",
      downloadWithN13: "دانلود با N13",
      noLinksFound: "لینکی یافت نشد",
      noLinksFoundDetail: "هیچ لینک قابل دانلودی شناسایی نشد.",
      unsupportedLink: "لینک پشتیبانی نمی‌شود",
      onlyHttpSupported: "فقط لینک‌های http(s) قابل ارسال به N13 هستند.",
      sentToQueue: "به صف ارسال شد",
      toastSent: "به صف N13 اضافه شد ✓",
      toastReloadPage: "N13 به‌روزرسانی شد — لطفاً این صفحه را دوباره بارگذاری کنید.",
      toastFailed: "ارسال به N13 ناموفق بود",
      batchQueued: "دسته به صف ارسال شد",
      failedToSend: "ارسال ناموفق بود",
      failedToSendDetail: "N13 را اجرا کنید یا پروتکل dldm:// را ثبت کنید.",
      optionsTitle: "تنظیمات افزونه N13",
      enableIntegration: "فعال‌سازی یکپارچگی مرورگر",
      enableContextMenu: "نمایش منوی راست‌کلیک",
      enableDetection: "فعال‌سازی تشخیص دانلود",
      showDownloadButton: "نمایش دکمه شناور دانلود",
      openN13WhenNeeded: "باز کردن N13 در صورت نیاز",
      language: "زبان",
      save: "ذخیره",
      saved: "ذخیره شد",
      shortcutDownloadPage: "دانلود صفحه فعلی با N13",
      aboutText: "همراه مرورگر مدیریت دانلود N13.",
      version: "نسخه",
      currentPage: "صفحه فعلی",
      recent: "اخیر",
      clearRecent: "پاک کردن موارد اخیر",
      noRecent: "دانلود اخیری وجود ندارد",
      // Link Grabber
      grabLinks: "گرفتن لینک‌های دانلود",
      linkGrabber: "گیرنده لینک N13",
      scanning: "در حال بررسی…",
      foundLinks: "{n} لینک قابل دانلود یافت شد",
      selectAll: "انتخاب همه",
      deselectAll: "پاک کردن",
      selectedCount: "انتخاب‌شده: {n} از {total}",
      sendToN13: "ارسال به N13",
      addToQueue: "افزودن به صف دانلود",
      close: "بستن",
      back: "بازگشت",
      grabUnavailable: "این صفحه قابل بررسی نیست",
      grabUnavailableDetail: "N13 فقط می‌تواند صفحات http(s) را بررسی کند.",
      noGroup: "گروه دانلودی تعیین نشد.",
      grabbedNone: "لینک قابل دانلودی یافت نشد.",
      grabberQueued: "لینک‌ها به N13 ارسال شد",
      addingToN13: "در حال افزودن به N13…",
      addedToN13: "{n} دانلود به N13 اضافه شد.",
      grabberPartial: "فقط {n} از {total} لینک به N13 اضافه شد (رد شده: {rejected}).",
      authFailed: "احراز هویت اتصال به N13 ناموفق بود.",
      grabberSendFailed: "ارسال لینک‌ها به N13 ناموفق بود",
      connectingN13: "در حال اتصال به N13…",
      authenticatingN13: "در حال احراز هویت…",
      sendingLinks: "در حال ارسال {n} لینک…",
      startingN13: "در حال اجرای N13…",
      serverUnavailable: "سرور N13 در دسترس نیست",
      n13NotRunning: "N13 اجرا نیست",
      startN13Manually: "لطفاً ابتدا مدیریت دانلود N13 را اجرا کنید.",
      allRejected: "۰ از {total} لینک اضافه شد ({rejected} رد شد).",
    },
  };

  const RTL_LOCALES = new Set(["fa"]);

  class I18N {
    constructor(locale) {
      this.locale = locale || DEFAULT_LOCALE;
      if (!MESSAGES[this.locale]) this.locale = DEFAULT_LOCALE;
    }

    t(key, fallback) {
      const msg = MESSAGES[this.locale]?.[key];
      if (msg !== undefined) return msg;
      return fallback !== undefined ? fallback : MESSAGES.en[key] || key;
    }

    isRtl() {
      return RTL_LOCALES.has(this.locale);
    }

    static getLocales() {
      return Object.keys(MESSAGES);
    }
  }

  global.N13_I18N = I18N;
})(typeof globalThis !== "undefined" ? globalThis : this);
