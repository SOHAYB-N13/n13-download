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
    },
    fa: {
      extName: "مدیریت دانلود N13",
      extDescription: "ارسال دانلودهای مرورگر به مدیریت دانلود N13",
      popupTitle: "مدیریت دانلود N13",
      statusConnected: "N13 متصل است",
      statusNotRunning: "N13 اجرا نیست",
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
