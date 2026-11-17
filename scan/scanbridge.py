#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""eSCL (AirScan) bridge for USB-only HP Smart Tank 520/540 printers on macOS.

The printer contains an eSCL scan server, but exposes it only over USB through
an IPP-over-USB interface (class 7/1/4). This bridge opens that interface with
libusb, serves the same HTTP API on a local TCP port and announces it via
Bonjour as an _uscan._tcp scanner, so Image Capture, Preview and
System Settings > Printers & Scanners can use it without any driver.

    scanbridge.py              run the bridge (this Mac only, 127.0.0.1)
    scanbridge.py --lan        also share the scanner with the local network
    scanbridge.py --test FILE  scan one A4 page (300 dpi, colour) to FILE.jpg
"""
import argparse
import http.client
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))
import usb.core  # noqa: E402
import usb.util  # noqa: E402
import usb.backend.libusb1 as libusb1  # noqa: E402

HP_VID = 0x03F0
IPP_USB = (7, 1, 4)
LIBUSB = os.environ.get("HP520_LIBUSB", os.path.join(HERE, "libusb-1.0.dylib"))
VERBOSE = False
HOP = {"host", "connection", "content-length", "transfer-encoding", "keep-alive",
       "accept-encoding", "te", "upgrade", "proxy-connection", "expect"}
NS = "http://schemas.hp.com/imaging/escl/2011/05/03"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Busy(LookupError):
    """The IPP-over-USB interfaces are held by another process, normally macOS'
    own /usr/libexec/ippusbd, which then provides the scanner itself."""


class UsbGone(Exception):
    """The printer disappeared from USB; the bridge exits and launchd restarts it."""


def dechunk(data):
    """Return the decoded body if a chunked stream is complete, otherwise None."""
    out, i = bytearray(), 0
    while True:
        j = data.find(b"\r\n", i)
        if j < 0:
            return None
        n = int(data[i:j].split(b";")[0] or b"0", 16)
        i = j + 2
        if n == 0:
            return bytes(out) if data.find(b"\r\n", i) >= 0 else None
        if len(data) < i + n + 2:
            return None
        out += data[i:i + n]
        i += n + 2


class UsbHttp:
    """One HTTP connection tunnelled through an IPP-over-USB interface."""

    def __init__(self):
        backend = libusb1.get_backend(find_library=lambda _: LIBUSB)
        if backend is None:
            sys.exit(f"cannot load libusb from {LIBUSB}")
        def cls(i):
            return (i.bInterfaceClass, i.bInterfaceSubClass, i.bInterfaceProtocol)

        errors, found = [], None
        for dev in usb.core.find(find_all=True, idVendor=HP_VID, backend=backend):
            try:
                alts = list(dev.get_active_configuration())
            except usb.core.USBError:
                continue
            # Only IPP-over-USB interfaces: HP's vendor interface ff/04/01 answers
            # eSCL GET requests but never accepts scan jobs.
            for intf in [i for i in alts if cls(i) == IPP_USB]:
                try:
                    usb.util.claim_interface(dev, intf.bInterfaceNumber)
                except usb.core.USBError as e:
                    errors.append(f"interface {intf.bInterfaceNumber}: {e.strerror or e}")
                    continue
                try:
                    intf.set_altsetting()
                except usb.core.USBError as e:
                    usb.util.release_interface(dev, intf.bInterfaceNumber)
                    errors.append(f"interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}: {e.strerror or e}")
                    continue
                found = (dev, intf)
                break
            if found:
                break
        if not found:
            if errors:
                raise Busy("IPP-over-USB interfaces are in use by another process: " + "; ".join(errors))
            raise LookupError("no HP printer with an IPP-over-USB interface found")
        self.dev, intf = found
        self.iface = intf.bInterfaceNumber
        # Hold the other IPP-over-USB interfaces too, so macOS' ippusbd cannot take
        # them: its HTTP stream gets out of step with this printer's firmware.
        self.extra = []
        for i in alts:
            n = i.bInterfaceNumber
            if cls(i) == IPP_USB and n != self.iface and n not in self.extra:
                try:
                    usb.util.claim_interface(self.dev, n)
                    self.extra.append(n)
                except usb.core.USBError:
                    pass
        self.desc = "USB interface %d alt %d (%02x/%02x/%02x)" % ((intf.bInterfaceNumber, intf.bAlternateSetting) + cls(intf))
        if self.extra:
            self.desc += ", also holding interfaces " + ", ".join(map(str, self.extra))

        def bulk(direction):
            return usb.util.find_descriptor(intf, custom_match=lambda e:
                usb.util.endpoint_direction(e.bEndpointAddress) == direction and
                usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
        self.OUT, self.IN = bulk(usb.util.ENDPOINT_OUT), bulk(usb.util.ENDPOINT_IN)
        if self.OUT is None or self.IN is None:
            self.close()
            raise LookupError(f"{self.desc} has no bulk endpoints")
        self.lock = threading.Lock()
        self.drain(1.0)

    def close(self):
        try:
            for n in self.extra:
                usb.util.release_interface(self.dev, n)
            usb.util.release_interface(self.dev, self.iface)
            usb.util.dispose_resources(self.dev)
        except usb.core.USBError:
            pass

    def drain(self, seconds=0.5):
        """Discard any stale bytes left in the device's output queue."""
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                self.IN.read(65536, timeout=200)
            except usb.core.USBTimeoutError:
                pass

    def request(self, method, path, headers=None, body=b"", limit=240):
        lines = [f"{method} {path} HTTP/1.1", "Host: localhost"]
        for k, v in (headers or {}).items():
            if k.lower() not in HOP:
                lines.append(f"{k}: {v}")
        if body or method in ("POST", "PUT"):
            lines.append(f"Content-Length: {len(body)}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
        with self.lock:
            try:
                return self._exchange(method, path, raw, limit)
            except usb.core.USBTimeoutError:
                raise
            except usb.core.USBError as e:
                raise UsbGone(str(e))

    def _exchange(self, method, path, raw, limit):
        self.OUT.write(raw, timeout=15000)
        buf, t0, last = bytearray(), time.time(), time.time()
        head = None
        while time.time() - t0 < limit:
            try:
                chunk = self.IN.read(262144, timeout=1000)
                if chunk:
                    buf += bytes(chunk)
                    last = time.time()
            except usb.core.USBTimeoutError:
                chunk = None
            if head is None and b"\r\n\r\n" in buf:
                k = buf.index(b"\r\n\r\n")
                head, rest = bytes(buf[:k]).decode("latin-1"), k + 4
                hl = head.lower()
                status = int(head.split()[1])
                chunked = "transfer-encoding: chunked" in hl
                m = re.search(r"content-length:\s*(\d+)", hl)
                clen = int(m.group(1)) if m else None
            if head is not None:
                data = bytes(buf[rest:])
                if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
                    body = b""
                    break
                if chunked:
                    body = dechunk(data)
                    if body is not None:
                        break
                elif clen is not None:
                    if len(data) >= clen:
                        body = data[:clen]
                        break
                elif chunk is None and time.time() - last > 2:
                    body = data
                    break
        else:
            self.drain(1.0)
            raise TimeoutError(f"no complete answer from the printer to {method} {path}")
        first, *rest_lines = head.split("\r\n")
        reason = first.split(" ", 2)[2] if first.count(" ") >= 2 else ""
        hdrs = []
        for line in rest_lines:
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs.append((k.strip(), v.strip()))
        return status, reason, hdrs, body


class HttpUpstream:
    """Forward to an existing eSCL HTTP server, e.g. macOS' own ippusbd proxy."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.desc = f"upstream http://{host}:{port}"

    def close(self):
        pass

    def request(self, method, path, headers=None, body=b"", limit=240):
        hdrs = {k: v for k, v in (headers or {}).items() if k.lower() not in HOP}
        hdrs["Host"] = f"{self.host}:{self.port}"
        c = http.client.HTTPConnection(self.host, self.port, timeout=limit)
        try:
            c.request(method, path, body=body or None, headers=hdrs)
            r = c.getresponse()
            data = r.read()
            return r.status, r.reason, list(r.getheaders()), data
        finally:
            c.close()


def find_native_scanner(wait=4):
    """Find the eSCL scanner that macOS (ippusbd) announces for a USB HP printer."""
    def run(args):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=wait)
            return p.stdout
        except subprocess.TimeoutExpired as e:
            return (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    names = []
    for line in run(["dns-sd", "-B", "_uscan._tcp", "local."]).splitlines():
        m = re.search(r"\sAdd\s+\d+\s+-?\d+\s+\S+\s+_uscan\._tcp\.\s+(.+)$", line)
        if m and not m.group(1).endswith("(USB)") and m.group(1) not in names:
            names.append(m.group(1))
    for name in names:
        out = run(["dns-sd", "-L", name, "_uscan._tcp", "local."])
        m = re.search(r"can be reached at (\S+?)\.?:(\d+)", out)
        if m and "usb_VID=1008" in out:
            host = "127.0.0.1" if m.group(1).rstrip(".") in ("localhost",) else m.group(1).rstrip(".")
            return host, int(m.group(2)), name
    return None


def scanner_info(u):
    """Read ScannerCapabilities and derive the Bonjour TXT record from it."""
    st, _, _, xml = u.request("GET", "/eSCL/ScannerCapabilities")
    if st != 200:
        raise LookupError(f"printer has no eSCL scanner (HTTP {st})")
    x = xml.decode("utf-8", "replace")

    def tag(name):
        m = re.search(rf"<(?:pwg|scan):{name}>([^<]*)<", x)
        return m.group(1).strip() if m else ""
    model = tag("MakeAndModel") or "HP Smart Tank"
    modes = set(re.findall(r"<scan:ColorMode>([^<]+)<", x))
    cs = [n for m, n in (("RGB24", "color"), ("Grayscale8", "grayscale"), ("BlackAndWhite1", "binary")) if m in modes]
    fmts = sorted(set(re.findall(r"<(?:pwg:DocumentFormat|scan:DocumentFormatExt)>([^<]+)<", x))
                  - {"application/octet-stream"})
    sources = ["platen"] if "<scan:Platen>" in x else []
    if "<scan:Adf>" in x:
        sources.append("adf")
    txt = {"txtvers": "1", "vers": tag("Version") or "2.0", "rs": "eSCL", "ty": model,
           "pdl": ",".join(fmts) or "image/jpeg", "cs": ",".join(cs) or "color",
           "is": ",".join(sources) or "platen", "duplex": "F", "note": "USB"}
    dev_uuid = tag("UUID")
    # Announce our own UUID: Image Capture merges scanners with the same UUID and
    # then keeps macOS' own USB entry (ippusbd), hiding the bridge.
    own = str(uuid.uuid5(uuid.NAMESPACE_URL, "hp520-scanbridge:" + (dev_uuid or model)))
    txt["UUID"] = own
    return model, txt, dev_uuid, own


SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ns}" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.63</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:ScanRegions><pwg:ScanRegion>
    <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    <pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>
    <pwg:Width>2480</pwg:Width><pwg:Height>3508</pwg:Height>
  </pwg:ScanRegion></pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
</scan:ScanSettings>""".format(ns=NS)


def test_scan(u, out):
    st, _, h, _ = u.request("POST", "/eSCL/ScanJobs", {"Content-Type": "text/xml"}, SETTINGS.encode())
    loc = dict((k.lower(), v) for k, v in h).get("location")
    log(f"scan job: HTTP {st} {loc}")
    if st != 201 or not loc:
        sys.exit("the printer refused the scan job")
    job = re.sub(r"^https?://[^/]+", "", loc).rstrip("/")
    t0 = time.time()
    for _ in range(180):
        st, _, _, body = u.request("GET", job + "/NextDocument")
        if st == 200:
            with open(out, "wb") as f:
                f.write(body)
            log(f"done in {time.time() - t0:.0f} s: {out} ({len(body) // 1024} KiB)")
            u.request("GET", job + "/NextDocument")  # lets the printer close the job (expect 404)
            return
        if st not in (503, 409):
            sys.exit(f"unexpected HTTP {st} for NextDocument")
        time.sleep(1)
    sys.exit("no image received")


def serve(u, port, lan):
    model, txt, dev_uuid, own_uuid = scanner_info(u)
    addr = "0.0.0.0" if lan else "127.0.0.1"
    state = {"gone": False}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log("http " + (fmt % args))

        def proxy(self):
            body = b""
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                while True:
                    n = int(self.rfile.readline().split(b";")[0], 16)
                    if n == 0:
                        self.rfile.readline()
                        break
                    body += self.rfile.read(n)
                    self.rfile.readline()
            elif self.headers.get("Content-Length"):
                body = self.rfile.read(int(self.headers["Content-Length"]))
            if VERBOSE and body:
                log(f"  > {self.command} {self.path} body: " + re.sub(r"\s+", " ", body.decode("utf-8", "replace"))[:1500])
            try:
                st, reason, hdrs, rb = u.request(self.command, self.path, dict(self.headers), body)
            except UsbGone as e:
                self.send_error(503, "printer disconnected")
                log(f"printer disconnected: {e}")
                state["gone"] = True
                threading.Thread(target=srv.shutdown, daemon=True).start()
                return
            except Exception as e:  # timeout or protocol error: keep serving
                log(f"error on {self.command} {self.path}: {e}")
                self.send_error(504, "printer did not answer")
                return
            if VERBOSE:
                ctype = dict((k.lower(), v) for k, v in hdrs).get("content-type", "")
                log(f"  < {st} {reason} {ctype} {len(rb)} bytes"
                    + (": " + re.sub(r"\s+", " ", rb.decode("utf-8", "replace"))[:800] if st >= 400 or "xml" in ctype and self.command == "POST" else ""))
            if dev_uuid and "xml" in dict((k.lower(), v) for k, v in hdrs).get("content-type", ""):
                rb = rb.replace(dev_uuid.encode(), own_uuid.encode())
            self.send_response(st, reason)
            host = self.headers.get("Host") or f"127.0.0.1:{port}"
            for k, v in hdrs:
                if k.lower() in HOP or k.lower() in ("server", "date"):
                    continue
                if k.lower() == "location":
                    v = re.sub(r"^https?://[^/]+", f"http://{host}", v)
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(rb)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(rb)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = proxy

    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer((addr, port), Handler)
    name = f"{model} (USB)"
    record = [f"{k}={v}" for k, v in txt.items()]
    if lan:
        cmd = ["dns-sd", "-R", name, "_uscan._tcp", "local", str(port)] + record
    else:
        cmd = ["dns-sd", "-P", name, "_uscan._tcp", "local", str(port),
               "hp520-scanbridge.local", "127.0.0.1"] + record
    adv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if threading.current_thread() is threading.main_thread():  # launchd stops us with SIGTERM
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    log(f"bridge up: '{name}' at http://{addr}:{port}/eSCL via {u.desc}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        adv.terminate()
        srv.server_close()
    return 1 if state["gone"] else 0


def log_once(msg):
    """Log a repeating idle message only when it changes."""
    state = os.path.expanduser("~/Library/Logs/.hp520-scanbridge.state")
    try:
        with open(state) as f:
            last = f.read()
    except OSError:
        last = None
    if msg != last:
        if msg:
            log(msg)
        try:
            with open(state, "w") as f:
                f.write(msg)
        except OSError:
            pass


def redirect_log():
    """Under launchd there is no terminal: write to ~/Library/Logs instead."""
    if sys.stdout.isatty():
        return
    path = os.path.expanduser("~/Library/Logs/hp520-scanbridge.log")
    try:
        if os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    f = open(path, "a", buffering=1)
    os.dup2(f.fileno(), 1)
    os.dup2(f.fileno(), 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", metavar="FILE", help="scan one A4 page to FILE (JPEG) and exit")
    ap.add_argument("--lan", action="store_true", help="share the scanner with the local network")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--upstream", metavar="HOST:PORT|auto",
                    help="diagnostics: forward to an eSCL server instead of USB; 'auto' finds macOS' ippusbd scanner")
    ap.add_argument("-v", "--verbose", action="store_true", help="log request and error bodies")
    a = ap.parse_args()
    global VERBOSE
    VERBOSE = a.verbose
    if a.upstream:
        if a.upstream == "auto":
            found = find_native_scanner()
            if not found:
                sys.exit("macOS does not announce an eSCL scanner for a USB HP printer")
            host, port, name = found
            log(f"macOS scanner '{name}' at {host}:{port}")
        else:
            host, _, port = a.upstream.rpartition(":")
            port = int(port)
        u = HttpUpstream(host, port)
        if a.test:
            test_scan(u, a.test)
            return 0
        return serve(u, a.port, a.lan)
    if not a.test:
        redirect_log()
    try:
        u = UsbHttp()
    except LookupError as e:
        if a.test:
            sys.exit(str(e))
        if isinstance(e, Busy):
            msg = ("macOS' own IPP-over-USB service (ippusbd) holds the printer and announces "
                   "the scanner itself; the bridge stays idle. (" + str(e) + ")")
            pause = 120
        else:
            msg, pause = str(e), 30
        log_once(msg)
        time.sleep(pause)  # launchd starts us again and we look once more
        sys.exit(0)
    log_once("")
    try:
        if a.test:
            test_scan(u, a.test)
            return 0
        return serve(u, a.port, a.lan)
    finally:
        u.close()


if __name__ == "__main__":
    sys.exit(main())
