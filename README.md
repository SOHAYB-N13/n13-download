<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" alt="License: MIT"/>
  <img src="https://img.shields.io/badge/Platform-Windows-blue.svg?style=for-the-badge&logo=windows&logoColor=white" alt="Platform: Windows"/>
</p>

<h1 align="center">⬇️ N13 Download Manager</h1>

<p align="center">
  <em>مدیر دانلود چندریسمانی</em> ·
  <em>Multi-threaded download manager</em>
</p>

---

## 🇬🇧 English

### 📖 About

**N13 Download Manager** (Terminal Download Manager, TDM) is a fast,
multi-threaded download manager for Windows. It splits downloads into
**multiple parallel parts**, supports **resume**, **batch downloads**, and even
integrates with your **browser** so you can send any link straight to the
manager.

Two interfaces are included:

- 🖥️ **Terminal (TUI)** — a polished Rich-based menu with live progress bars
- 🌐 **Graphical (GUI)** — a dark, modern web UI (pywebview)

### 💿 Download the app (Windows)

Get the ready-to-run **installer** — no Python required:

[⬇️ Download N13-Download-Manager-Setup.exe](https://github.com/SOHYAB-N13/n13-download/releases/latest)

The installer bundles WebView2 setup, registers the `dldm://` protocol and is
updated automatically from the app.

### ✨ Features

| Feature | Description |
| --- | --- |
| ⚡ Multi-threaded | Downloads split into parallel parts (up to 64 threads) |
| ▶️ Resume | Interrupted downloads restart from where they stopped |
| 📦 Batch mode | Scan URL patterns, import lists from CSV / text files |
| 🌐 Browser integration | Chrome extension + `dldm://` protocol + local relay server |
| ✅ Checksum check | Verify downloads with MD5 or SHA-256 |
| 🍪 Cookie support | Raw header, `cookies.txt`, or live browser cookies |
| 🛡️ SSRF protection | Blocks access to private / local IP ranges |
| ⏱️ Speed controls | Throttle bandwidth, pause/resume tasks |
| 🔌 Shutdown after | Optional auto shutdown when the queue finishes |
| 💾 Persistent config | Settings saved to `~/.config/terminal-download-manager/` |
| 🗓️ Scheduler | Start / stop time windows and a night-time speed cap |
| 📋 Clipboard monitor | Opt-in: auto-captures URLs copied to the clipboard |
| 🗄️ SQLite task store | Crash-safe, persistent queue & download history |
| 🔬 URL analyzer | Inspects a link first (name, size, type, range support) |
| 🚦 Single instance | A second launch forwards the URL to the running instance |
| 🧠 Smart optimizer | Auto-tunes connection count by file size and server stability |
| ⚙️ Download rules | Rules route each link to the right folder/category automatically |
| 🖱️ System tray | Tray icon with pause/resume/folder/settings and a live speed tooltip |
| 🔄 Auto-updater | Checks GitHub Releases, verifies SHA-256, installs & restarts |
| 💿 Installer | One-click Windows installer — no Python required |
| 🌍 Multi-language | English and Persian (Farsi) UI with an i18n system |

### 🚀 Install & run from source

Prerequisites: **Python 3.10 or newer**

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2a. Launch the terminal UI
python d.py

# 2b. Launch the graphical UI
python d.py --gui
```

### 🎯 Usage

Download a file directly:

```bash
python d.py "https://example.com/file.zip"
```

Download with 8 threads into a specific folder:

```bash
python d.py "https://example.com/file.zip" -t 8 -d "D:/Downloads"
```

Download and verify its checksum:

```bash
python d.py "https://example.com/file.zip" --checksum "sha256:..."
```

#### Command-line options

| Option | Description |
| --- | --- |
| `<url>` | Download URL |
| `-d, --dir <path>` | Download directory |
| `-t, --threads <n>` | Number of download threads |
| `--checksum <hash>` | Expected MD5 or SHA-256 hash to verify |
| `--insecure-ssl` | Disable SSL verification (requires `TDM_INSECURE_SSL=1`) |
| `--from-browser` | Treat the URL as a browser-originated download |
| `--url-file <path>` | Read the URL from a file |
| `--register` | Register the `dldm://` protocol handler |
| `--unregister` | Remove the `dldm://` protocol handler |
| `--create-extension` | Generate a ready-to-load Chrome extension |
| `--gui` | Launch the graphical interface |

### 🌐 Browser integration

1. `python d.py --register` — register the `dldm://` handler.
2. `python d.py --create-extension` — generates the Chrome extension folder
   with a local `token.json` (auto-ignored by git).
3. Load the extension from `chrome://extensions` (Developer mode → Load unpacked).
4. Right-click any link in Chrome and choose **Send to N13 Download Manager**.

> 🔒 The `token.json` files are intentionally **git-ignored** — they contain a
> per-machine relay token and must never be committed.

---

## 🇮🇷 فارسی

### 📖 معرفی

**مدیر دانلود N13** یک دانلود منیجر چندریسمانی و سریع برای ویندوز است. دانلودها را به
**چند بخش موازی** تقسیم می‌کند، از **ادامه‌دهی (Resume)** و **دانلود گروهی** پشتیبانی
می‌کند و حتی به **مرورگر** متصل می‌شود تا هر لینکی را مستقیم به مدیر دانلود بفرستید.

دو رابط کاربری دارد:

- 🖥️ **رابط ترمینال (TUI)** — منوی زیبای مبتنی بر Rich با نوار پیشرفت زنده
- 🌐 **رابط گرافیکی (GUI)** — وب‌یو تاریک و مدرن (pywebview)

### 💿 دانلود برنامه (ویندوز)

**نصب‌کننده آماده** را دانلود کنید — نیازی به پایتون نیست:

[⬇️ دانلود N13-Download-Manager-Setup.exe](https://github.com/SOHYAB-N13/n13-download/releases/latest)

نصب‌کننده شامل تنظیم WebView2 و ثبت پروتکل `dldm://` است و برنامه خودش از داخل،
به‌روزرسانی‌ها را نصب می‌کند.

### ✨ امکانات

| امکانات | توضیح |
| --- | --- |
| ⚡ چندریسمانی | تقسیم دانلود به بخش‌های موازی (تا ۶۴ ریسمان) |
| ▶️ ادامه‌دهی | دانلود قطع‌شده از همان نقطه‌ای که ماند ادامه می‌یابد |
| 📦 دانلود گروهی | اسکن الگوی لینک‌ها و وارد کردن لیست از فایل CSV / متنی |
| 🌐 اتصال به مرورگر | اکستنشن کروم + پروتکل `dldm://` + سرور واسط لوکال |
| ✅ بررسی Checksum | تأیید صحت دانلود با MD5 یا SHA-256 |
| 🍪 پشتیبانی از کوکی | هدر کوکی، فایل `cookies.txt` یا کوکی‌های زنده مرورگر |
| 🛡️ محافظت SSRF | مسدودسازی دسترسی به آدرس‌های خصوصی / لوکال |
| ⏱️ کنترل سرعت | محدودسازی پهنای باند و توقف/ادامه وظایف |
| 🔌 خاموش‌شدن خودکار | خاموش کردن سیستم پس از پایان صف دانلود |
| 💾 ذخیره تنظیمات | ذخیره تنظیمات در `~/.config/terminal-download-manager/` |
| 🗓️ زمان‌بندی | پنجره‌های شروع/توقف و سقف سرعت در ساعات شب |
| 📋 مانیتور کلیپ‌بورد | اختیاری: دریافت خودکار لینک‌های کپی‌شده |
| 🗄️ پایگاه داده SQLite | ذخیره امن و پایدار صف و تاریخچه دانلود |
| 🔬 تحلیل لینک | بررسی پیش از دانلود (نام، حجم، نوع، پشتیبانی Range) |
| 🚦 تک‌نمونه‌ای | اجرای دوم، لینک را به نمونه در حال اجرا می‌دهد |
| 🧠 بهینه‌ساز هوشمند | تنظیم خودکار تعداد اتصال‌ها بر اساس حجم فایل و پایداری سرور |
| ⚙️ قوانین دانلود | قوانین، هر لینک را خودکار به پوشه/دسته مناسب می‌برند |
| 🖱️ سینی سیستم | آیکون سینی با توقف/ادامه/پوشه/تنظیمات و تولتیپ سرعت زنده |
| 🔄 به‌روزرسانی خودکار | بررسی ریلیز گیت‌هاب، تأیید SHA-256، نصب و اجرای مجدد |
| 💿 نصب‌کننده | نصب یک‌کلیکه ویندوز — بدون نیاز به پایتون |
| 🌍 چندزبانه | رابط انگلیسی و فارسی با سیستم i18n |

### 🚀 نصب و اجرا از سورس

پیش‌نیازها: **پایتون ۳.۱۰ یا بالاتر**

```bash
# ۱. نصب وابستگی‌ها
pip install -r requirements.txt

# ۲الف. اجرای رابط ترمینال
python d.py

# ۲ب. اجرای رابط گرافیکی
python d.py --gui
```

### 🎯 روش استفاده

دانلود مستقیم یک فایل:

```bash
python d.py "https://example.com/file.zip"
```

دانلود با ۸ ریسمان در پوشه دلخواه:

```bash
python d.py "https://example.com/file.zip" -t 8 -d "D:/Downloads"
```

دانلود و بررسی Checksum:

```bash
python d.py "https://example.com/file.zip" --checksum "sha256:..."
```

#### پارامترهای خط فرمان

| پارامتر | توضیح |
| --- | --- |
| `<url>` | لینک دانلود |
| `-d, --dir <مسیر>` | پوشه مقصد دانلود |
| `-t, --threads <تعداد>` | تعداد ریسمان‌های دانلود |
| `--checksum <هش>` | هش MD5 یا SHA-256 برای بررسی صحت فایل |
| `--insecure-ssl` | غیرفعال‌سازی بررسی SSL (با شرط `TDM_INSECURE_SSL=1`) |
| `--from-browser` | در نظر گرفتن لینک به عنوان دانلود از مرورگر |
| `--url-file <مسیر>` | خواندن لینک از یک فایل |
| `--register` | ثبت پردازشگر پروتکل `dldm://` |
| `--unregister` | حذف پردازشگر پروتکل `dldm://` |
| `--create-extension` | ساخت اکستنشن آماده کروم |
| `--gui` | اجرای رابط گرافیکی |

### 🌐 اتصال به مرورگر

1. `python d.py --register` — ثبت پردازشگر `dldm://`
2. `python d.py --create-extension` — ساخت پوشه اکستنشن کروم همراه با `token.json` محلی
   (به صورت خودکار از git حذف می‌شود)
3. اکستنشن را از `chrome://extensions` بارگذاری کنید (Developer mode → Load unpacked)
4. روی هر لینکی در کروم کلیک راست کنید و **Send to N13 Download Manager** را بزنید

> 🔒 فایل‌های `token.json` عمداً در **git-ignored** هستند — آن‌ها حاوی رمز واسط
> مخصوص هر سیستم هستند و هرگز نباید در ریپازیتوری قرار بگیرند.

---

## 📄 License

Released under the [MIT License](LICENSE) · Copyright © 2026 **SOHAYB N13**
