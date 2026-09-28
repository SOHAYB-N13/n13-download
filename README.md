# ⬇️ N13 Download Manager

> **A modern, open-source download manager for Windows — built for speed, control, and reliability.**  
> **یک دانلود منیجر مدرن و متن‌باز برای ویندوز — ساخته‌شده برای سرعت، کنترل و دانلود قابل‌اعتماد.**

[English](#-english) · [فارسی](#-فارسی)

---

<a id="-english"></a>

# 🇬🇧 English

## 🚀 N13 Download Manager

N13 is a modern, open-source, multi-threaded download manager for Windows.

It is designed to make downloading large files easier, more reliable, and more manageable — from the first click to the completed download.

N13 combines a graphical interface, browser integration, download queues, scheduling, bandwidth control, automatic organization, persistent history, checksum verification, and a terminal interface in one application.

### ✨ Why N13?

Downloading should be simple:

**Copy a link → send it to N13 → let N13 handle the rest.**

N13 gives you control when you need it, while keeping everyday downloads simple.

---

## ⚡ Highlights

- 🚀 Multi-threaded downloads with up to **64 connections**
- ▶️ Resume interrupted downloads when supported by the server
- 📦 Batch downloads and URL lists
- 🌐 Browser integration
- 📋 Clipboard monitoring
- 🗓️ Download scheduler
- ⏱️ Bandwidth and speed limits
- 🧠 Smart connection optimization
- 🗂️ Automatic categories and download rules
- 📥 Powerful download queue
- 🔐 MD5 and SHA-256 checksum verification
- 🍪 Cookie support
- 🔬 URL analyzer
- 🛡️ SSRF protection and secure browser relay
- 🗄️ Persistent SQLite download history
- 🖥️ Modern graphical interface
- 💻 Rich terminal interface
- 🖱️ Windows system tray integration
- 🔄 Automatic update support
- 🌍 English and Persian / RTL interface

---

## 🎯 Built Around the Whole Download Workflow

N13 is more than a program that starts a download and waits.

### 1. Download

Start downloads using:

- Direct URLs
- Browser integration
- Clipboard detection
- URL files
- Batch workflows
- Command line

### 2. Manage

Control your downloads from a single queue:

- Pause
- Resume
- Retry
- Remove
- Reorder
- Prioritize
- Multi-select actions

### 3. Automate

Let N13 handle repetitive tasks:

- Schedule downloads
- Apply automatic rules
- Sort downloads into categories
- Limit bandwidth
- Shut down Windows after the queue finishes

### 4. Verify

Make sure your downloaded files are what you expected:

- MD5 verification
- SHA-256 verification
- Download metadata
- Persistent task history

---

# 🖥️ Modern Graphical Interface

N13 includes a modern Windows GUI designed around everyday download management.

The interface provides:

- 📊 Dashboard
- 📥 Download queue
- 🔎 Download details
- 🗂️ Categories
- 🗓️ Scheduler
- ⚙️ Settings
- 🖱️ System tray
- ⌨️ Keyboard navigation
- 🌍 English / Persian localization
- ↔️ Full RTL support for Persian

The GUI is designed to keep important information visible without turning the application into a complicated control panel.

---

# 🚀 Multi-threaded Downloads

N13 can split supported downloads into multiple parallel connections.

This can improve utilization of available bandwidth when the remote server supports HTTP range requests.

You can control the number of connections per download, with support for up to **64 threads**.

> Actual speed depends on the server, network, file size, connection limits, and other conditions. N13 does not guarantee a specific download speed.

---

# ▶️ Resume Interrupted Downloads

Network interruptions happen.

When the server supports resuming, N13 can continue an interrupted download instead of forcing you to start again from zero.

This is especially useful for:

- Large files
- Unstable connections
- Long-running downloads
- Scheduled downloads

---

# 📦 Batch Downloads

Need to download many files?

N13 supports batch workflows such as:

- URL lists
- Text files
- CSV imports
- Multiple queued downloads
- URL pattern scanning

Instead of starting each download manually, add them to the queue and let N13 process them.

---

# 🌐 Browser Integration

N13 can connect your browser directly to the download manager.

Supported workflows include:

- Chrome extension integration
- `dldm://` protocol
- Local browser relay
- Right-click **Send to N13 Download Manager**
- Extension creation and repair

The goal is simple:

**Find a file in your browser → send it to N13 → manage it from the download manager.**

---

# 📋 Clipboard Monitoring

When enabled, N13 can watch the clipboard for copied URLs.

This is useful when you frequently copy download links and want N13 to detect them without repeatedly opening the application.

Clipboard monitoring is optional and can be disabled.

---

# 🧠 Smart Connection Optimization

N13 can adjust connection usage based on download conditions such as:

- File size
- Server behavior
- Connection stability

The purpose is to avoid blindly using the maximum number of connections for every file.

---

# 🗂️ Automatic Rules & Categories

Keep your downloads organized automatically.

Create rules that send files to the appropriate folders or categories based on your workflow.

Example:

```text
Videos      → D:/Downloads/Videos
Programs    → D:/Downloads/Programs
Archives    → D:/Downloads/Archives
Documents  → D:/Downloads/Documents
```

---

# 📥 Powerful Download Queue

The queue is the center of N13's workflow.

You can:

- Drag & drop to reorder downloads
- Set priorities
- Pause and resume tasks
- Retry failed downloads
- Remove multiple tasks
- Perform actions on selected downloads
- Navigate using the keyboard
- Control what downloads next

---

# 🗓️ Scheduler

Schedule downloads instead of starting them manually.

Configure:

- Start time
- Optional end time
- Days of the week
- Night-time speed limits

This is useful for large downloads that you prefer to run during specific hours.

---

# ⏱️ Bandwidth Control

Do not let downloads consume your entire connection.

N13 provides speed and bandwidth controls so you can leave enough network capacity for:

- Browsing
- Streaming
- Gaming
- Video calls
- Other downloads

---

# 🔌 Shutdown After Downloads

Starting a large download before going to sleep?

N13 can optionally shut down Windows after the download queue finishes successfully.

A cancellation window is provided so you can stop the shutdown if necessary.

---

# 🔐 Checksum Verification

N13 supports file integrity verification using:

- **MD5**
- **SHA-256**

You can compare the downloaded file against an expected checksum and detect corrupted or unexpected files.

---

# 🍪 Cookie Support

Some download links require browser authentication or cookies.

N13 supports cookie-based workflows through:

- Raw cookie headers
- `cookies.txt`
- Live browser cookies

---

# 🔬 URL Analyzer

Before a download begins, N13 can inspect a URL and retrieve information such as:

- File name
- File size
- Content type
- Range support

This helps N13 determine how a download can be handled before it starts.

---

# 🛡️ Security

Security is part of the project architecture.

N13 includes protections and controls such as:

- SSRF protection against private/local network targets
- Machine-specific browser relay token
- SHA-256 verification for update packages
- Sensitive `token.json` excluded from Git

For security-related issues, please follow the project's security guidelines.

---

# 🗄️ Persistent Download History

N13 uses SQLite for persistent download/task information.

Your download state and history can remain available across application restarts instead of disappearing when the program closes.

---

# 💻 Terminal Interface

Prefer the command line?

N13 also includes a Rich-based terminal interface with live download progress.

This makes N13 useful for both:

- Everyday desktop downloads
- Scripted and developer workflows

---

# 🖱️ Windows System Tray

N13 can run from the Windows system tray.

Tray actions can include:

- Pause / Resume
- Open download folder
- Open settings
- View current download speed

---

# 🔄 Automatic Updates

N13 can check GitHub Releases for new versions.

Update packages are verified using SHA-256 before installation, and the application can restart after updating.

---

# 🌍 Localization

N13 currently supports:

- 🇬🇧 English
- 🇮🇷 Persian / Farsi

The Persian interface includes RTL layout support.

---

# 📥 Installation

## Windows — Recommended

For normal Windows users, the installer is the easiest way to get started.

The Windows installer is designed to:

- Require no separate Python installation
- Install the application
- Handle WebView2 setup
- Register the `dldm://` protocol
- Support application updates

Download the latest release from the repository's **Releases** page.

---

# 🛠️ Run From Source

### Requirements

- Windows
- Python 3.10+
- Git

Clone the repository:

```bash
git clone https://github.com/SOHAYB-N13/n13-download.git
cd n13-download
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Launch the terminal interface:

```bash
python d.py
```

Launch the graphical interface:

```bash
python d.py --gui
```

---

# 🌐 Browser Setup

To connect Chrome with N13:

### 1. Register the protocol

```bash
python d.py --register
```

### 2. Create the browser extension

```bash
python d.py --create-extension
```

### 3. Load the extension in Chrome

Open:

```text
chrome://extensions
```

Enable **Developer mode**, choose **Load unpacked**, and select the generated extension directory.

### 4. Send downloads to N13

Right-click a supported link and choose:

```text
Send to N13 Download Manager
```

> 🔒 `token.json` contains a machine-specific relay token and must never be committed to the repository.

---

# 🎯 Command Line Usage

### Download a file

```bash
python d.py "https://example.com/file.zip"
```

### Use multiple connections

```bash
python d.py "https://example.com/file.zip" -t 8
```

### Choose a download directory

```bash
python d.py "https://example.com/file.zip" -d "D:/Downloads"
```

### Verify a download

```bash
python d.py "https://example.com/file.zip" --checksum "sha256:..."
```

---

# ⚙️ Command Line Options

| Option | Description |
|---|---|
| `<url>` | Download URL |
| `-d, --dir <path>` | Download directory |
| `-t, --threads <n>` | Number of download threads |
| `--checksum <hash>` | Expected MD5 or SHA-256 hash |
| `--insecure-ssl` | Disable SSL verification when explicitly enabled |
| `--from-browser` | Treat the URL as browser-originated |
| `--url-file <path>` | Read URLs from a file |
| `--register` | Register the `dldm://` protocol |
| `--unregister` | Remove the `dldm://` protocol |
| `--create-extension` | Generate the browser extension |
| `--gui` | Launch the graphical interface |

---

# 🧩 Project Structure

```text
n13-download/
├── batch/
├── browser/
├── chrome_extension/
├── config/
├── core/
├── extension/
├── installer/
├── ui/
├── d.py
├── requirements.txt
├── PACKAGING.md
├── SECURITY.md
└── README.md
```

The project separates the download engine, browser integration, interface, configuration, packaging, and supporting components so they can evolve independently.

---

# 🧪 Development

N13 is an actively developed open-source project.

Development focuses on:

- Download reliability
- Performance
- Queue management
- Browser integration
- Windows integration
- Automation
- Security
- User experience
- Internationalization

Bug reports, feature requests, documentation improvements, and code contributions are welcome.

---

# 🛣️ Roadmap

N13 is continuously evolving.

Possible future improvements include:

- More browser integrations
- More powerful link detection
- Improved connection management
- Additional automation features
- UI / UX improvements
- More protocols and download sources
- Additional platform support

---

# 🤝 Contributing

Contributions are welcome.

If you find a bug or want to improve N13:

1. Open an issue.
2. Clearly describe the problem or proposed improvement.
3. Include reproduction steps for bugs whenever possible.
4. Submit a pull request for code changes.

Please read `SECURITY.md` before reporting security-related issues.

---

# 📄 License

N13 Download Manager is released under the **MIT License**.

Copyright © 2026 SOHAYB N13

---

# ⭐ Support N13

If you find N13 useful:

- ⭐ Star the repository
- 🐛 Report bugs
- 💡 Suggest improvements
- 🔧 Contribute code
- 📢 Share the project

Every star, issue, contribution, and piece of feedback helps N13 grow.

---

<div align="center">

### ⬇️ Download faster. Manage smarter. Stay in control.

**N13 Download Manager**

Built with ❤️ and Python.

</div>

---

<a id="-فارسی"></a>

# 🇮🇷 فارسی

## 🚀 دانلود منیجر N13

N13 یک دانلود منیجر مدرن، متن‌باز و چندریسمانی برای ویندوز است.

هدف N13 این است که دانلود فایل‌های بزرگ را **ساده‌تر، قابل‌اعتمادتر و قابل‌کنترل‌تر** کند؛ از لحظه‌ای که لینک را دریافت می‌کنید تا زمانی که فایل به‌طور کامل دانلود شود.

N13 رابط گرافیکی، اتصال به مرورگر، صف دانلود، زمان‌بندی، کنترل پهنای باند، دسته‌بندی خودکار، تاریخچه دائمی، بررسی صحت فایل، و رابط خط فرمان را در یک برنامه ترکیب می‌کند.

### ✨ چرا N13؟

دانلود کردن باید ساده باشد:

**لینک را کپی کن → به N13 بده → بقیه کار را به N13 بسپار.**

N13 در عین سادگی برای استفاده روزمره، ابزارهای لازم برای کنترل دقیق دانلودها را هم در اختیار شما قرار می‌دهد.

---

## ⚡ امکانات اصلی

- 🚀 دانلود چندریسمانی با حداکثر **۶۴ اتصال**
- ▶️ ادامه دانلودهای قطع‌شده در صورت پشتیبانی سرور
- 📦 دانلود گروهی و لیست لینک‌ها
- 🌐 اتصال مستقیم به مرورگر
- 📋 نظارت بر کلیپ‌بورد
- 🗓️ زمان‌بندی دانلود
- ⏱️ محدود کردن سرعت و پهنای باند
- 🧠 بهینه‌سازی هوشمند تعداد اتصال‌ها
- 🗂️ دسته‌بندی و قوانین خودکار دانلود
- 📥 صف دانلود قدرتمند
- 🔐 بررسی MD5 و SHA-256
- 🍪 پشتیبانی از Cookie
- 🔬 تحلیل لینک قبل از دانلود
- 🛡️ محافظت در برابر SSRF و ارتباط امن مرورگر
- 🗄️ ذخیره دائمی اطلاعات دانلود با SQLite
- 🖥️ رابط گرافیکی مدرن
- 💻 رابط خط فرمان
- 🖱️ پشتیبانی از System Tray ویندوز
- 🔄 پشتیبانی از بروزرسانی خودکار
- 🌍 رابط انگلیسی و فارسی با پشتیبانی کامل RTL

---

# 🎯 N13 فقط یک دانلودر ساده نیست

N13 کل فرایند دانلود را مدیریت می‌کند.

### ۱. دریافت

می‌توانید دانلود را از روش‌های مختلف شروع کنید:

- لینک مستقیم
- مرورگر
- کلیپ‌بورد
- فایل حاوی URLها
- دانلود گروهی
- خط فرمان

### ۲. مدیریت

تمام دانلودها را از یک صف مدیریت کنید:

- توقف
- ادامه
- تلاش مجدد
- حذف
- جابه‌جایی
- تعیین اولویت
- عملیات روی چند دانلود به‌صورت همزمان

### ۳. خودکارسازی

کارهای تکراری را به N13 بسپارید:

- زمان‌بندی دانلود
- اعمال قوانین خودکار
- دسته‌بندی فایل‌ها
- محدود کردن پهنای باند
- خاموش کردن ویندوز پس از پایان صف دانلود

### ۴. بررسی

اطمینان حاصل کنید فایل دانلودشده همان چیزی است که انتظار داشتید:

- بررسی MD5
- بررسی SHA-256
- اطلاعات دانلود
- تاریخچه دائمی وظایف

---

# 🖥️ رابط گرافیکی مدرن

N13 یک رابط گرافیکی مدرن برای مدیریت دانلودها در ویندوز دارد.

امکانات رابط گرافیکی شامل:

- 📊 داشبورد
- 📥 صف دانلود
- 🔎 جزئیات دانلود
- 🗂️ دسته‌بندی‌ها
- 🗓️ زمان‌بندی
- ⚙️ تنظیمات
- 🖱️ System Tray
- ⌨️ کنترل با صفحه‌کلید
- 🌍 زبان انگلیسی و فارسی
- ↔️ پشتیبانی کامل از RTL در فارسی

هدف رابط N13 این است که اطلاعات مهم را در دسترس نگه دارد، بدون اینکه برنامه به یک پنل پیچیده و شلوغ تبدیل شود.

---

# 🚀 دانلود چندریسمانی

N13 می‌تواند دانلودهای پشتیبانی‌شده را به چند اتصال موازی تقسیم کند.

در صورتی که سرور از HTTP Range Request پشتیبانی کند، این روش می‌تواند استفاده از پهنای باند موجود را بهتر کند.

تعداد اتصال‌ها قابل کنترل است و N13 از حداکثر **۶۴ ریسمان** پشتیبانی می‌کند.

> سرعت واقعی به سرور، اینترنت، اندازه فایل، محدودیت‌های اتصال و شرایط شبکه بستگی دارد. N13 سرعت مشخصی را تضمین نمی‌کند.

---

# ▶️ ادامه دانلودهای قطع‌شده

قطع شدن اینترنت نباید همیشه به معنی شروع دوباره دانلود باشد.

اگر سرور از ادامه دانلود پشتیبانی کند، N13 می‌تواند دانلود را از محل توقف ادامه دهد.

این قابلیت برای موارد زیر بسیار کاربردی است:

- فایل‌های حجیم
- اینترنت ناپایدار
- دانلودهای طولانی
- دانلودهای زمان‌بندی‌شده

---

# 📦 دانلود گروهی

اگر تعداد زیادی فایل برای دانلود دارید، لازم نیست همه را یکی‌یکی شروع کنید.

N13 از مواردی مانند زیر پشتیبانی می‌کند:

- لیست URL
- فایل متنی
- CSV
- چند دانلود در صف
- اسکن الگوهای URL

لینک‌ها را وارد صف کنید و اجازه دهید N13 آن‌ها را مدیریت کند.

---

# 🌐 اتصال به مرورگر

N13 می‌تواند مرورگر را مستقیماً به دانلود منیجر متصل کند.

روش‌های پشتیبانی‌شده شامل:

- اتصال به افزونه Chrome
- پروتکل `dldm://`
- Browser Relay محلی
- گزینه **Send to N13 Download Manager**
- ساخت و تعمیر افزونه

فرایند ساده است:

**فایل را در مرورگر پیدا کن → به N13 بفرست → دانلود را در N13 مدیریت کن.**

---

# 📋 نظارت بر کلیپ‌بورد

در صورت فعال بودن، N13 می‌تواند کلیپ‌بورد را برای URLهای کپی‌شده بررسی کند.

این قابلیت برای زمانی مفید است که مرتب لینک دانلود کپی می‌کنید و نمی‌خواهید هر بار برنامه را به‌صورت دستی باز کنید.

این قابلیت اختیاری است و می‌توان آن را غیرفعال کرد.

---

# 🧠 بهینه‌سازی هوشمند اتصال‌ها

N13 می‌تواند تعداد اتصال‌ها را بر اساس شرایط دانلود تنظیم کند؛ از جمله:

- اندازه فایل
- رفتار سرور
- پایداری اتصال

هدف این است که برنامه برای هر فایل بدون توجه به شرایط، به‌صورت کورکورانه از حداکثر اتصال‌ها استفاده نکند.

---

# 🗂️ قوانین و دسته‌بندی خودکار

دانلودها را به‌صورت خودکار مرتب کنید.

می‌توانید قوانینی تعریف کنید تا فایل‌ها بر اساس روند کاری شما در پوشه یا دسته مناسب قرار بگیرند.

مثال:

```text
Videos      → D:/Downloads/Videos
Programs    → D:/Downloads/Programs
Archives    → D:/Downloads/Archives
Documents  → D:/Downloads/Documents
```

---

# 📥 صف دانلود قدرتمند

صف دانلود مرکز مدیریت دانلودهای N13 است.

می‌توانید:

- دانلودها را با Drag & Drop جابه‌جا کنید
- اولویت تعیین کنید
- دانلودها را متوقف و ادامه دهید
- دانلودهای ناموفق را دوباره امتحان کنید
- چند دانلود را همزمان حذف کنید
- روی چند دانلود انتخاب‌شده عملیات انجام دهید
- با صفحه‌کلید در صف حرکت کنید
- ترتیب دانلودهای بعدی را کنترل کنید

---

# 🗓️ زمان‌بندی دانلود

به‌جای شروع دستی دانلودها، زمان آن‌ها را مشخص کنید.

امکان تنظیم موارد زیر وجود دارد:

- ساعت شروع
- ساعت پایان اختیاری
- روزهای هفته
- محدودیت سرعت در ساعات مشخص

این قابلیت برای فایل‌های حجیمی که می‌خواهید در ساعات خاصی دانلود شوند بسیار مناسب است.

---

# ⏱️ کنترل پهنای باند

اجازه ندهید دانلود تمام اینترنت شما را مصرف کند.

N13 امکان کنترل سرعت و پهنای باند را فراهم می‌کند تا بتوانید همزمان از اینترنت برای کارهای دیگری مانند:

- وب‌گردی
- استریم
- بازی
- تماس تصویری
- دانلودهای دیگر

استفاده کنید.

---

# 🔌 خاموش شدن خودکار ویندوز

اگر یک دانلود حجیم را قبل از خواب شروع می‌کنید، می‌توانید N13 را طوری تنظیم کنید که پس از پایان موفق صف دانلود، ویندوز را خاموش کند.

برای جلوگیری از خاموش شدن ناخواسته، امکان لغو خاموش شدن نیز وجود دارد.

---

# 🔐 بررسی صحت فایل

N13 از بررسی صحت فایل با الگوریتم‌های زیر پشتیبانی می‌کند:

- **MD5**
- **SHA-256**

با این قابلیت می‌توانید فایل دانلودشده را با Hash مورد انتظار مقایسه کنید و خرابی یا تغییر فایل را تشخیص دهید.

---

# 🍪 پشتیبانی از Cookie

برخی لینک‌های دانلود به احراز هویت یا Cookie مرورگر نیاز دارند.

N13 از روش‌های مختلف Cookie پشتیبانی می‌کند:

- Raw Cookie Header
- فایل `cookies.txt`
- Cookieهای زنده مرورگر

---

# 🔬 تحلیل لینک

قبل از شروع دانلود، N13 می‌تواند اطلاعات لینک را بررسی کند، مانند:

- نام فایل
- حجم فایل
- نوع محتوا
- پشتیبانی از Range Request

این کار به N13 کمک می‌کند قبل از شروع دانلود، نحوه مدیریت فایل را بهتر مشخص کند.

---

# 🛡️ امنیت

امنیت بخشی از معماری N13 است.

برخی از کنترل‌ها و محافظت‌های پروژه شامل:

- محافظت SSRF در برابر دسترسی به شبکه‌های خصوصی و محلی
- توکن اختصاصی برای Browser Relay هر دستگاه
- بررسی SHA-256 بسته‌های بروزرسانی
- قرار گرفتن `token.json` در Git Ignore

برای گزارش مشکلات امنیتی، دستورالعمل‌های امنیتی پروژه را مطالعه کنید.

---

# 🗄️ تاریخچه دائمی دانلود

N13 برای ذخیره اطلاعات وظایف و تاریخچه دانلود از SQLite استفاده می‌کند.

اطلاعات دانلودها می‌تواند بعد از بسته شدن و اجرای دوباره برنامه نیز حفظ شود.

---

# 💻 رابط خط فرمان

اگر با Command Line راحت‌تر هستید، N13 یک رابط مبتنی بر Rich نیز دارد که پیشرفت دانلود را به‌صورت زنده نمایش می‌دهد.

بنابراین N13 هم برای:

- استفاده روزمره روی دسکتاپ
- اسکریپت‌ها و کارهای توسعه‌دهندگان

قابل استفاده است.

---

# 🖱️ System Tray ویندوز

N13 می‌تواند در System Tray ویندوز اجرا شود.

از طریق Tray می‌توانید به گزینه‌هایی مانند موارد زیر دسترسی داشته باشید:

- توقف / ادامه دانلود
- باز کردن پوشه دانلود
- تنظیمات
- مشاهده سرعت فعلی دانلود

---

# 🔄 بروزرسانی خودکار

N13 می‌تواند نسخه‌های جدید را از GitHub Releases بررسی کند.

بسته‌های بروزرسانی قبل از نصب با SHA-256 بررسی می‌شوند و برنامه می‌تواند پس از بروزرسانی دوباره اجرا شود.

---

# 🌍 زبان‌ها

N13 در حال حاضر از این زبان‌ها پشتیبانی می‌کند:

- 🇬🇧 انگلیسی
- 🇮🇷 فارسی

رابط فارسی دارای پشتیبانی از **RTL** است.

---

# 📥 نصب

## ویندوز — روش پیشنهادی

برای کاربران عادی ویندوز، استفاده از Installer ساده‌ترین روش نصب N13 است.

نصب‌کننده ویندوز برای موارد زیر طراحی شده است:

- عدم نیاز به نصب جداگانه Python
- نصب برنامه
- مدیریت WebView2
- ثبت پروتکل `dldm://`
- پشتیبانی از بروزرسانی برنامه

آخرین نسخه را از بخش **Releases** ریپازیتوری دریافت کنید.

---

# 🛠️ اجرای پروژه از سورس

### پیش‌نیازها

- Windows
- Python 3.10+
- Git

ریپازیتوری را دریافت کنید:

```bash
git clone https://github.com/SOHAYB-N13/n13-download.git
cd n13-download
```

وابستگی‌ها را نصب کنید:

```bash
pip install -r requirements.txt
```

اجرای رابط خط فرمان:

```bash
python d.py
```

اجرای رابط گرافیکی:

```bash
python d.py --gui
```

---

# 🌐 راه‌اندازی مرورگر

برای اتصال Chrome به N13:

### ۱. ثبت پروتکل

```bash
python d.py --register
```

### ۲. ساخت افزونه

```bash
python d.py --create-extension
```

### ۳. اضافه کردن افزونه به Chrome

در Chrome وارد شوید:

```text
chrome://extensions
```

گزینه **Developer mode** را فعال کنید، سپس **Load unpacked** را انتخاب کرده و پوشه افزونه ساخته‌شده را انتخاب کنید.

### ۴. ارسال لینک به N13

روی یک لینک مناسب کلیک راست کنید و گزینه زیر را انتخاب کنید:

```text
Send to N13 Download Manager
```

> 🔒 فایل `token.json` شامل توکن اختصاصی Browser Relay است و نباید در Git commit شود.

---

# 🎯 استفاده از خط فرمان

### دانلود یک فایل

```bash
python d.py "https://example.com/file.zip"
```

### استفاده از چند اتصال

```bash
python d.py "https://example.com/file.zip" -t 8
```

### انتخاب پوشه دانلود

```bash
python d.py "https://example.com/file.zip" -d "D:/Downloads"
```

### بررسی فایل

```bash
python d.py "https://example.com/file.zip" --checksum "sha256:..."
```

---

# ⚙️ گزینه‌های خط فرمان

| گزینه | توضیح |
|---|---|
| `<url>` | لینک دانلود |
| `-d, --dir <path>` | پوشه دانلود |
| `-t, --threads <n>` | تعداد اتصال‌های دانلود |
| `--checksum <hash>` | Hash مورد انتظار MD5 یا SHA-256 |
| `--insecure-ssl` | غیرفعال کردن SSL Verification در صورت فعال‌سازی صریح |
| `--from-browser` | مشخص کردن اینکه URL از مرورگر آمده است |
| `--url-file <path>` | دریافت URLها از یک فایل |
| `--register` | ثبت پروتکل `dldm://` |
| `--unregister` | حذف پروتکل `dldm://` |
| `--create-extension` | ساخت افزونه مرورگر |
| `--gui` | اجرای رابط گرافیکی |

---

# 🧩 ساختار پروژه

```text
n13-download/
├── batch/
├── browser/
├── chrome_extension/
├── config/
├── core/
├── extension/
├── installer/
├── ui/
├── d.py
├── requirements.txt
├── PACKAGING.md
├── SECURITY.md
└── README.md
```

ساختار پروژه بخش‌های مربوط به موتور دانلود، اتصال مرورگر، رابط کاربری، تنظیمات، بسته‌بندی و اجزای جانبی را از یکدیگر جدا نگه می‌دارد.

---

# 🧪 توسعه

N13 یک پروژه متن‌باز است که توسعه آن ادامه دارد.

تمرکز توسعه پروژه روی موارد زیر است:

- قابلیت اطمینان دانلود
- عملکرد و سرعت
- مدیریت صف
- اتصال به مرورگر
- یکپارچگی با ویندوز
- خودکارسازی
- امنیت
- تجربه کاربری
- چندزبانه بودن

گزارش باگ، پیشنهاد قابلیت جدید، بهبود مستندات و Pull Request همگی مورد استقبال هستند.

---

# 🛣️ مسیر توسعه

N13 همچنان در حال پیشرفت است.

برخی از زمینه‌های احتمالی توسعه آینده:

- پشتیبانی از مرورگرهای بیشتر
- تشخیص قدرتمندتر لینک‌ها
- مدیریت بهتر اتصال‌ها
- قابلیت‌های بیشتر برای خودکارسازی
- بهبود رابط کاربری و تجربه کاربری
- پشتیبانی از پروتکل‌ها و منابع دانلود بیشتر
- پشتیبانی از پلتفرم‌های بیشتر

---

# 🤝 مشارکت در پروژه

اگر باگی پیدا کردید یا می‌خواهید N13 را بهتر کنید:

1. یک Issue ایجاد کنید.
2. مشکل یا پیشنهاد خود را واضح توضیح دهید.
3. برای باگ‌ها، مراحل بازتولید مشکل را تا حد امکان بنویسید.
4. برای تغییرات کد، Pull Request ارسال کنید.

قبل از گزارش مشکلات امنیتی، فایل `SECURITY.md` را مطالعه کنید.

---

# 📄 مجوز

N13 Download Manager تحت **MIT License** منتشر شده است.

Copyright © 2026 SOHAYB N13

---

# ⭐ حمایت از N13

اگر N13 برای شما مفید است:

- ⭐ به ریپازیتوری Star بدهید
- 🐛 باگ‌ها را گزارش کنید
- 💡 قابلیت‌های جدید پیشنهاد دهید
- 🔧 در توسعه مشارکت کنید
- 📢 پروژه را با دیگران به اشتراک بگذارید

هر Star، Issue، Contribution و بازخورد به رشد N13 کمک می‌کند.

---

<div align="center">

### ⬇️ سریع‌تر دانلود کن. هوشمندانه‌تر مدیریت کن. کنترل دست خودت باشد.

**N13 Download Manager**

ساخته‌شده با ❤️ و Python.

</div>
