# 🖨️ MFP Print & Scan Server

**English** · [Русский](README.ru.md)

A small self-hosted web server that turns a USB printer/scanner (MFP, multifunction unit) into a network device for your home or small office:

- **print** any PDF or image from a browser — on a phone, tablet or another computer — with a real print preview;
- **add it as a network printer** on other machines: the server speaks **IPP Everywhere**, so Windows, Linux (CUPS), macOS, Android and iOS clients use their **built-in driverless driver**. You don't need the printer's own driver on each client;
- **scan** from the browser like [phpSane](https://github.com/gawindx/phpSane): preview the glass, select an area, pick resolution/mode/format, merge pages into one PDF, or make a **copy** at actual size;
- **add it as a network scanner** too: the server also speaks **AirScan (eSCL)**, so macOS, iOS and Android scan with their own built-in scanning, no app needed;
- **maintain** the printer: OS test page, and for Canon PIXMA — nozzle check and print-head cleaning.

It runs on **Windows** (Win32 print spooler + WIA) and **Linux** (CUPS + SANE). The UI is in **English and Russian**.

![Print tab](docs/screenshots/print-en.png)

<details>
<summary>More screenshots</summary>

![Scan tab](docs/screenshots/scan-en.png)
![Russian UI](docs/screenshots/print-ru.png)

</details>

---

## Contents

- [Why](#why)
- [Features](#features)
- [Tested hardware](#tested-hardware)
- [Quick start](#quick-start)
  - [Windows](#windows)
  - [Linux](#linux)
  - [Running as a service](#running-as-a-service)
- [Using it](#using-it)
  - [Printing from the browser](#printing-from-the-browser)
  - [Adding it as a network printer (IPP)](#adding-it-as-a-network-printer-ipp)
  - [Scanning and copying](#scanning-and-copying)
  - [Scanning from another device (AirScan/eSCL)](#scanning-from-another-device-airscanescl)
  - [Printer maintenance](#printer-maintenance)
  - [Language](#language)
- [Configuration](#configuration)
  - [Users and sign-in](#users-and-sign-in)
- [How it works](#how-it-works)
- [HTTP API](#http-api)
- [Data on disk](#data-on-disk)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Extending](#extending)
- [Development](#development)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Why

Cheap inkjet MFPs such as the Canon PIXMA MG2500 series have **only USB**: no Wi‑Fi, no network printing, no scanning from a phone. Sharing them the Windows way needs a vendor driver on every PC and doesn't help phones at all. This project puts one always-on machine next to the printer and makes the printer available to everything on the LAN:

- anything with a browser can print and scan;
- anything with an IPP client (all modern OSes) can print with its built-in driver;
- anything with an eSCL/AirScan client (macOS, iOS, Android) can scan the same way.

## Features

### Printing
- Upload a file (drag & drop), choose printer, copies and **options read from the printer driver**: paper size, paper type, quality, color, duplex. Nothing is hard-coded, so another printer shows its own options.
- **PDF and images are rendered by the server itself** (PDFium / Pillow) straight into the printer. Printing therefore doesn't depend on which programs are installed, and settings apply to that one job only, not to the printer's defaults.
- **Accurate preview** for PDFs and images: the real sheet size and the printer's hardware margins, pages fitted exactly the way they'll print, landscape pages auto-rotated, grayscale for B&W. Preview and printing share the same layout code.
- **Documents and text** (`.txt`, `.rtf`, `.docx`, `.odt`, `.xlsx`, `.pptx`, …): with [LibreOffice](https://www.libreoffice.org/) installed on the server they're converted to PDF and then printed and previewed exactly like a PDF, on Windows and Linux. Non-UTF-8 text files (e.g. Windows-1251) are re-encoded first, so Cyrillic isn't garbled. Without LibreOffice they're handed to the program registered to print them (Notepad, Word/WordPad, …) and there's no preview.
- **Paper type ↔ size rules**: some printers reject certain combinations, e.g. glossy photo paper in 13×18 on a Canon (error 4102). The server swaps in a compatible paper type and records a note on the job. The UI does the same as you pick options.
- **Job history**: status, settings and notes. Stored in a JSON file, so it survives restarts. Delete single entries or clear it all; uploaded files are removed with their entries.

### Network printer (IPP Everywhere)
- The server is an IPP/2.0 printer on port 631 (`ipp://<host>:631/ipp/print`).
- Clients render documents themselves and send **PDF, PWG Raster or JPEG**, sized to the media and margins the server advertises. The server prints them 1:1.
- Capabilities come from the real driver: 20+ paper sizes with standard PWG names and real margins, paper types, color, duplex, quality.
- **Bonjour/mDNS announcement** (Windows, `mdns = yes` by default): macOS, iOS and Android find the printer by name in "Add Printer" instead of needing its address typed in.
- Tested with Linux/CUPS clients (text editor, LibreOffice, PDFs, photos).

### Scanning
- Scanner capabilities from the driver: glass size, resolutions, modes, brightness/contrast.
- Low-resolution **preview of the whole glass**. **Select an area with the mouse**, or pick a preset: A4, A5, A6, Letter, 4×6 photo, business card.
- Color / grayscale / B&W (text), 75–600 dpi (whatever the scanner supports); shows the resulting pixel size.
- Save as **JPEG, PNG, TIFF or PDF** (correct DPI stored in the file).
- Gallery of scans: open, download, delete, **merge selected scans into one multi-page PDF** (in the order you tick them).
- **Copy**: print a scan at its real physical size (a scanned business card prints as a business card, not blown up to A4).
- **AirScan/eSCL**: the scanner is also a driverless network scanner (`escl = yes`, on **both** Windows and Linux) — macOS "Image Capture", iOS's built-in scan and Android scan directly, no app or this web UI needed. Bonjour-discoverable like the printer.

### Maintenance
- **Test page**: the operating system's standard test page, for any printer.
- **Nozzle check** and **head cleaning** for Canon inkjets: byte-for-byte the maintenance job the Canon driver itself sends (IVEC mode switch + BJL commands), captured from the Windows driver's print queue.

### Other
- Optional **sign-in** (`auth = yes` in `config.ini`): users and passwords (hashed or plain) in the config, remembered sessions, brute-force lockout, HTTP Basic for scripts, optional Basic auth for IPP.
- Optional **HTTPS for the web UI**: point `ssl_certfile` / `ssl_keyfile` in `config.ini` at a certificate and key, and the web port serves TLS instead of plain HTTP — no reverse proxy needed if you already have a certificate (e.g. from your router, a LAN CA, or `mkcert`).
- One **config file** (`config.ini`) for language, ports, folders and users.
- **English / Russian** UI with a remembered choice; server messages follow the page language.
- **Remembered print/scan settings**: the last printer, copies, per-printer options, and the last scanner, area, mode, resolution, brightness/contrast and format are kept in a cookie in your browser (not on the server) and pre-filled next time.
- Works from phones (responsive layout).
- One-click start on Windows (`start.bat`) and a start script for Linux (`start.sh`).
- **No admin rights needed** on Windows: per-user printer settings, spooler access only.

## Tested hardware

| Component | Status |
|---|---|
| Windows 10 Enterprise LTSC 2021 (21H2), Python 3.12 | ✅ server: printing, preview, IPP, scanning, maintenance UI |
| Canon PIXMA MG2541 (driver "Canon MG2500 series Printer", USB) | ✅ printing (PDF, images, text), WIA scanning |
| Linux client (CUPS, driverless IPP Everywhere) → this server | ✅ printing from GNOME Text Editor, LibreOffice, PDF viewer |
| Windows client with "Microsoft IPP Class Driver" | ⚠️ implemented per spec, not yet confirmed on a real client |
| Bonjour/mDNS advertisement (`zeroconf`) | ⚠️ TXT record verified correct with `avahi-browse` against a stand-in printer object on Linux; not yet run for real on Windows, and no macOS/iOS/Android device has tried discovering it |
| HTTPS for the web UI (`ssl_certfile`/`ssl_keyfile`) | ✅ tested on Linux: serves TLS only on the web port, falls back to plain HTTP cleanly if only one of the two is set |
| AirScan/eSCL server (Linux, real Canon PIXMA MG2500) | ✅ ScannerCapabilities/ScannerStatus/ScanJobs/NextDocument all tested against real hardware: correct bed size and formats reported, a real scan came back as PDF and as grayscale JPEG with the requested region cropped correctly (verified pixel size and content); mDNS advertisement seen by `avahi-browse` **and independently picked up by SANE's own `escl`/`airscan` client backends** on the same machine, with a matching UUID. That self-discovery also surfaced and let us fix a real bug: it made `scanimage -L` ~12x slower and occasionally hit a transient "device busy" — see "A Linux quirk this surfaced" in [How it works](#how-it-works) |
| AirScan/eSCL server (Windows) | ⚠️ not yet run on a real Windows machine |
| AirScan/eSCL discovery from a real macOS/iOS/Android device | ⚠️ not yet tried — only verified via `avahi-browse` and SANE's own eSCL client, not Apple's/Google's actual client software |
| Linux server (CUPS printing) | ✅ printing was tested at the start of the project |
| Linux server — SANE scanning (preview, area select, color/gray/lineart, JPEG/PNG/TIFF/PDF, merge to PDF) | ✅ tested end-to-end against a real Canon PIXMA MG2500 over `scanimage` |
| Linux server — print preview (PDF/image rendering, margins) | ✅ tested; exact hardware margins aren't available on Linux (falls back to A4 + 5 mm) |
| Linux server — maintenance: OS test page, Canon nozzle check and head cleaning (via CUPS/`lp`) | ✅ confirmed on the MG2500 — printed both the CUPS test page and the nozzle pattern, audibly ran head cleaning |
| Maintenance on Windows: test page (server run as administrator), Canon nozzle check and head cleaning | ✅ confirmed on the MG2541 |
| Documents via LibreOffice 26.8 on Windows (TXT UTF-8 / Windows-1251, RTF, DOCX, XLSX) | ✅ conversion, preview and printing tested (printed to Microsoft Print to PDF) |
| Documents via LibreOffice 24.2 on Linux (TXT UTF-8 / Windows-1251, DOCX, RTF) | ✅ conversion, preview (correct Cyrillic) and conversion caching tested; the converted PDF reaches CUPS correctly — physical output on paper wasn't confirmed this run (printer was disconnected) |
| Windows autostart task (`register_service.bat`) | ✅ confirmed across a full computer restart: the Task Scheduler task registers, and the server comes up on its own before anyone logs on |
| Linux systemd unit | ✅ confirmed: installed under a dedicated `mfp` system user (enabled for boot), serving real requests and reaching the USB scanner via the `scanner` group |

Reports for other printers and scanners are very welcome, see [Extending](#extending).

## Quick start

### Windows

Requirements:
- Windows 10 or 11;
- [Python 3.11+ from python.org](https://www.python.org/downloads/windows/): tick **"Add python.exe to PATH"** during installation;
- the printer installed with its normal driver, so that printing from e.g. Notepad already works;
- optional: [LibreOffice](https://www.libreoffice.org/download/), to print and preview documents and text files exactly. It's found automatically in its standard install folder; restart the server after installing it.

Steps:
1. Download or clone this repository.
2. Double-click **`start.bat`**.

On the first run it creates a virtual environment in `.venv` and installs dependencies. Later it reinstalls them only when `requirements.txt` changes. Then it shows the addresses to open:

```
[MFP] Web UI:         http://localhost:8000
[MFP] On the network: http://192.168.1.20:8000   IPP printer: ipp://192.168.1.20:631/ipp/print
```

Stop it with <kbd>Ctrl</kbd>+<kbd>C</kbd> or by closing the window.

**Start automatically:** to run it in the background from Windows startup, see [Running as a service](#running-as-a-service).

**Allow access from other devices:** Windows asks about the firewall the first time Python listens on the network; allow it for private networks. For IPP clients, also open port 631, and UDP 5353 for Bonjour/mDNS discovery to work (in an elevated PowerShell):

```powershell
New-NetFirewallRule -DisplayName "MFP Print & Scan Server (IPP)" -Direction Inbound -Protocol TCP -LocalPort 631 -Action Allow
New-NetFirewallRule -DisplayName "MFP Print & Scan Server (Web)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallRule -DisplayName "MFP Print & Scan Server (mDNS)" -Direction Inbound -Protocol UDP -LocalPort 5353 -Action Allow
```

> **Tip.** On Windows 10/11, "Let Windows manage my default printer" makes the most recently used printer the default one. If you want the server's default to stay on your MFP, turn it off in *Settings → Devices → Printers & scanners*.

### Linux

Requirements:
- Python 3.11+ with `venv` (`sudo apt install python3-venv`);
- CUPS with the printer configured (`lpstat -a` lists it);
- for scanning, SANE: `sudo apt install sane-utils`, and `scanimage -L` should list the scanner;
- optional: LibreOffice, to print office documents (`sudo apt install libreoffice`).

Steps:

```bash
git clone <this repo> mfp-print-scan-server
cd mfp-print-scan-server
chmod +x start.sh
./start.sh
```

On Linux the built-in IPP printer is disabled: CUPS already is an IPP server. Share the printer through CUPS instead (`sudo cupsctl --share-printers`, then tick "Share this printer" in the CUPS admin page).

### Running as a service

#### Windows: `register_service.bat` / `unregister_service.bat`

Double-click **`register_service.bat`** and approve the administrator prompt. It:

1. prepares `.venv` (the same as `start.bat`);
2. registers a **Task Scheduler task "MFP Print & Scan Server"** that:
   - starts **at Windows startup**, before anyone logs on, with no console window;
   - runs **as your user account without storing a password** (S4U logon), so it sees the same printers, printer settings and default printer as you;
   - restarts the server if it exits (every minute), with no run-time limit;
   - writes the log to **`logs\server.log`** (the previous log is kept as `server.log.1` once it passes 5 MB);
3. opens TCP 8000 and 631 in Windows Firewall for private/domain networks, and warns if your network is marked *Public*;
4. starts the server right away and checks that it answers.

Why a scheduled task and not a classic Windows service:
- `python.exe` can't act as a service program;
- a service running as SYSTEM wouldn't see your default printer and your per-user printer settings.

If a `start.bat` window is still running, close it first, otherwise the ports are busy. Running the script again updates the task. Ports come from `config.ini`, so run the script again after changing them there: the firewall rules follow.

Managing it afterwards:

| What | How |
|---|---|
| status, start, stop | Task Scheduler (`taskschd.msc`) → *MFP Print & Scan Server*, or `Get-ScheduledTask "MFP Print & Scan Server"` / `Start-ScheduledTask` / `Stop-ScheduledTask` |
| log | `logs\server.log` |
| remove | double-click **`unregister_service.bat`**: stops and deletes the task and removes the firewall rules. Job history, scans and logs stay |

> **Updating the code** while it runs as a service: stop the task (or run `unregister_service.bat`), replace the files, run `register_service.bat` again.

#### Linux: systemd

An example unit is in [`deploy/mfp-print-scan-server.service`](deploy/mfp-print-scan-server.service). It assumes the code lives in `/opt/mfp-print-scan-server` and runs as a dedicated `mfp` user:

```bash
# 1. Put the code in place and create a service account
sudo cp -r mfp-print-scan-server /opt/mfp-print-scan-server
sudo useradd --system --home /opt/mfp-print-scan-server --shell /usr/sbin/nologin mfp
sudo chown -R mfp:mfp /opt/mfp-print-scan-server

# 2. Create .venv and install dependencies as that user
sudo -u mfp /opt/mfp-print-scan-server/start.sh --setup-only

# 3. Install and start the service
sudo cp /opt/mfp-print-scan-server/deploy/mfp-print-scan-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mfp-print-scan-server
```

Check the unit before installing it:
- `User=` / `Group=`: the account the server runs as;
- `SupplementaryGroups=lp scanner`: groups for printing and for USB scanner access. systemd refuses to start if a listed group doesn't exist, so check with `getent group scanner` and drop the missing ones;
- `WorkingDirectory=` / `ExecStart=`: change them if the code isn't in `/opt/mfp-print-scan-server`;
- `Environment=`: any [configuration](#configuration) variables.

Managing it afterwards:

```bash
systemctl status mfp-print-scan-server       # state
journalctl -u mfp-print-scan-server -f       # live log
sudo systemctl restart mfp-print-scan-server # after updating the code or settings
sudo systemctl disable --now mfp-print-scan-server && sudo rm /etc/systemd/system/mfp-print-scan-server.service  # remove
```

## Using it

### Printing from the browser

1. Open `http://<server>:8000`.
2. Drop a file or click to choose one.
3. Pick the printer and options. The option list comes from that printer's driver.
4. Optionally press **Preview**. While the preview is open it updates on every change.
5. Press **Print**. The job appears in **Recent jobs**. If the server had to adjust something (e.g. the paper type), a ⚠ with an explanation is shown next to the status.

| Format | How it's printed | Preview |
|---|---|---|
| PDF | rendered by the server (PDFium) | ✅ exact |
| JPEG, PNG, BMP, GIF, TIFF, WebP | rendered by the server (Pillow) | ✅ exact |
| TXT, RTF, DOC/DOCX, ODT, XLS/XLSX, ODS, CSV, PPT/PPTX, ODP | **with LibreOffice installed:** converted to PDF, then like a PDF (Windows and Linux) | ✅ exact, with LibreOffice |
| the same, **without LibreOffice** | Windows: the program registered to print the file type; Linux: CUPS with the original file | — |

### Adding it as a network printer (IPP)

The server is an **IPP Everywhere** printer at `ipp://<server>:631/ipp/print`. It also answers on `ipp://<server>:631/` and `ipp://<server>:631/ipp`, because some clients only let you type a host name. Clients use their **own built-in driver**, and their print dialog shows the preview.

**Linux (CUPS)**
```bash
sudo lpadmin -p MFP -E -v ipp://192.168.1.20:631/ipp/print -m everywhere
```
Or in *Settings → Printers → Add*, or at `http://localhost:631/admin`: enter the URI and choose the "IPP Everywhere" / "driverless" driver.

**Windows 10/11**
1. Open *Settings → Devices → Printers & scanners → Add a printer or scanner → "The printer that I want isn't listed"*.
2. Choose *"Add a printer using an IP address or hostname"*.
3. Set *Device type* to **IPP Device** and enter the server's address.

Windows picks the **Microsoft IPP Class Driver** by itself.

**macOS / iOS / Android.** With `mdns = yes` (the default, Windows only — see [Configuration](#configuration)) the server advertises itself over Bonjour/mDNS, so "Add Printer" finds it by name, no address needed. Without it (or on Linux, where the OS's own IPP server handles sharing instead), add it manually the same way as above: macOS *System Settings → Printers → Add → IP*, protocol "IPP", address `192.168.1.20`, queue `ipp/print`.

What the server advertises is built from the real printer driver: paper sizes with their hardware margins, paper types, color, duplex, quality, and 300/600 dpi for raster clients. Jobs are printed page = sheet, so the margins the client laid out are exactly where they end up on paper.

### Scanning and copying

1. Open the **Scan** tab and put the document face down in the corner of the glass.
2. Press **Preview**: the whole glass at low resolution, a few seconds.
3. Drag on the preview to select an area, or choose a preset. Drag the selection to move it; a single click resets it to the whole glass.
4. Choose mode, resolution, brightness/contrast and format. The line below shows the resulting size in pixels.
5. Press **Scan**. The result appears in **Scans**.

In the gallery:
- tick scans and press **Merge selected into PDF**; pages go in the order you ticked them;
- **Print** makes a copy at actual size on the printer selected on the Print tab.

### Scanning from another device (AirScan/eSCL)

With `escl = yes` (the default, Windows and Linux) the scanner is also available as a driverless network scanner — no app or this web UI needed:

- **macOS**: *System Settings → Printers & Scanners* (or the Image Capture app) should list it automatically once discovered via Bonjour; scan from there directly.
- **iOS**: the Files app's *Scan Documents*, and many other apps' "scan" buttons, look for AirScan-compatible scanners on the network automatically.
- **Android**: apps that support "network scan" / eSCL discovery (many scanning apps do) find it the same way.

Discovery needs `mdns = yes` (the default); without it, a client that lets you enter an address directly can use `http://<server>:8000/eSCL/` as the eSCL root. Only one scanner is exposed this way — set `escl_scanner` in `config.ini` if the backend reports more than one and the wrong one gets picked.

### Printer maintenance

Below the print button there's a **Maintenance** row for the selected printer:

| Action | Printers | What it does |
|---|---|---|
| Test page | any | the OS's standard test page (`Win32_Printer.PrintTestPage` / CUPS `testprint`). On Windows it only works when the server runs **as administrator**: Windows refuses it otherwise |
| Nozzle check | Canon inkjets | prints the nozzle check pattern — gaps mean the head needs cleaning |
| Head cleaning | Canon inkjets | cleans the print head (uses ink, ~1 minute) |

Every action asks for confirmation and is recorded in the job history.

### Language

The **RU / EN** switch in the top-right corner stores the choice in a cookie for a year. Until someone picks a language, the page is in English: `defaultlang` in [`config.ini`](#configuration). Set `defaultlang = auto` to follow each browser's language instead. Server messages (option names, errors) follow the page language.

To open the page in a language without changing the stored choice, add `?lang=en` or `?lang=ru` to the URL.

Paper names like "Plain Paper" / «Обычная бумага» come from the printer driver in the **Windows language** and can't be translated by the server.

## Configuration

Settings live in **`config.ini`** next to `run.py`. On first start it's created from [`config.example.ini`](config.example.ini), which documents every key. Edit `config.ini`, not the example. `config.ini` is excluded from git because it may contain passwords. **Restart the server after changes.**

```ini
defaultlang = en
port = 8000
ipp_port = 631
ipp_printer =
mdns = yes
escl = yes
escl_scanner =
ssl_certfile =
ssl_keyfile =
data_dir = data
scans_dir = scans
auth = none
ipp_auth = no
escl_auth = no
session_days = 30

[users]
# name = password
```

| Key | Default | Meaning | Env. variable |
|---|---|---|---|
| `defaultlang` | `en` | UI language until a user picks one with the RU / EN switch; also used for IPP jobs. `en`, `ru`, or `auto` = each browser's language, falling back to the OS language | `MFP_LANG` |
| `port` | `8000` | web UI / API port | `MFP_PORT` |
| `ipp_port` | `631` | IPP printer port (Windows only); `0` disables IPP | `MFP_IPP_PORT` |
| `ipp_printer` | *(empty)* | which printer the IPP endpoint prints to; empty = the OS default printer. By default virtual printers (PDF, XPS, Fax) and IPP printers pointing back at this server are skipped | `MFP_IPP_PRINTER` |
| `mdns` | `yes` | advertise the IPP printer and/or eSCL scanner via Bonjour/mDNS (IPP: Windows only; eSCL: both OSes); `no` disables it | `MFP_MDNS` |
| `escl` | `yes` | AirScan/eSCL scanning endpoint at `/eSCL/*` (Windows and Linux); `no` disables it | `MFP_ESCL` |
| `escl_scanner` | *(empty)* | which scanner the eSCL endpoint serves; empty = the scan backend's first one | `MFP_ESCL_SCANNER` |
| `ssl_certfile`, `ssl_keyfile` | *(empty)* | certificate/key (PEM) for HTTPS on the web port; both must be set to enable it, see [Security](#security) | `MFP_SSL_CERTFILE`, `MFP_SSL_KEYFILE` |
| `data_dir` | `data` | job history and the session signing key; relative paths are from the project folder | `MFP_DATA_DIR` |
| `scans_dir` | `scans` | where scans are stored | `MFP_SCANS_DIR` |
| `auth` | `none` | `none`: open to everyone on the network; `yes`: sign-in required, see [Users and sign-in](#users-and-sign-in) | `MFP_AUTH` |
| `ipp_auth` | `no` | `yes`: IPP printing also requires a user name and password (HTTP Basic) | `MFP_IPP_AUTH` |
| `escl_auth` | `no` | `yes`: eSCL scanning also requires a user name and password (HTTP Basic) | `MFP_ESCL_AUTH` |
| `session_days` | `30` | how long "Remember me" keeps you signed in | `MFP_SESSION_DAYS` |

Environment variables override the file. That's handy for one-off runs, e.g. `$env:MFP_PORT = "8080"; .\start.ps1`. Two more variables:
- `MFP_CONFIG`: use another config file;
- `MFP_RELOAD=1`: development auto-reload, see [Development](#development).

### Users and sign-in

With `auth = yes` every page, API call and scan download requires signing in. Only the login page and static files are open. Users go into the `[users]` section:

```ini
auth = yes

[users]
anna = pbkdf2_sha256$390000$...$...
john = plain-text-password
```

**Passwords** can be plain text, but a hash is better:

```bash
.venv\Scripts\python run.py --hash-password     # Linux: .venv/bin/python run.py --hash-password
```

It asks for the password twice and prints a line like `pbkdf2_sha256$390000$...`; paste it after `name = `. The server log warns about users with plain-text passwords.

> In `config.ini`, `;` or `#` preceded by a space starts a comment, so a plain-text password with " #" or " ;" in it gets cut. Hashes don't have this problem.

**How it behaves:**
- The browser shows a **sign-in page**. "Remember me" keeps you signed in for `session_days`; without it the session ends when the browser closes. **Sign out** is in the top-right corner.
- Sessions are signed cookies (HttpOnly, SameSite=Lax), signed with a random key in `data/secret.key`. They survive server restarts. Changing a user's password or removing the user signs that user out everywhere. Deleting `secret.key` signs everyone out.
- After **5 failed attempts in 5 minutes**, that IP address is blocked for the rest of those 5 minutes. Failed attempts are logged.
- **Scripts and `curl`** can send HTTP Basic credentials instead of a cookie:
  ```bash
  curl -u anna:password http://192.168.1.20:8000/api/jobs
  ```

**IPP printing** is not covered by `auth`: with `auth = yes` IPP stays open unless `ipp_auth = yes`. With `ipp_auth = yes` the printer requires HTTP Basic, and advertises it (`uri-authentication-supported = basic`):
- CUPS asks for the name and password, or they can be put into the URI: `ipp://anna:password@server:631/ipp/print`;
- Windows' built-in IPP driver and phones often can't send a password, so turn this on only if your clients support it.

**eSCL scanning** works the same way via `escl_auth`: `auth = yes` alone leaves `/eSCL/*` open, and macOS/iOS/Android's built-in scanning generally can't send a password either, so it's off by default.

Leave `auth = none` (the default) on a trusted home network where everyone may print and scan.

## How it works

```mermaid
flowchart LR
    Browser["Browser<br/>(PC / phone)"] -- "HTTP :8000<br/>upload, preview, scan" --> Web
    Client["IPP client<br/>(Windows / CUPS / macOS)"] -- "IPP :631<br/>PDF · PWG Raster · JPEG" --> IPP
    Scan["eSCL client<br/>(macOS / iOS / Android)"] -- "HTTP :8000/eSCL<br/>ScanJobs · NextDocument" --> ESCL

    subgraph Server["MFP Print & Scan Server (FastAPI)"]
        Web["Web UI + REST API"]
        IPP["IPP Everywhere printer"]
        ESCL["eSCL (AirScan) server"]
        Layout["Shared page layout<br/>(fit / sheet / actual size)"]
        PB["Print backend"]
        SB["Scan backend"]
        Web --> PB
        IPP --> PB
        Web --> SB
        ESCL --> SB
        PB --- Layout
    end

    PB -- "Windows: spooler, DEVMODE,<br/>PDFium → printer DC" --> Printer[("MFP<br/>USB")]
    PB -- "Linux: lp / CUPS" --> Printer
    SB -- "Windows: WIA (COM)<br/>Linux: SANE (scanimage)" --> Printer
```

**Printing on Windows.** For PDFs, images and IPP jobs the server opens a device context on the printer with a job-specific DEVMODE: paper size, paper type, quality, color, duplex. It then draws the pages itself (PDFium's `FPDF_RenderPage` into the DC, Pillow for bitmaps). Pages are placed in one of three ways, and the preview uses the same code:
- **fit**: into the printable area (uploads);
- **sheet**: page = physical sheet (IPP clients);
- **actual size**: scans/copies.

Other formats go through the `printto` shell verb. In that case the options are applied to the **per-user** default DEVMODE (`SetPrinter` level 9), which doesn't need admin rights.

**Why not just use the driver's settings?** Drivers expose options through `DeviceCapabilities`, but not the rules between them. The Canon driver happily accepts "envelope + A4" through every API (`DocumentProperties`, PrintTicket validation) and only its own dialog forbids it. Such rules live in `app/printing/media_constraints.py` as data per printer model.

**IPP.** `app/ipp/protocol.py` is a small RFC 8010 encoder/decoder. `app/ipp/printer.py` implements:
- the operations: Get-Printer-Attributes, Validate-Job, Print-Job, Create-Job/Send-Document, Get-Job-Attributes, Get-Jobs, Cancel-Job, Close-Job;
- capability mapping from the driver to IPP (PWG media names, `media-col-database` with real margins).

`app/printing/pwg.py` decodes PWG Raster.

**Scanning.**
- Windows: WIA through COM, on one dedicated thread, since COM objects are apartment-bound and a flatbed does one scan at a time.
- Linux: `scanimage`; options are parsed from `scanimage --all-options`.

**eSCL.** `app/escl/` implements the AirScan/eSCL HTTP+XML API on top of the same `ScanBackend` — the web UI's `/api/scan*` routes and the eSCL server share one backend instance, so its busy-lock actually serializes the physical scanner between a browser and a phone. `POST /eSCL/ScanJobs` returns immediately (a background thread does the scan); `GET .../NextDocument` blocks until it's done, then streams the image once. eSCL/Bonjour model one scanner per announced service, so `escl_scanner` in `config.ini` picks which one if the backend reports more than one (empty = the first).

**A Linux quirk this surfaced.** On a Linux machine with `sane-airscan`/`escl` installed (common on Ubuntu), turning on this server's own eSCL announcement means the machine's own SANE now discovers *itself* as a network scanner over Bonjour — so `scanimage -L` (used internally to list scanners) starts taking ~12s for its network-discovery timeout instead of ~1s, and can occasionally hit "device busy" if that self-discovery probes the real USB scanner at the same moment this server's own capabilities/scan calls do. `linux_sane.py` caches `list_scanners()` (never caching an empty result, so a real disconnect is still noticed promptly) and retries a couple of times on a transient open failure for the lightweight list/capabilities calls — not for an actual scan. Harmless duplicate entries for the same physical scanner (`pixma:...`, `escl:http://...`, `airscan:...`) can still show up in `/api/scanners`' list on such a machine; picking one of the network ones just adds a pointless network round trip back to this same server, not a bug, since a printer/scanner behind a genuine network hop wouldn't have this self-reference at all.

### Project layout

```
app/
  main.py              FastAPI app: pages, print/preview/jobs/maintenance API, IPP endpoint
  scan_routes.py       scanning API (web UI)
  escl_routes.py       eSCL (AirScan) HTTP endpoints
  mdns_util.py         shared zeroconf plumbing for ipp/mdns.py and escl/mdns.py
  i18n.py              all UI and server texts (ru/en)
  preview.py           print preview rendering
  storage.py           job history (JSON, atomic writes)
  models.py            API models
  printing/
    base.py            PrintBackend interface
    windows_print.py   Windows backend (spooler, DEVMODE, PDFium/GDI, WMI, RAW jobs)
    linux_cups.py      Linux backend (lp, lpoptions)
    convert.py         documents/text → PDF via LibreOffice (both OSes), with a cache
    layout.py          page placement shared by printing and preview
    media_constraints.py  paper type ↔ size rules per printer model
    maintenance.py     test page / vendor maintenance jobs (Vendor plugin table, currently Canon IVEC+BJL)
    pwg.py             PWG Raster decoder
  ipp/
    protocol.py        IPP binary encoding
    printer.py         IPP Everywhere printer
    mdns.py            Bonjour/mDNS advertisement (Windows)
  scanning/
    base.py            ScanBackend interface
    windows_wia.py     WIA backend
    linux_sane.py      SANE backend
    store.py           saved scans, thumbnails, PDF merge
  escl/
    protocol.py        eSCL XML: capabilities/status build, ScanSettings parse
    server.py          target scanner, job lifecycle, image encoding
    mdns.py            Bonjour/mDNS advertisement (Windows and Linux)
  templates/index.html
  static/              app.js (print tab), scan.js (scan tab), style.css
run.py                 starts the web (8000) and IPP (631) servers; --log-file for background use
start.bat / start.ps1  Windows launcher
start.sh               Linux launcher
register_service.bat   Windows: start with the system (Task Scheduler task + firewall rules)
unregister_service.bat Windows: remove it
scripts/               PowerShell behind the .bat files
deploy/                systemd unit for Linux
docs/                  screenshots, development notes
```

## HTTP API

The UI is a thin client over a JSON API; interactive docs are at `http://<server>:8000/docs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/printers` | printers known to the OS |
| GET | `/api/printers/{name}/options` | options from the driver (+ paper type ↔ size rules) |
| GET | `/api/printers/{name}/maintenance` | available maintenance actions |
| POST | `/api/printers/{name}/maintenance/{action}` | run `test_page`, `nozzle_check`, `head_cleaning` |
| POST | `/api/print` | multipart: `file`, `printer`, `copies`, `options` (JSON object of option keys → values) |
| POST | `/api/preview` | multipart: `file`, `printer`, `options` → PNG pages as data URLs |
| GET | `/api/jobs` | job history |
| DELETE | `/api/jobs/{id}` · `/api/jobs` | delete one entry · clear finished jobs |
| GET | `/api/scanners` · `/api/scanners/capabilities?scanner_id=…` | scanners and their capabilities |
| POST | `/api/scan/preview` | preview of the whole glass |
| POST | `/api/scan` | `scanner_id`, `resolution`, `mode`, `x`/`y`/`width`/`height` (mm), `brightness`/`contrast` (−100…100), `format` |
| GET | `/api/scans` · `/api/scans/{name}` · `/api/scans/{name}/thumb` | list · download · thumbnail |
| DELETE | `/api/scans/{name}` | delete a scan |
| POST | `/api/scans/merge` | `{"names": [...]}` → one PDF |
| POST | `/api/scans/{name}/print` | print a scan at actual size |
| POST | `/ipp/print` (port 631) | IPP endpoint |
| GET | `/eSCL/ScannerCapabilities` · `/eSCL/ScannerStatus` | eSCL (AirScan) capabilities/status, XML |
| POST | `/eSCL/ScanJobs` | eSCL: create a scan job from a `ScanSettings` XML body → `Location` header |
| GET | `/eSCL/ScanJobs/{id}/NextDocument` | eSCL: the scanned image (blocks until the scan is done) |
| DELETE | `/eSCL/ScanJobs/{id}` | eSCL: cancel/discard a job |

Example:

```bash
curl -F file=@report.pdf -F "printer=Canon MG2500 series Printer" \
     -F 'options={"paper_size":"A4","color":"mono","quality":"draft"}' \
     http://192.168.1.20:8000/api/print
```

Send `?lang=en|ru` or an `X-Lang` header to get labels and errors in that language. With `auth = yes`, add `-u name:password` (HTTP Basic). Without credentials the API answers `401`.

## Data on disk

| Path | Contents |
|---|---|
| `config.ini` | settings and users (created from `config.example.ini`) |
| `data/secret.key` | random key that signs sign-in sessions; delete it to sign everyone out |
| `data/jobs.json` | job history (last 500). Written atomically; if it's ever corrupt, it's kept as `jobs.json.broken` and history starts empty |
| `uploads/` | files uploaded for printing; removed when their history entry is deleted or cleared |
| `scans/` | scans; `scans/.meta/` holds thumbnails and metadata |
| `logs/server.log` | server log when running as a Windows service (on Linux the log goes to the journal) |

None of these are committed to git (see `.gitignore`).

## Security

**By default (`auth = none`) there is no sign-in.** Anyone who can reach the ports can print, scan, and see or delete history and scans. Turn on [`auth = yes`](#users-and-sign-in) if other people share your network.

Even with sign-in, the server is meant for a LAN:
- by default it speaks plain **HTTP**, so passwords and cookies aren't encrypted on the network unless you set `ssl_certfile`/`ssl_keyfile` (below);
- **don't** forward ports 8000/631 from your router, and don't expose the server to the internet.

**HTTPS for the web UI.** Set both in `config.ini`:
```ini
ssl_certfile = /path/to/fullchain.pem
ssl_keyfile = /path/to/privkey.pem
```
Restart the server — the web port then serves HTTPS only (plain HTTP requests to it fail; the IPP port stays HTTP, IPP-over-TLS isn't implemented). Paths are relative to the project folder if not absolute. A self-signed certificate works (browsers just warn once); for a certificate no browser warns about, use your router's if it issues one for your LAN domain, a local CA (e.g. [mkcert](https://github.com/FiloSottile/mkcert)), or one from a service that supports your setup.

For remote access (outside your LAN) use a VPN, or a reverse proxy in front of the server.

Other notes:
- uploaded files are limited to 100 MB;
- scan file names are validated, so paths can't escape the scans folder;
- the server needs no admin rights on Windows.

## Troubleshooting

<details>
<summary><b>Canon shows "Support code 4102: media type and paper size are not set correctly"</b></summary>

The printer rejected the combination of paper type and size, e.g. Glossy Photo Paper in 13×18. Press **Stop** on the printer or **Cancel printing** in the dialog. The server now fixes known-bad combinations automatically, see `app/printing/media_constraints.py`. If you hit a combination that isn't covered, please open an issue with the printer model, paper type and size.
</details>

<details>
<summary><b>CUPS: "Unable to create PPD: No IPP attributes"</b></summary>

CUPS got no answer to its capability query. Check, in order:
1. The server is running and shows `Uvicorn running on http://0.0.0.0:631`.
2. Port 631 is open in the Windows firewall (see [Quick start](#windows)).
3. The URI is `ipp://<server>:631/ipp/print`.
</details>

<details>
<summary><b>The server window closes / "port is already in use"</b></summary>

Another instance is already running (maybe from autostart), or something else uses port 8000/631. Close the other window, or pick other ports with `port` / `ipp_port` in `config.ini`.
</details>

<details>
<summary><b>"Python 3 not found" from start.bat</b></summary>

Install Python from python.org with "Add python.exe to PATH". The `python` command that opens Microsoft Store is only a stub; the launcher prefers `py.exe` anyway.
</details>

<details>
<summary><b>Moved the folder from Linux and it doesn't start</b></summary>

A `.venv` created on Linux doesn't work on Windows. Delete `.venv` and run `start.bat` again.
</details>

<details>
<summary><b>A job is stuck in "Queued"</b></summary>

Jobs only stay queued while they're printing. If the server was stopped mid-print, the job is marked "The server was restarted while printing" on the next start. Jobs sent to an unavailable printer fail immediately with an explanation.
</details>

<details>
<summary><b>Scanner not found / busy</b></summary>

Check that the scanner shows up in Windows *Scan* / *Devices* (WIA), or in `scanimage -L` on Linux. Only one scan runs at a time; a second one gets "The scanner is busy".
</details>

<details>
<summary><b>.docx prints with broken formatting</b></summary>

Install [LibreOffice](https://www.libreoffice.org/) on the server and restart it: documents are then converted to PDF by LibreOffice and keep their formatting, with a preview. Without it, Windows prints `.docx` with WordPad (or Word, if installed), and WordPad loses formatting.
</details>

## Extending

**Another printer.** Install it in the OS: it appears in the list with the options its driver reports, and IPP advertises its real paper sizes and margins. No code changes are needed.

**Another scanner.** Same story on the eSCL side: any scanner the backend (WIA/SANE) already lists works over AirScan too. If more than one is connected, set `escl_scanner` in `config.ini` to pick which one is served — eSCL/Bonjour only model one scanner per announced service.

**Paper type ↔ size rules for a new model.** Add an entry to `RULES` in `app/printing/media_constraints.py`, keyed by driver name. Use the DEVMODE media/paper ids; you can list them with `DeviceCapabilities` (`DC_MEDIATYPES`, `DC_PAPERS`).

**Maintenance commands for other brands.** `app/printing/maintenance.py` has a small `Vendor` plugin table (`VENDORS`) instead of hardcoding Canon: each entry is a match function (driver name → bool), which of the maintenance actions the brand supports, and a function building the raw job bytes for each. Add one entry for your brand and nothing else needs touching — the API and UI pick it up automatically. The hard part is getting the bytes right: printers ignore anything that doesn't match their own driver's exact framing (a "should be correct" plain-BJL command for Canon was silently ignored — see the module's docstring for why). The reliable way to get them is to **capture a real job from the vendor's own driver**: pause the printer's queue, trigger the action from the vendor's own utility, then read the held job's raw bytes (`win32print.ReadPrinter` on Windows; the spool file under `/var/spool/cups/` on Linux) before releasing it — that's exactly how the Canon bytes here were captured. Full instructions are in the `Vendor` class docstring in `app/printing/maintenance.py`.

**Translations.** All texts are in `app/i18n.py`:
- keys `web.*` are used by the page and its JavaScript;
- the other keys are server messages.

To add a language, add it to `LANGS` and add a value for it to every entry.

## Development

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux: source .venv/bin/activate
pip install -r requirements.txt
set MFP_RELOAD=1                # PowerShell: $env:MFP_RELOAD="1"
python run.py
```

`MFP_RELOAD=1` serves only the web port and reloads on changes in `app/`. On Windows, uvicorn's reloader occasionally keeps the old worker running; restart manually if changes don't show up.

Stack:
- **backend**: FastAPI, uvicorn, pywin32, pypdfium2, Pillow, zeroconf;
- **frontend**: plain HTML/CSS/JS, no build step.

## Known limitations

- **Plain HTTP by default.** Set `ssl_certfile`/`ssl_keyfile` in `config.ini` for HTTPS (see [Security](#security)), or use a reverse proxy — either way it's still meant for a LAN, not the open internet.
- **Bonjour/mDNS** advertises the IPP printer on Windows only, matching where the IPP server itself runs; on Linux, share it through CUPS instead (which does its own mDNS via Avahi).
- **No AirPrint (URF raster)**, so iPhones can't print to it directly yet.
- **No OCR** for scans.
- **Documents without LibreOffice** are printed by whatever program is registered for them, with no preview; install LibreOffice for exact results.
- **Duplex**: the MG2500 driver reports duplex, but the printer has no automatic duplexer, so the driver does manual duplex.

## License

[MIT](LICENSE).

## Credits

- [phpSane](https://github.com/gawindx/phpSane): inspiration for the scanning UI.
- [gutenprint](https://gimp-print.sourceforge.io/) `commandtocanon` and Canon's cnijfilter sources: the BJL command vocabulary. The exact job layout comes from the Canon Windows driver's own maintenance jobs.
- [PDFium](https://pdfium.googlesource.com/pdfium/) via [pypdfium2](https://github.com/pypdfium2-team/pypdfium2), [Pillow](https://python-pillow.org/), [FastAPI](https://fastapi.tiangolo.com/), [pywin32](https://github.com/mhammond/pywin32).
- PWG specifications: IPP Everywhere (PWG 5100.14), PWG Raster (PWG 5102.4), media names (PWG 5101.1).
