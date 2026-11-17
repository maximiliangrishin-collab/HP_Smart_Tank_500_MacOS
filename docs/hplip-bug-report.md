# HPLIP bug: black is dropped in colour mode on pcl3gui2 printers (Smart Tank 520/540)

**Component:** hpcups (`prnt/hpcups/Pcl3Gui2.cpp`, `prnt/hpcups/HPCupsFilter.cpp`),
PPD `prnt/drv/hpcups.drv.in` (class `Athena-L`, and every other class that uses
`hpPrinterLanguage "pcl3gui2"` with `ColorModel RGB` = `cupsColorSpace 17`)

**Version:** HPLIP 3.25.8

**Device:** HP Smart Tank 520_540 series, USB, firmware `LAP1CN2446AR`

## Symptom

In *Color* mode, pure black (text, black fills) does not print at all; the area
stays white. Colours and composite greys print correctly. *Black Only Grayscale*
prints black correctly.

## Cause

1. The PPD's `ColorModel RGB` asks for an RGBW raster (`cupsColorSpace 17`).
2. For RGBW input, `HPCupsFilter::extractBlackPixels()` moves every pixel with
   `W == 0` to the 1-bit black plane and **sets its RGB value to white**
   (`0xFF, 0xFF, 0xFF`).
3. `Pcl3Gui2` is hard-wired to colour-only CRD
   (`crd_type = eCrd_color_only; // pcl3 printers support RGB only ref:hplip-1701`),
   so `Pcl3Gui2::Encapsulate()` never sends the black plane.

Black pixels are therefore removed from the colour plane and the black plane is
discarded. Measured on a test page: the PCL data for the black title text is
**0 bytes** in RGBW mode and 13 195 bytes once the raster is plain RGB.

## Fix

Either

* request plain RGB in the PPD for pcl3gui2 classes:
  `*ColorModel RGB/Color: "<</cupsColorSpace 1/cupsBitsPerColor 8/cupsRowStep 0>>setpagedevice"`
  (verified on the device: black text and fills print correctly), or
* in `extractBlackPixels()`, leave the pixel black in RGB (`0,0,0`) instead of
  white when the encapsulator uses `eCrd_color_only`.

The first variant is used in this project.
