#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Installs printing (hpcups filter + PPD + CUPS queue) and scanning (eSCL bridge)
# for the USB-only HP Smart Tank 520/540 on macOS (Apple silicon and Intel).
#
#   ./install.sh                 everything
#   ./install.sh --no-scan       printing only
#   ./install.sh --no-print      scanning only
#   ./install.sh --queue-only    (re)create the print queue, e.g. after plugging the printer in
#   ./install.sh --paper A4      default paper size (A4 or Letter; default: from macOS region)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD="$ROOT/build"
DEST=/Library/Printers/HPSmartTank520
QUEUE=HP_Smart_Tank_520
LABEL=org.hp520-macos.scanbridge
DAEMON=/Library/LaunchDaemons/$LABEL.plist
OLD_AGENT=/Library/LaunchAgents/$LABEL.plist   # versions before the system daemon
PORT=8095

LIBUSB_V=1.0.30
LIBUSB_URL="https://github.com/libusb/libusb/releases/download/v$LIBUSB_V/libusb-$LIBUSB_V.tar.bz2"
LIBUSB_SHA=fea36f34f9156400209595e300840767ab1a385ede1dc7ee893015aea9c6dbaf
JPEG_V=9f
JPEG_URL="https://www.ijg.org/files/jpegsrc.v$JPEG_V.tar.gz"
JPEG_SHA=04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b

DO_PRINT=1; DO_SCAN=1; QUEUE_ONLY=0; PAPER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-scan) DO_SCAN=0 ;;
    --no-print) DO_PRINT=0 ;;
    --queue-only) QUEUE_ONLY=1 ;;
    --paper) PAPER="${2:-}"; shift ;;
    -h|--help) sed -n '3,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n==> %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)

fetch() {  # url sha256 -> prints local path
  local url=$1 sha=$2 out
  out="$BUILD/dl/$(basename "$1")"
  mkdir -p "$BUILD/dl"
  if [ ! -f "$out" ] || [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" != "$sha" ]; then
    curl -fsSL --retry 3 -o "$out.part" "$url" || die "download failed: $url"
    mv "$out.part" "$out"
  fi
  [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" = "$sha" ] || die "checksum mismatch: $out"
  echo "$out"
}

paper_default() {
  [ -n "$PAPER" ] && { echo "$PAPER"; return; }
  local loc country
  loc=$(defaults read -g AppleLocale 2>/dev/null || echo en_US)
  country=${loc##*_}; country=${country%%@*}
  case "$country" in US|CA|MX|PH|CL|CO|VE|GT|PR|CR|SV|NI|PA|DO) echo Letter ;; *) echo A4 ;; esac
}

create_queue() {
  step "Print queue $QUEUE"
  local uri
  uri=$(lpinfo --include-schemes usb -v 2>/dev/null | awk '/usb:\/\/HP\/Smart%20Tank%205/ {print $2; exit}')
  if [ -z "$uri" ]; then
    echo "The printer is not visible on USB. Connect it, switch it on and run:  ./install.sh --queue-only"
    return 0
  fi
  echo "device: $uri"
  sudo lpadmin -p "$QUEUE" -D "HP Smart Tank 520" -L "USB" -v "$uri" \
       -P "$DEST/HP-Smart-Tank-520.ppd" -o printer-is-shared=false -o "PageSize=$(paper_default)" -E
  sudo cupsenable "$QUEUE"; sudo cupsaccept "$QUEUE"
  echo "queue ready (paper: $(paper_default)). Test page:  lp -d $QUEUE \"$ROOT/tools/testpage.pdf\""
}

# ---------------------------------------------------------------- checks
[ "$(uname -s)" = Darwin ] || die "this installer is for macOS"
[ "$(id -u)" -ne 0 ] || die "run as your normal user, not with sudo (it asks for the password itself)"
if ! xcode-select -p >/dev/null 2>&1 || ! command -v clang >/dev/null 2>&1; then
  xcode-select --install 2>/dev/null || true
  die "Command Line Tools are required. Finish the installation in the window that opened, then run ./install.sh again."
fi
echo "macOS $(sw_vers -productVersion), $(uname -m)"

if [ "$QUEUE_ONLY" = 1 ]; then
  [ -x "$DEST/hpcups" ] || die "the driver is not installed yet; run ./install.sh first"
  create_queue; exit 0
fi

echo "The password is needed to install files into $DEST and /Library/LaunchDaemons."
sudo -v

# ---------------------------------------------------------------- printing
if [ "$DO_PRINT" = 1 ]; then
  step "Building libjpeg $JPEG_V (static)"
  J="$BUILD/jpeg"
  if [ ! -f "$J/lib/libjpeg.a" ] || [ ! -f "$J/include/jpegint.h" ]; then
    tgz=$(fetch "$JPEG_URL" "$JPEG_SHA")
    rm -rf "$BUILD/jpeg-$JPEG_V" && tar xzf "$tgz" -C "$BUILD"
    ( cd "$BUILD/jpeg-$JPEG_V" && ./configure --prefix="$J" --disable-shared -q CFLAGS="-O2 -w" >/dev/null \
      && make -s -j"$JOBS" >/dev/null && make -s install >/dev/null )
    cp "$BUILD/jpeg-$JPEG_V/jpegint.h" "$J/include/"   # needed by hpcups, not installed by libjpeg
  fi

  step "Building the hpcups filter"
  rm -rf "$BUILD/hpcups" && cp -R "$ROOT/driver/hpcups" "$BUILD/hpcups"
  make -s -C "$BUILD/hpcups" -j"$JOBS" CC=clang CXX=clang++ JPEG="$J" JPEG_LIB="$J/lib/libjpeg.a" >/dev/null
  file "$BUILD/hpcups/hpcups"

  step "Installing the driver into $DEST"
  sudo install -d -o root -g wheel -m 755 "$DEST"
  sudo install -o root -g wheel -m 755 "$BUILD/hpcups/hpcups" "$DEST/hpcups"
  sudo install -o root -g wheel -m 644 "$ROOT/driver/HP-Smart-Tank-520.ppd" "$DEST/HP-Smart-Tank-520.ppd"
  create_queue
fi

# ---------------------------------------------------------------- scanning
if [ "$DO_SCAN" = 1 ]; then
  step "Building libusb $LIBUSB_V"
  U="$BUILD/libusb"
  if [ ! -f "$U/lib/libusb-1.0.0.dylib" ]; then
    tbz=$(fetch "$LIBUSB_URL" "$LIBUSB_SHA")
    rm -rf "$BUILD/libusb-$LIBUSB_V" && tar xjf "$tbz" -C "$BUILD"
    ( cd "$BUILD/libusb-$LIBUSB_V" && ./configure --prefix="$U" --disable-static -q >/dev/null \
      && make -s -j"$JOBS" >/dev/null && make -s install >/dev/null )
  fi
  file "$U/lib/libusb-1.0.0.dylib"

  step "Installing the scan bridge"
  sudo install -d -o root -g wheel -m 755 "$DEST/scan"
  sudo rm -rf "$DEST/scan/vendor"
  sudo cp -R "$ROOT/scan/vendor" "$DEST/scan/vendor"
  sudo install -o root -g wheel -m 644 "$ROOT/scan/scanbridge.py" "$DEST/scan/scanbridge.py"
  sudo install -o root -g wheel -m 755 "$U/lib/libusb-1.0.0.dylib" "$DEST/scan/libusb-1.0.dylib"
  sudo chown -R root:wheel "$DEST/scan"
  sed -e "s#@LABEL@#$LABEL#" -e "s#@DEST@#$DEST#" -e "s#@PORT@#$PORT#" "$ROOT/scan/launchdaemon.plist.in" > "$BUILD/daemon.plist"
  plutil -lint "$BUILD/daemon.plist" >/dev/null
  # older versions ran the bridge as a per-user LaunchAgent
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sudo rm -f "$OLD_AGENT"
  sudo install -o root -g wheel -m 644 "$BUILD/daemon.plist" "$DAEMON"
  sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
  sleep 1
  sudo launchctl bootstrap system "$DAEMON"

  printf 'waiting for the scanner'
  for _ in $(seq 1 20); do
    if curl -fs "http://127.0.0.1:$PORT/eSCL/ScannerStatus" >/dev/null 2>&1; then
      printf '\nscanner is up. In Image Capture pick "Smart Tank 520_540 series (USB)",\n'
      printf 'not "HP Smart Tank 520_540 series" (that entry is macOS'"'"' own and does not work with this printer).\n'
      break
    fi
    printf '.'; sleep 2
  done
  curl -fs "http://127.0.0.1:$PORT/eSCL/ScannerStatus" >/dev/null 2>&1 || \
    printf '\nthe scanner is not answering yet (printer off or unplugged?). It starts by itself once the printer is connected.\nLog: /Library/Logs/hp520-scanbridge.log\n'
fi

step "Done"
