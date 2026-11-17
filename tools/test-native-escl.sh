#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Diagnostic: scan one A4 page through the scanner that macOS itself announces
# for the printer (via /usr/libexec/ippusbd), bypassing Image Capture and the
# bridge. Writes ~/Desktop/native-escl-test.jpg.
NAME="${1:-HP Smart Tank 520_540 series}"
OUT=~/Desktop/native-escl-test.jpg

echo "== stopping the bridge, so only the macOS scanner is involved"
launchctl bootout "gui/$(id -u)/org.hp520-macos.scanbridge" 2>/dev/null

echo "== resolving '$NAME' (_uscan._tcp)"
RES=$( (dns-sd -L "$NAME" _uscan._tcp local. & sleep 3; kill $!) 2>/dev/null )
ADDR=$(echo "$RES" | sed -n 's/.*can be reached at \([^ ]*\).*/\1/p' | head -1)
RS=$(echo "$RES" | grep -oE '(^| )rs=[^ ]*' | head -1 | sed 's/.*rs=//'); RS=${RS#/}; RS=${RS:-eSCL}
[ -n "$ADDR" ] || { echo "not found in Bonjour"; echo "$RES"; exit 1; }
H=${ADDR%:*}; H=${H%.}; PORT=${ADDR##*:}
BASE="http://$H:$PORT/$RS"
echo "address: $BASE"
echo "$RES" | grep -E "reached|txtvers" | head -3

echo "== status"
curl -s -m 15 "$BASE/ScannerStatus" | grep -oE '<pwg:State>[^<]*|<scan:JobState>[^<]*' | head -5

echo "== scan job"
SETTINGS='<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
<pwg:Version>2.63</pwg:Version><scan:Intent>Document</scan:Intent>
<pwg:ScanRegions><pwg:ScanRegion><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>2480</pwg:Width><pwg:Height>3508</pwg:Height>
</pwg:ScanRegion></pwg:ScanRegions><pwg:InputSource>Platen</pwg:InputSource><scan:ColorMode>RGB24</scan:ColorMode>
<scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution>
<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat><scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
</scan:ScanSettings>'
HDRS=$(curl -s -m 60 -D - -o /dev/null -X POST -H 'Content-Type: text/xml' --data-binary "$SETTINGS" "$BASE/ScanJobs" | tr -d '\r')
echo "$HDRS" | head -1
LOC=$(echo "$HDRS" | awk 'tolower($1)=="location:" {print $2}')
[ -n "$LOC" ] || { echo "no job created"; exit 1; }
case "$LOC" in http*) ;; *) LOC="http://$H:$PORT$LOC" ;; esac
echo "job: $LOC"

T0=$(date +%s)
for _ in $(seq 1 90); do
  CODE=$(curl -s -m 180 -o "$OUT" -w '%{http_code}' "${LOC%/}/NextDocument")
  echo "NextDocument: HTTP $CODE ($(( $(date +%s) - T0 )) s)"
  [ "$CODE" = 200 ] && break
  [ "$CODE" = 503 ] || [ "$CODE" = 409 ] || break
  sleep 2
done
ls -la "$OUT" 2>/dev/null && file "$OUT"
echo
echo "The bridge stays stopped. Start it again with:"
echo "  launchctl bootstrap gui/\$(id -u) /Library/LaunchAgents/org.hp520-macos.scanbridge.plist"
