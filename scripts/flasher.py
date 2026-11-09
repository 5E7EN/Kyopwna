#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 5E7EN <https://github.com/5E7EN>
# SPDX-License-Identifier: AGPL-3.0-or-later
#

"""
** Kyopwna **
Kyocera partition patcher flasher.

Usage: sudo python3 flasher.py
"""

import struct
import sys
import time
import os
import hashlib
import usb.core
import usb.util

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

IMAGES = [
    ("aboot", "../images/aboot_patched.mbn"),
    ("sbl1",  "../images/sbl1_patched.mbn"),
]


KC_VID = 0x0482
KC_PID = 0x0AA4

CMD_HELLO = 0x01
CMD_HELLO_RESP = 0x02
CMD_END_IMAGE_TX = 0x04
CMD_RESET = 0x07
CMD_EXEC = 0x0B

VCMD_EMMC_LBA_READ = 0xA3
VCMD_EMMC_LBA_READ_RESP = 0xA4
VCMD_EMMC_STREAM_WRITE = 0xBA

MODE_CMD_EXEC = 3
MAX_USB = 0x4000
MAX_EMMC_CHUNK = 0x3E00
BA_CHUNK_SIZE = 1048576
MS_PER_MB = 1500
MIN_TIMEOUT_MS = 3000
HDR_FMT, HDR_SZ = "<II", 8
TIMEOUT_MS = 30000


class SaharaUSB:
    def __init__(self, vid=KC_VID, pid=KC_PID):
        self.timeout = TIMEOUT_MS
        self._dev = self._ep_in = self._ep_out = None
        dev = usb.core.find(idVendor=vid, idProduct=pid)
        if dev is None:
            raise RuntimeError(f"Device {vid:04X}:{pid:04X} not found")
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        intf = dev.get_active_configuration()[(0, 0)]

        def bulk(direction):
            return usb.util.find_descriptor(
                intf,
                custom_match=lambda e: (
                    usb.util.endpoint_direction(e.bEndpointAddress) == direction
                    and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                ),
            )

        self._ep_in, self._ep_out = bulk(usb.util.ENDPOINT_IN), bulk(usb.util.ENDPOINT_OUT)
        if self._ep_in is None or self._ep_out is None:
            raise RuntimeError("Bulk endpoints not found")
        self._dev = dev

    def send(self, data):
        return self._ep_out.write(data, timeout=self.timeout)

    def recv(self, max_len=MAX_USB, timeout_ms=None):
        return bytes(self._ep_in.read(max_len, timeout=timeout_ms or self.timeout))

    def drain(self, timeout_ms=100):
        try:
            self._ep_in.read(MAX_USB, timeout=timeout_ms)
        except usb.core.USBError:
            pass

    def close(self):
        if self._dev:
            usb.util.dispose_resources(self._dev)
            self._dev = None


class SaharaSession:
    def __init__(self, usb):
        self.usb = usb
        self._handshake()

    def _handshake(self):
        try:
            data = self.usb.recv(timeout_ms=1500)
        except Exception:
            data = None

        if data is None or len(data) < HDR_SZ:
            return

        cmd, _ = parse_hdr(data)
        if cmd != CMD_HELLO:
            raise ProtocolError(f"Expected HELLO (1), got {cmd:#x}")

        version, version_compat, max_cmd_len, _ = struct.unpack_from("<IIII", data, 8)
        if max_cmd_len <= 0x400:
            print("[Sahara] WARNING: PBL Sahara - vendor cmds unavailable.")

        hello_resp = struct.pack("<IIIIII", version, version_compat, 0, MODE_CMD_EXEC, 0, 0) + b"\x00" * 16
        tx_pkt = build_pkt(CMD_HELLO_RESP, hello_resp)
        self.usb.send(tx_pkt)

        while True:
            data = self.usb.recv()
            cmd, _ = parse_hdr(data)
            if cmd == CMD_END_IMAGE_TX:
                img_id, err = struct.unpack_from("<II", data, 8)
                raise ProtocolError(f"Device rejected HELLO_RESP: img_id={img_id:#x} err={err:#x}")
            if cmd == CMD_HELLO:
                self.usb.send(tx_pkt)
                continue
            if cmd == CMD_EXEC:
                break

        time.sleep(0.05)

    def send_reset(self):
        self.usb.send(build_pkt(CMD_RESET))
        try:
            self.usb.recv()
        except Exception:
            pass

    def emmc_lba_read(self, lba, byte_count):
        if byte_count > MAX_EMMC_CHUNK:
            raise ValueError(f"byte_count {byte_count} > max {MAX_EMMC_CHUNK}")
        timeout = _timeout_for(byte_count)
        self.usb.send(build_pkt(VCMD_EMMC_LBA_READ, struct.pack("<II", lba, byte_count)))
        data = self.usb.recv(timeout_ms=timeout)
        cmd, _ = parse_hdr(data)
        if cmd == CMD_END_IMAGE_TX:
            raise ProtocolError(f"eMMC LBA read failed: {data.hex()}")
        if cmd != VCMD_EMMC_LBA_READ_RESP:
            raise ProtocolError(f"Expected 0xA4, got {cmd:#x}")
        resp_lba, resp_len = struct.unpack_from("<II", data, 8)
        if resp_lba != lba:
            raise ProtocolError(f"LBA echo mismatch: sent {lba}, got {resp_lba}")
        result = bytearray(data[16:])
        while len(result) < resp_len:
            result.extend(self.usb.recv(timeout_ms=timeout))
        return bytes(result[:resp_len])

    def read_gpt(self):
        raw = bytearray()
        lba, remaining = 0, 34
        chunk_sectors = MAX_EMMC_CHUNK // 512
        while remaining > 0:
            n = min(remaining, chunk_sectors)
            raw.extend(self.emmc_lba_read(lba, n * 512))
            lba += n
            remaining -= n
        return _parse_gpt(bytes(raw))

    def clear_write_protect(self, start_lba, sector_count):
        self.usb.drain()
        self.usb.send(struct.pack("<IIII", 0xAB, 16, start_lba, sector_count))
        try:
            self.usb.recv(timeout_ms=5000)
        except Exception:
            pass

    def flash(self, start_lba, data, label=""):
        if len(data) % 512:
            raise ValueError("data length must be a multiple of 512")
        timeout = _timeout_for(len(data))
        sector_count = len(data) // 512

        self.clear_write_protect(start_lba, sector_count)
        self.usb.drain()

        for attempt in range(3):
            self.usb.send(build_pkt(VCMD_EMMC_STREAM_WRITE, struct.pack("<II", start_lba, sector_count)))
            resp = self.usb.recv(timeout_ms=timeout)
            if len(resp) >= 16:
                cmd_id, _, _, p3 = struct.unpack_from("<IIII", resp)
                if cmd_id == CMD_END_IMAGE_TX and p3 != 0:
                    if attempt < 2:
                        time.sleep(0.5)
                        continue
                    raise ProtocolError(f"Device not ready for write")
            break

        sent, total = 0, len(data)
        max_pkt = self.usb._ep_out.wMaxPacketSize
        while sent < total:
            chunk_size = min(total - sent, BA_CHUNK_SIZE)
            actual = self.usb._ep_out.write(data[sent:sent + chunk_size], timeout=timeout)
            if actual == chunk_size and chunk_size % max_pkt == 0:
                self.usb._ep_out.write(b"", timeout=timeout)
            sent += actual

        resp = self.usb.recv(timeout_ms=timeout)
        if len(resp) >= 16:
            cmd_id = struct.unpack_from("<I", resp)[0]
            if cmd_id == CMD_END_IMAGE_TX:
                raise ProtocolError(f"Flash failed")


class ProtocolError(Exception):
    pass


def _timeout_for(nbytes):
    return max(MIN_TIMEOUT_MS, int(1000 + (nbytes / (1024 * 1024)) * MS_PER_MB))


def build_pkt(cmd, payload=b""):
    return struct.pack(HDR_FMT, cmd, HDR_SZ + len(payload)) + payload


def parse_hdr(data):
    if len(data) < HDR_SZ:
        raise ValueError(f"Packet too short: {len(data)}B")
    return struct.unpack_from(HDR_FMT, data, 0)


def _parse_gpt(data):
    hdr_off = 512
    if len(data) < hdr_off + 92:
        raise ValueError("GPT data too short")
    if data[hdr_off:hdr_off + 8] != b"EFI PART":
        raise ValueError("No GPT signature at LBA 1")
    part_start_lba, num_parts, entry_sz = struct.unpack_from("<QII", data, hdr_off + 72)
    entries_off = int(part_start_lba) * 512
    partitions = []
    for i in range(num_parts):
        off = entries_off + i * entry_sz
        if off + entry_sz > len(data):
            break
        if data[off:off + 16] == b"\x00" * 16:
            continue
        start_lba, end_lba = struct.unpack_from("<QQ", data, off + 32)
        name = data[off + 56:off + 56 + 72].decode("utf-16-le").rstrip("\x00")
        sectors = end_lba - start_lba + 1
        partitions.append({"name": name, "start_lba": start_lba, "sectors": sectors, "bytes": sectors * 512})
    return partitions


def _find_partition(partitions, name):
    for p in partitions:
        if p["name"].lower() == name.lower():
            return p
    print(f"[Error] Partition '{name}' not found.", file=sys.stderr)
    sys.exit(1)


def _prepare_image(raw, part):
    part_size = part["sectors"] * 512
    if len(raw) % 512:
        raw += b"\x00" * (512 - len(raw) % 512)
    if len(raw) // 512 > part["sectors"]:
        print(f"[Error] Image too large for partition '{part['name']}'", file=sys.stderr)
        sys.exit(1)
    if len(raw) < part_size:
        raw = raw.ljust(part_size, b"\x00")
    return raw


def main():
    print()
    print("=" * 62)
    print("  ** Kyopwna **")
    print("  jt/u/BenTorah")
    print("  v1.0")
    print("=" * 62)
    print()

    _v = {
        b"\xc3\xf1\x05\x91\x93\x52\xa5\x6e\xd5\x93\xc2\x30\xad\x1b\x9b\x14\xfb\x67\x1c\x30\x4b\x21\x27\xe8\x57\x2c\x03\x53\x9f\x54\xe0\x45",
        b"\x39\xa5\x1f\x58\x37\x8f\xa1\x5b\xb8\xd0\xeb\x89\x37\xff\x1c\x4d\x53\x7f\x04\x88\x53\x4b\xb8\xd0\xc1\xeb\x7f\x53\xf0\x22\x92\xcc",
    }
    for _, filename in IMAGES:
        fpath = os.path.join(SCRIPT_DIR, filename)
        if not os.path.isfile(fpath) or hashlib.sha256(open(fpath, "rb").read()).digest() not in _v:
            print("[Error] Failed to initialize flash context", file=sys.stderr)
            sys.exit(1)

    try:
        transport = SaharaUSB()
        session = SaharaSession(transport)
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        sys.exit(1)

    try:
        parts = session.read_gpt()

        to_flash = []
        for part_name, filename in IMAGES:
            p = _find_partition(parts, part_name)
            to_flash.append((p, os.path.join(SCRIPT_DIR, filename)))

        for i, (p, fpath) in enumerate(to_flash, 1):
            print(f"Flashing {os.path.basename(fpath)} -> {p['name']}...")
            raw = open(fpath, "rb").read()
            data = _prepare_image(raw, p)
            session.flash(p["start_lba"], data, label=p["name"])
            print(f"  done ({i}/{len(to_flash)})")

        session.send_reset()

        print()
        print("----------------------------------------------------")
        print("[*] Device will now reboot in EDL 9008 mode!")
        print("[*] Remove the SD card and continue with exploit.py")
        print("----------------------------------------------------")

    except ProtocolError as e:
        print(f"-- Flash failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n-- Interrupted")
    finally:
        transport.close()


if __name__ == "__main__":
    main()
