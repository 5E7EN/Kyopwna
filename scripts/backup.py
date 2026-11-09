#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 5E7EN <https://github.com/5E7EN>
# SPDX-License-Identifier: AGPL-3.0-or-later
#

"""
** Kyopwna **
Kyocera partition backup tool.

Usage: sudo python3 backup.py
"""

import struct
import sys
import time
import os
import usb.core
import usb.util

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(SCRIPT_DIR, "..", "BACKUP")

PARTITIONS_TO_BACKUP = ["sbl1", "aboot", "boot"]

KC_VID = 0x0482
KC_PID = 0x0AA4

CMD_HELLO = 0x01
CMD_HELLO_RESP = 0x02
CMD_END_IMAGE_TX = 0x04
CMD_EXEC = 0x0B

VCMD_EMMC_LBA_READ = 0xA3
VCMD_EMMC_LBA_READ_RESP = 0xA4

MODE_CMD_EXEC = 3
MAX_USB = 0x4000
MAX_EMMC_CHUNK = 0x3E00
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

    def dump(self, start_lba, sector_count, label=""):
        result = bytearray()
        lba, remaining = start_lba, sector_count
        chunk_sectors = MAX_EMMC_CHUNK // 512
        tag = f"[{label}] " if label else "[eMMC] "
        while remaining > 0:
            n = min(remaining, chunk_sectors)
            print(
                f"\r{tag}LBA {lba} ({len(result)//1024}/{sector_count*512//1024} KB)",
                end="", flush=True,
            )
            result.extend(self.emmc_lba_read(lba, n * 512))
            lba += n
            remaining -= n
        print(f"\r{tag}Done: {len(result)} bytes from LBA {start_lba}   ")
        return bytes(result)


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


def main():
    print()
    print("=" * 62)
    print("  ** Kyopwna **")
    print("  Partition Backup Tool")
    print("  jt/u/BenTorah")
    print("=" * 62)
    print()

    os.makedirs(BACKUP_DIR, exist_ok=True)

    try:
        transport = SaharaUSB()
        session = SaharaSession(transport)
    except Exception as e:
        print(f"[Error] {e}", file=sys.stderr)
        sys.exit(1)

    try:
        parts = session.read_gpt()

        to_backup = []
        for part_name in PARTITIONS_TO_BACKUP:
            p = _find_partition(parts, part_name)
            to_backup.append(p)

        for i, p in enumerate(to_backup, 1):
            out_path = os.path.join(BACKUP_DIR, f"{p['name']}.mbn")
            print(f"Reading {p['name']} ({p['bytes']:,} bytes, {p['sectors']} sectors)...")
            data = session.dump(p["start_lba"], p["sectors"], label=p["name"])
            with open(out_path, "wb") as f:
                f.write(data)
            print(f"  Saved to {out_path} ({i}/{len(to_backup)})")

        print()
        print("----------------------------------------------------")
        print(f"[*] Backup complete! Files saved to: {BACKUP_DIR}")
        print("----------------------------------------------------")

    except ProtocolError as e:
        print(f"-- Backup failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n-- Interrupted")
    finally:
        transport.close()


if __name__ == "__main__":
    main()
