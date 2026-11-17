# HP Smart Tank 520/540 (USB) on macOS — printing and scanning

*[Русская версия](README.ru.md)*

HP ships no macOS driver for the USB-only **HP Smart Tank 520 / 540** all-in-one
(product 1F3W2A and regional variants). This project makes both functions work
on current macOS, using only open-source code:

* **Printing in colour and greyscale** through the standard macOS print dialog,
  with quality modes (Normal, Best, High-Resolution Photo 1200 dpi) and paper types.
* **Scanning** in Image Capture, Preview and *System Settings → Printers & Scanners*,
  in colour, greyscale and black-and-white, 75–1200 dpi, JPEG or PDF.

No HP software, no paid apps, no binary blobs.

> Not affiliated with or endorsed by HP Inc. Use at your own risk.

## How it works

| Function | What the printer offers over USB | What this project adds |
|---|---|---|
| Scanning | A full **eSCL** (AirScan) server, reachable only through its IPP-over-USB interface (class 7/1/4). It does **not** offer IPP printing there. | `scanbridge.py` forwards HTTP between a local port and that USB interface and announces the scanner via Bonjour as *Smart Tank 520_540 series (USB)*. It runs as a system LaunchDaemon. See [Why not macOS' own USB scanner](#why-not-macos-own-usb-scanner). |
| Printing | The PCL3GUI2 printer language on the classic printer interface (7/1/2). | HP's open-source **hpcups** filter from HPLIP 3.25.8, built natively for macOS, with an HPLIP PPD and a fix for missing black (see [docs/hplip-bug-report.md](docs/hplip-bug-report.md)). macOS' own CUPS sends the job over USB. |

## Requirements

* macOS on Apple silicon or Intel. Tested on macOS 27.0.1 (Apple silicon) with
  printer firmware `LAP1CN2446AR`. Intel Macs and older macOS versions are
  expected to work but are untested.
* **Command Line Tools** (the installer offers to install them; about 1 GB).
* An administrator password.
* Internet access during installation (libusb and libjpeg are downloaded and
  checked against pinned SHA-256 sums).

## Installation

1. Connect the printer by USB and switch it on.
2. Download this repository (*Code → Download ZIP*, then unzip) or clone it:
   ```bash
   git clone https://github.com/maximiliangrishin-collab/HP_Smart_Tank_500_MacOS.git
   ```
3. In Terminal:
   ```bash
   cd HP_Smart_Tank_500_MacOS
   ./install.sh
   ```
   If macOS says the Command Line Tools are missing, finish their installation
   and run `./install.sh` again.
4. Print the test page:
   ```bash
   lp -d HP_Smart_Tank_520 tools/testpage.pdf
   ```
5. Open **Image Capture** and choose **Smart Tank 520_540 series (USB)**. Image Capture may
   also list **HP Smart Tank 520_540 series**: that entry is macOS' own and does not work with
   this printer, so ignore it.

Options: `--no-scan`, `--no-print`, `--queue-only` (re-create the print queue,
for example if the printer was not connected during installation),
`--paper A4|Letter`.

## Uninstallation

```bash
./uninstall.sh
```

## Troubleshooting

* **The scanner does not appear or fails.** Check the log:
  `tail /Library/Logs/hp520-scanbridge.log`. Test without Image Capture:
  `sudo launchctl bootout system/org.hp520-macos.scanbridge`, then
  `sudo python3 -I /Library/Printers/HPSmartTank520/scan/scanbridge.py --test ~/Desktop/scan.jpg`,
  then start the bridge again:
  `sudo launchctl bootstrap system /Library/LaunchDaemons/org.hp520-macos.scanbridge.plist`.
  Add `--verbose` instead of `--test …` to see every request Image Capture sends.
* **"Failed to open a connection to the device (-21345)" or "failed to complete the overview
  scan".** You picked *HP Smart Tank 520_540 series*; pick the entry ending in *(USB)*.
* **An old queue created in *Printers & Scanners* (AirPrint/IPP) with stuck jobs.** It can never
  print on this printer. Remove it: `lpstat -p` to find its name, then `cancel -a <name>` and
  `sudo lpadmin -x <name>`.
* **The print queue was not created.** The printer was not visible on USB:
  connect it and run `./install.sh --queue-only`.
* **Black or a colour is missing even in a copy made on the printer itself.**
  That is a print-head or ink issue, not the driver: run head cleaning from the
  printer's panel.
* **Share the scanner with other devices on the network:** edit
  `/Library/LaunchDaemons/org.hp520-macos.scanbridge.plist` to add `--lan` to the arguments.
  Anyone on the network can then scan.

## Why not macOS' own USB scanner

macOS (seen on 15.7 and 27.0) has its own IPP-over-USB service: for a printer with a CUPS
queue it submits a launchd job `com.apple.print.ippusb.<make>.<model>.<serial>` that runs
`/usr/libexec/ippusbd`, takes the IPP-USB interfaces and announces the scanner as
*HP Smart Tank 520_540 series*. With this printer that path breaks: the firmware sometimes
sends a surplus HTTP response, `ippusbd` evidently does not resynchronise, and from then on every
request gets the answer to the previous one (for example `ScannerStatus` returns the
5 384-byte `ScannerCapabilities` document). Image Capture then fails with -9921, -21345 or
"failed to complete the overview scan".

The bridge discards such stale bytes before each request. To keep the printer, it holds all
three IPP-USB interfaces and, running as root, removes that launchd job for HP printers
whenever it finds the interfaces taken. It announces its own UUID, because Image Capture
merges scanners with the same UUID and would otherwise show only macOS' entry.
`tools/test-native-escl.sh` runs one scan through macOS' entry for comparison.

## Files installed

* `/Library/Printers/HPSmartTank520/` — filter `hpcups`, the PPD, and the scan bridge
* `/Library/LaunchDaemons/org.hp520-macos.scanbridge.plist` — runs the bridge (as root)
* `/Library/Logs/hp520-scanbridge.log` — bridge log
* CUPS queue `HP_Smart_Tank_520`

## Licence

GPL-2.0-or-later (see `LICENSE`). `driver/hpcups/` contains HP's HPLIP code
under its original BSD and GPL-2.0-or-later headers (`driver/COPYING.hplip`,
changes listed in `driver/PATCHES.md`). `scan/vendor/usb/` is PyUSB 1.3.1
(BSD-3-Clause, `scan/vendor/LICENSE.pyusb`).
