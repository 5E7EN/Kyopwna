<h1 align='center'>
Kyopwna
</h1>

<p align='center'>
Bootloader unlock for Kyocera E4610 (and others)
</p>

<p align="center">
    <img src="https://img.shields.io/badge/built-BS%22D-brightgreen" />
</p>

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

> [!CAUTION]
> **[Back up your stock partitions](#backup)** before continuing!

0. With device on, enable OEM unlocking and ADB in developer menu.
1. Next, we need to enter a flashable sahara mode. There are two ways to go about this:
    1. Kyocera: use SD card marker to enter SBL1 sahara A004 mode
        - Plug a microSD card into your computer
        - Copy the microSD/`NOTPUSH` file onto the SD card
        - Eject the SD card and insert it into your Kyocera
        - Reconnect battery and press power button. The device's outer LED should be steady green. Continue below #2 (`flasher.py` script).    
    2. Universal (does not work on my Kyocera): `adb reboot edl`, or use an EDL cable
2. Once in sahara mode, flash the included "patched" partitions:
    1. Kyocera: with the device connected in SBL1 sahara mode, run the SBL flasher script:
        1. `cd <path-to-this-folder>`
        2. Install libusb and also `pip install -r requirements.txt` (pyusb)
        3. `sudo python3 scripts/flasher.py`
        4. Device will reboot into EDL 9008 mode after it successfully flashes both patched images.
        5. **Remove the microSD card from your Kyocera, reconnect the battery, and press power button.** This will bring you back into EDL 9008 mode.
    2. Universal: use bkerler's EDL client (w/ the included firehose loader, or any)
        1. With the device in EDL 9008 mode, run the `sudo python3 scripts/firehose-deauther.py` script.
            - This payload will neutralize auth and re-enum USB. This will allow you to use any firehose loader, regardless of its signature. The included loader - `images/KyopwnaLoader.mbn` - supports EL3-level capabilities, including peek/poke.
            - **NOTE:** In most cases, the eMMC will enforce write-protection for boot related partitions. This is noticable when trying to write to such partitions and receiving a “read back verify” failure. If this occurs, append the `--disable-wp` flag to the script. This only needs to be done once. THEN,
        2. `edl --loader images/KyopwnaLoader.mbn w aboot images/aboot_patched.mbn`. THEN,
        3. `edl --loader images/KyopwnaLoader.mbn w sbl1 images/sbl1_patched.mbn`. THEN,
        4. `edl reset`
3. The intention with the above is to brick the device so that it reliably falls into EDL 9008 mode.
4. Next, with the device in EDL 9008 mode, hold the POWER button and simultaneously run `sudo python3 scripts/exploit.py`. The device will boot into fastboot mode.
5. Connect to fastboot, and run:
    - `fastboot oem unlock`. THEN,
    - `fastboot flash sbl1 images/sbl1_stock.mbn`. THEN,
    - `fastboot flash aboot images/aboot_stock.mbn`. THEN,
    - `fastboot flash boot images/boot_patched-magisk30_7.img`. THEN,
    - `fastboot reboot`
6. The device will now reboot normally with orange state screen - indicating successful unlock!

Launch Magisk 30.7 via adb:
`adb shell monkey -p com.topjohnwu.magisk -c android.intent.category.LAUNCHER 1`

## Backup

Before flashing anything, back up your stock `aboot` and `sbl1` partitions. You will want them in case the process goes sour for some reason.

1. Kyocera: enter SBL1 sahara mode using the microSD method described in step 1.i above, then run the backup script:
    - `sudo python3 scripts/backup.py`
    - This will dump those partitions from your device and save them to a `BACKUP/` folder in the project root.
2. Universal: with the device in EDL 9008 mode, use bkerler's EDL client:
    - `mkdir BACKUP`
    - `edl --loader images/KyopwnaLoader.mbn r sbl1 BACKUP/sbl1.mbn`
    - `edl --loader images/KyopwnaLoader.mbn r aboot BACKUP/aboot.mbn`
    - `edl --loader images/KyopwnaLoader.mbn r boot BACKUP/boot.mbn`

---

Eventually, this will be made into an "ACE" framework to support custom payloads for this and other affected chipsets.  
For now, it is a standalone bootloader unlock tool.


## Credits

- **[CVE-2026-25262](https://nvd.nist.gov/vuln/detail/cve-2026-25262)**
- **Alexander Kozlov & Sergey Anufrienko** - Original vulnerability discovery & [presentation](https://www.youtube.com/watch?v=ZlWvdRBuxpc)
- **[Leo Buskin](https://github.com/leobuskin)** - Initial motivation and groundwork for SBL flash script
- **[5E7EN](https://github.com/5E7EN)** - **PoC development & exploit implementation**

If you integrate this tool elsewhere, please credit accordingly and keep the license in mind.

