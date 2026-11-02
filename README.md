# Kyopwna

<p align="center">
    <img src="https://img.shields.io/badge/built-BS%22D-brightgreen" />
</p>

Bootloader unlock for Kyocera E4610 (and others)

---

### ⚠️ Disclaimer:
### As with all my tools, I take no responsibility for anything that happens to your device as a result of usage.
### Read through the instructions once first and understand what it entails before continuing!

This guide is for the E4610 only. Other devices with the MSM8909 SoC are supported, but the payload needs to be modified accordingly.  
If you want to see your device added, please open an issue.

## Process  

0. With device on, enable OEM unlocking and ADB in developer menu.
1. Next, we need to enter a flashable sahara mode. There are two ways to go about this:
    1. Kyocera: use SD card marker to enter SBL1 sahara A004 mode
    2. Universal (does not work on my Kyocera): `adb reboot edl`, or use an EDL cable
2. Once in sahara mode, FIRST flash the custom `aboot_patched.mbn` to the aboot partition, and THEN flash `sbl1_patched.mbn` to the sbl1 partition.
    1. Kyocera: use custom SBL1 sahara client
        - `sudo python3 <path-to-sahara-client> flash aboot aboot_patched.mbn` (or whatever the command syntax is). THEN,
        - `sudo python3 <path-to-sahara-client> flash sbl1 sbl1_patched.mbn`. THEN,
        - Reboot device/remove battery
    2. Universal: _Coming soon™_
3. The intention with the above is to brick the device so that it consistently falls into EDL 9008 mode.
4. Next, with the device in EDL 9008 mode, hold the POWER button and run `sudo python3 exploit.py`. The device should boot Android normally.
5. Connect to ADB, and run `adb reboot bootloader`
6. The device will reboot into EDL 9008 mode. Hold the POWER button and run `sudo python3 exploit.py`. The device will boot into fastboot mode.
7. Connect to fastboot, and run:
    - `fastboot oem unlock`. THEN,
    - `fastboot flash sbl1 sbl1_stock.mbn`. THEN,
    - `fastboot flash aboot aboot_stock.mbn`. THEN,
    - `fastboot flash boot boot_patched-magisk30_7.img`. THEN,
    - `fastboot reboot`
8. The device will now reboot normally with orange state screen - indicating successful unlock!

Launch Magisk 30.7 via adb:
`adb shell monkey -p com.topjohnwu.magisk -c android.intent.category.LAUNCHER 1`

---

Eventually, this will be made into an "ACE" framework to support custom payloads for this and other affected chipsets.  
For now, it is a standalone bootloader unlock tool.


## Credits

- **[CVE-2026-25262](https://nvd.nist.gov/vuln/detail/cve-2026-25262)**
- **Alexander Kozlov & Sergey Anufrienko** - Original vulnerability discovery & [presentation](https://www.youtube.com/watch?v=ZlWvdRBuxpc)
- **[5E7EN](https://github.com/5E7EN)** - PoC development & exploit implementation

If you integrate this tool elsewhere, please credit accordingly and keep the license in mind.

