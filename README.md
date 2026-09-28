⬇️ N13 Download Manager

A modern, open-source download manager for Windows — built for speed, control, and reliability.

N13 Download Manager is a multi-threaded download manager designed to make downloading large files easier, faster, and more reliable.

Split downloads into parallel connections, resume interrupted downloads, organize your queue, schedule downloads, send links directly from your browser, control bandwidth, and manage everything from a modern graphical interface.

«No Python required for the Windows installer.»

---

✨ Why N13?

Downloading a file should be simple.

Copy a link → send it to N13 → let N13 handle the rest.

N13 combines the features you would expect from a modern download manager with tools for users who want more control over how their downloads work.

🚀 Built for real downloads

- ⚡ Multi-threaded downloads
- ▶️ Resume interrupted downloads
- 📦 Batch downloads
- 🌐 Browser integration
- 📋 Clipboard monitoring
- 🗓️ Download scheduling
- ⏱️ Bandwidth control
- 🧠 Smart connection optimization
- 🗂️ Automatic download rules and categories
- 🖥️ Modern graphical interface
- 💻 Terminal interface
- 🖱️ System tray integration
- 🔄 Automatic updates
- 🗄️ Persistent download history
- 🔐 Checksum verification
- 🌍 English and Persian UI

---

🎯 What makes N13 different?

N13 is not just a downloader that starts a request and waits for it to finish.

It is designed around the entire download workflow.

1. Download

Start downloads using a URL, clipboard, batch list, command line, or directly from your browser.

2. Manage

Pause, resume, retry, remove, prioritize, reorder, and organize downloads from one place.

3. Automate

Use scheduling, automatic categories, download rules, bandwidth limits, and optional shutdown after the queue finishes.

4. Monitor

Track progress, speed, network activity, memory usage, download details, and history from the application.

---

⚡ Features

🚀 Multi-threaded Downloads

N13 can split a download into multiple parallel parts and download them simultaneously.

This allows the application to make better use of available connections when the server supports range requests.

Up to 64 download threads are supported.

---

▶️ Resume Interrupted Downloads

A failed connection or interrupted download does not necessarily mean starting again from zero.

When supported by the server, N13 can resume a download from where it stopped.

---

📦 Batch Downloads

Need to download many files?

N13 supports batch workflows including:

- URL lists
- Text files
- CSV imports
- URL pattern scanning
- Multiple downloads in a managed queue

---

🌐 Browser Integration

Send downloads directly from your browser to N13.

N13 provides:

- Chrome extension integration
- "dldm://" protocol support
- Local relay server
- Right-click Send to N13 Download Manager
- Extension repair from the application

The browser integration is designed so you do not have to manually copy and paste every download link.

---

📋 Clipboard Monitor

Enable the optional clipboard monitor and N13 can detect copied URLs automatically.

This is especially useful when working with download links repeatedly.

---

🧠 Smart Connection Optimizer

N13 can automatically adjust the number of connections based on factors such as:

- File size
- Server stability
- Download conditions

The goal is to avoid blindly using the maximum number of connections for every file.

---

🗂️ Download Rules & Categories

Automatically organize downloads.

Create rules that route downloads to the appropriate folder or category instead of manually choosing a destination every time.

For example:

Videos      → D:/Downloads/Videos
Programs    → D:/Downloads/Programs
Archives    → D:/Downloads/Archives
Documents  → D:/Downloads/Documents

---

📥 Powerful Download Queue

The queue gives you control over what happens next.

You can:

- Reorder downloads with drag & drop
- Set priorities
- Pause and resume tasks
- Retry failed downloads
- Remove multiple tasks
- Perform actions on multiple selected downloads
- Navigate the queue using the keyboard
- Control the order of upcoming downloads

---

🗓️ Scheduler

Schedule when downloads are allowed to run.

Configure:

- Start time
- Optional end time
- Days of the week
- Night-time speed limits

This makes it possible to let large downloads run during specific hours without manually starting them.

---

⏱️ Bandwidth Control

Do not let downloads consume all available bandwidth.

N13 provides speed controls so downloads can be limited when you need the connection for other tasks.

---

🔌 Shutdown After Downloads

Going to sleep while a large download is running?

N13 can optionally shut down Windows after the download queue finishes successfully.

The shutdown includes a cancellation window so you can stop it if needed.

---

🔐 Download Verification

N13 supports checksum verification using:

- MD5
- SHA-256

This allows you to verify that a downloaded file matches an expected checksum.

---

🍪 Cookie Support

Some downloads require browser authentication or cookies.

N13 supports cookie-based download workflows through:

- Raw cookie headers
- "cookies.txt"
- Live browser cookies

---

🔬 URL Analyzer

Before starting a download, N13 can inspect the URL and determine information such as:

- File name
- File size
- Content type
- Range support

This helps the application understand how a download can be handled before it begins.

---

🛡️ Security

N13 includes SSRF protection designed to prevent downloads from accessing private or local IP ranges.

Browser communication also uses a per-machine relay token.

Sensitive "token.json" files are intentionally excluded from Git.

---

🗄️ Persistent Download History

N13 uses SQLite for persistent task and download history storage.

Your queue and download information can survive application restarts instead of disappearing when the application closes.

---

🖥️ Two Interfaces

N13 provides two ways to interact with the application.

🌐 Graphical Interface

A modern dark graphical interface built with PyWebView.

The GUI includes:

- Dashboard
- Download queue
- Download details
- Categories
- Scheduling
- Settings
- System tray
- Multi-selection actions
- Keyboard navigation
- English and Persian localization
- Full RTL support for Persian

💻 Terminal Interface

Prefer the command line?

N13 also provides a Rich-based terminal interface with live download progress.

---

📊 Dashboard

The dashboard gives you an overview of your downloads without forcing you to open multiple screens.

It provides grouped information about:

- Active downloads
- Download activity
- Speed and network usage
- Memory usage

---

🖱️ System Tray

N13 can run from the Windows system tray.

From the tray you can access actions such as:

- Pause / Resume
- Open download folder
- Settings
- Current download speed

---

🔄 Automatic Updates

N13 can check GitHub Releases for new versions.

Updates are verified using SHA-256 before installation and the application can restart itself after updating.

---

🌍 Languages

N13 currently supports:

- 🇬🇧 English
- 🇮🇷 Persian / Farsi

The Persian interface includes RTL layout support.

---

💿 Installation

Windows — Recommended

Download the latest Windows installer and run it.

The installer:

- Requires no Python installation
- Installs the application
- Handles WebView2 setup
- Registers the "dldm://" protocol
- Supports application updates

For normal Windows users, the installer is the easiest way to get started.

---

🛠️ Run From Source

If you want to develop or run N13 directly from source, you need:

- Windows
- Python 3.10+
- Git

Clone the repository:

git clone https://github.com/SOHAYB-N13/n13-download.git
cd n13-download

Install dependencies:

pip install -r requirements.txt

Launch the terminal interface

python d.py

Launch the graphical interface

python d.py --gui

---

🎯 Command Line Usage

Download a file

python d.py "https://example.com/file.zip"

Download using 8 threads

python d.py "https://example.com/file.zip" -t 8

Choose a download directory

python d.py "https://example.com/file.zip" -d "D:/Downloads"

Verify a download

python d.py "https://example.com/file.zip" --checksum "sha256:..."

---

🌐 Browser Setup

N13 can integrate with Chrome so links can be sent directly to the download manager.

1. Register the protocol

python d.py --register

2. Create the Chrome extension

python d.py --create-extension

3. Open Chrome extensions

Go to:

chrome://extensions

Enable Developer mode and choose Load unpacked.

Select the generated extension folder.

4. Send a link to N13

Right-click a link in Chrome and choose:

Send to N13 Download Manager

«🔒 "token.json" contains a machine-specific relay token and is intentionally ignored by Git. Never commit it to the repository.»

---

⚙️ Command Line Options

Option| Description
"<url>"| Download URL
"-d, --dir <path>"| Download directory
"-t, --threads <n>"| Number of download threads
"--checksum <hash>"| Expected MD5 or SHA-256 hash
"--insecure-ssl"| Disable SSL verification when explicitly enabled
"--from-browser"| Treat the URL as browser-originated
"--url-file <path>"| Read URLs from a file
"--register"| Register the "dldm://" protocol
"--unregister"| Remove the "dldm://" protocol
"--create-extension"| Generate the Chrome extension
"--gui"| Launch the graphical interface

---

🧩 Project Structure

N13 is organized into separate components so the download engine, browser integration, interface, configuration, and packaging can evolve independently.

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

---

🧪 Development

N13 is an open-source project and development is ongoing.

The project focuses on:

- Reliable downloading
- Better queue management
- Browser integration
- Windows integration
- Performance
- User experience
- Security
- Internationalization

Bug reports, feature requests, and improvements are welcome.

---

🛣️ Roadmap

N13 is actively evolving.

Future development may focus on areas such as:

- More browser integrations
- Improved download detection
- Better connection management
- More automation
- Additional platform support
- UI and UX improvements
- More download protocols and sources

---

🤝 Contributing

Contributions are welcome.

If you find a bug, have an idea, or want to improve N13:

1. Open an issue.
2. Describe the problem or proposed improvement.
3. Include reproduction steps when reporting a bug.
4. Submit a pull request for code changes.

Please read the project security guidelines before reporting security-related issues.

---

📄 License

N13 Download Manager is released under the MIT License.

Copyright © 2026 SOHAYB N13

---

⭐ Support the Project

If you find N13 useful:

- ⭐ Star the repository
- 🐛 Report bugs
- 💡 Suggest improvements
- 🔧 Contribute code
- 📢 Share the project with others

Every star, issue, and contribution helps the project grow.

---

Built with Python ❤️

N13 Download Manager

Download faster. Download smarter. Stay in control.
