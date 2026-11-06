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
This guide assumes you are running Linux and are comfortable with its command line.  
This process will **NOT** factory reset your device!

## Requirements

- E4610
- microSD card (w/ adapter)
- Micro USB to USB-A data cable
- Linux (VM with USB passthrough works too)
- ADB & fastboot

## Process  
### Please be meticulous in following the instructions. Limited support will be provided.  

0. With device on, enable OEM unlocking and ADB in developer menu.
1. Next, we need to enter a flashable sahara mode. There are two ways to go about this:
    1. Kyocera: use SD card marker to enter SBL1 sahara A004 mode
        - Plug a microSD card into your computer
        - Copy the `NOTPUSH` file found in the "microSD/" folder onto the SD card
        - Eject the SD card and insert it into your Kyocera
        - Reconnect battery and press power button. The device's outer LED should be steady green. Continue below #2 (`flasher.py` script).    
    2. Universal (does not work on my Kyocera): `adb reboot edl`, or use an EDL cable
2. Once in sahara mode, flash the included "patched" partitions:
    1. Kyocera: with the device connected in SBL1 sahara mode, run the SBL flasher script:
        - `cd <path-to-this-folder>`
        - Install libusb and also `pip install -r requirements.txt` (pyusb)
        - `sudo python3 flasher.py`
        - Device will reboot into EDL 9008 mode after it successfully flashes both patched images.
        - **Remove the microSD card from your Kyocera, reconnect the battery, and press power button.** This will bring you back into EDL 9008 mode.
    2. Universal: _Coming soon™_
3. The intention with the above is to brick the device so that it reliably falls into EDL 9008 mode.
4. Next, with the device in EDL 9008 mode, hold the POWER button and simultaneously run `sudo python3 exploit.py`. The device will boot into fastboot mode.
5. Connect to fastboot, and run:
    - `fastboot oem unlock`. THEN,
    - `fastboot flash sbl1 sbl1_stock.mbn`. THEN,
    - `fastboot flash aboot aboot_stock.mbn`. THEN,
    - `fastboot flash boot boot_patched-magisk30_7.img`. THEN,
    - `fastboot reboot`
6. The device will now reboot normally with orange state screen - indicating successful unlock!

Launch Magisk 30.7 via adb:
`adb shell monkey -p com.topjohnwu.magisk -c android.intent.category.LAUNCHER 1`

---

Eventually, this will be made into an "ACE" framework to support custom payloads for this and other affected chipsets.  
For now, it is a standalone bootloader unlock tool.


## Credits

- **[CVE-2026-25262](https://nvd.nist.gov/vuln/detail/cve-2026-25262)**
- **Alexander Kozlov & Sergey Anufrienko** - Original vulnerability discovery & [presentation](https://www.youtube.com/watch?v=ZlWvdRBuxpc)
- **[Leo Buskin](https://github.com/leobuskin)** - Initial motivation and groundwork for SBL flash script
- **[5E7EN](https://github.com/5E7EN)** - **PoC development & exploit implementation**

If you integrate this tool elsewhere, please credit accordingly and keep the license in mind.

