# Changes to the HPLIP sources

`driver/hpcups/` is the `hpcups` CUPS filter from **HPLIP 3.25.8**
(<https://sourceforge.net/projects/hplip/files/hplip/3.25.8/>, `prnt/hpcups/`),
plus three helper files from the same release. HP's licence headers are kept
unchanged; see `COPYING.hplip`.

| Change | Why |
|---|---|
| Added `Makefile` (standalone build, no autotools) | HPLIP's build system does not target macOS |
| Built with `-DDISABLE_IMAGEPROCESSOR`; removed `libImageProcessor-*.so` | closed x86 Linux library, used only by `ljzjstream` laser printers |
| D-Bus left undefined (`HAVE_DBUS` not set) | job notifications for the HPLIP Linux tray app only |
| `common/utils.{c,h}` copied as `hplip_utils.{c,h}`; includes updated in 9 files | macOS file systems are case-insensitive by default, and `utils.h` collides with hpcups' own `Utils.h` |
| `io/hpmud/hpmud.h` copied | header-only dependency of `dbuscomm.h` |
| `genJPEGStrips.cpp`: `<malloc.h>` → `<stdlib.h>` | `<malloc.h>` does not exist on macOS |
| `genPCLm.cpp`: `"./../../common/utils.h"` → `"hplip_utils.h"` | flat source tree |
| `CONFDIR` defined as `/Library/Printers/HPSmartTank520` | only used to look for optional HP binary plug-ins, which are not needed |

## PPD

`driver/HP-Smart-Tank-520.ppd` was produced with `ppdc` from HPLIP's
`prnt/drv/hpcups.drv.in` (model *HP Smart Tank 520 540 Series*, class
`Athena-L`, language `pcl3gui2`). Edits:

1. `*cupsFilter` points to the absolute path `/Library/Printers/HPSmartTank520/hpcups`.
2. **`*ColorModel RGB` requests `cupsColorSpace 1` (RGB) instead of `17` (RGBW).**
   This fixes black disappearing from colour prints; see
   `docs/hplip-bug-report.md`.
3. `*NickName` marks the PPD as the macOS build.
