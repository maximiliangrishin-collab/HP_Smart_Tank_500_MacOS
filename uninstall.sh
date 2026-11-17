#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Removes everything install.sh put on the system.
set -uo pipefail
DEST=/Library/Printers/HPSmartTank520
QUEUE=HP_Smart_Tank_520
LABEL=org.hp520-macos.scanbridge
DAEMON=/Library/LaunchDaemons/$LABEL.plist
OLD_AGENT=/Library/LaunchAgents/$LABEL.plist

echo "Removing the HP Smart Tank 520 driver and scan bridge (password required)."
sudo -v || exit 1
sudo launchctl bootout "system/$LABEL" 2>/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
sudo rm -f "$DAEMON" "$OLD_AGENT"
if lpstat -p "$QUEUE" >/dev/null 2>&1; then sudo lpadmin -x "$QUEUE"; echo "print queue removed"; fi
sudo rm -rf "$DEST"
sudo rm -f /Library/Logs/hp520-scanbridge.log* /Library/Logs/.hp520-scanbridge.state
rm -f ~/Library/Logs/hp520-scanbridge.log* ~/Library/Logs/.hp520-scanbridge.state
echo "Done."
