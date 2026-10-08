"""Portable high-level API for the documented Ex10 1/4-antenna profile."""
from contextlib import contextmanager
import re

from .errors import DeviceError, ProtocolError, RestoreError, ValidationError
from .protocol import SerialTransport, integer
from .configuration import validate_region, region_description


def hex_bytes(value, name, minimum=0, maximum=256, words=False):
    if not isinstance(value, str) or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
        raise ValidationError(name, f"{name} must be even-length hex without spaces")
    data = bytes.fromhex(value)
    if not minimum <= len(data) <= maximum or (words and len(data) % 2):
        raise ValidationError(name, f"Invalid {name} byte/word length")
    return data


def selector_bytes(selector):
    if not isinstance(selector, dict) or set(selector) != {"bank", "bit_address", "hex"}:
        raise ValidationError("selector", "selector requires exactly bank, bit_address, hex")
    bank = integer(selector["bank"], 1, 3, "selector.bank")
    address = integer(selector["bit_address"], 0, 16383, "selector.bit_address")
    data = hex_bytes(selector["hex"], "selector.hex", 1, 31)
    if address + len(data) * 8 > 16384:
        raise ValidationError("selector", "Selector exceeds memory bit address range")
    return bytes((bank,)) + address.to_bytes(2, "big") + bytes((len(data) * 8,)) + data


def parse_inventory(reply, antenna):
    if reply.status == 0xFB:
        if reply.data:
            raise ProtocolError("Unexpected no-tag payload")
        return []
    if reply.status not in (1, 2, 3, 4):
        raise DeviceError(reply.command, reply.status, reply.data)
    data = reply.data
    if len(data) < 2 or data[0] != 1 << (antenna - 1):
        raise ProtocolError("Invalid inventory antenna/data")
    tags, offset = [], 2
    for _ in range(data[1]):
        if offset >= len(data):
            raise ProtocolError("Missing tag record")
        flags = data[offset]
        offset += 1
        length = flags & 63
        extra = 7 if flags & 0x40 else 0
        if length < 2 or length % 2 or offset + length + 1 + extra > len(data):
            raise ProtocolError("Malformed FastID/phase record")
        epc_length = length - 12 if flags & 0x80 else length
        if epc_length < 2:
            raise ProtocolError("FastID record must contain EPC and 12-byte TID")
        tag = {"epc": data[offset:offset+epc_length].hex().upper(),
               "antenna": antenna, "rssi_raw": data[offset+length]}
        if flags & 0x80:
            tag["tid"] = data[offset+epc_length:offset+length].hex().upper()
            tag["tid_source"] = "fastid"
        tags.append(tag)
        offset += length + 1
        if extra:
            # Ex10 RWDev.cs parses the two phases and frequency as MSB-first.
            tag["phase_begin_raw"] = int.from_bytes(data[offset:offset+2], "big")
            tag["phase_end_raw"] = int.from_bytes(data[offset+2:offset+4], "big")
            tag["frequency_khz"] = int.from_bytes(data[offset+4:offset+7], "big")
            # Match the supplied demo's normal EPC display, modulo 180 degrees.
            tag["phase_begin_degrees"] = tag["phase_begin_raw"] * 0.087 % 180
            tag["phase_end_degrees"] = tag["phase_end_raw"] * 0.087 % 180
            offset += extra
    if offset != len(data):
        raise ProtocolError("Unexpected inventory trailing data")
    return tags


class Reader:
    def __init__(self, transport, antennas=4, simulated=False):
        if type(antennas) is not int or antennas not in (1, 4):
            raise ValueError("antennas must be 1 or 4")
        self.transport, self.antennas, self.simulated = transport, antennas, simulated

    @classmethod
    def open(cls, port, baud=115200, antennas="auto", address=255, *, timeout=3.0):
        # Validate before touching a physical serial port.
        automatic = antennas == "auto"
        if not automatic and (type(antennas) is not int or antennas not in (1, 4)):
            raise ValueError("antennas must be auto, 1 or 4")
        transport = SerialTransport.open(port, baud, address=address, timeout=timeout)
        reader = cls(transport, 1 if automatic else antennas)
        try:
            info = reader.info()
            if automatic:
                from .models import antenna_count
                reader.antennas = antenna_count(info["reader_type"])
            return reader
        except BaseException:
            transport.close()
            raise

    @classmethod
    def simulate(cls, antennas=4):
        from .simulation import MemorySerial
        if type(antennas) is not int or antennas not in (1, 4):
            raise ValueError("antennas must be 1 or 4")
        return cls(SerialTransport(MemorySerial(antennas)), antennas, simulated=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.transport.close()

    def _call(self, command, payload=b""):
        reply = self.transport.exchange(command, payload)[0]
        if reply.status != 0:
            raise DeviceError(command, reply.status, reply.data)
        return reply.data

    def info(self):
        with self.transport.lock:
            data = self._call(0x21)
            # Manual length example conflicts with its table; use the table's 12-byte format.
            if len(data) != 12:
                raise ProtocolError("Unsupported reader-info format; expected classic 12-byte profile")
            return {"simulated": self.simulated, "address": self.transport.address,
                    "firmware": f"{data[0]}.{data[1]}", "reader_type": data[2],
                    "protocol_bits": data[3], "configured_antennas": self.antennas,
                    "power_dbm": data[6], "antenna_mask": data[8] & 15,
                    "frequency_bytes": data[4:6].hex().upper(), "check_antenna": data[11]}

    def power(self, dbm=None, persist=False, powers_dbm=None):
        if type(persist) is not bool:
            raise ValueError("persist must be boolean")
        if dbm is not None and powers_dbm is not None:
            raise ValueError("Use either dbm or powers_dbm, not both")
        desired = None
        if dbm is not None:
            integer(dbm, 0, 30, "dbm")
            desired = [dbm] * self.antennas
        if powers_dbm is not None:
            if not isinstance(powers_dbm, (list, tuple)) or len(powers_dbm) != self.antennas:
                raise ValueError("powers_dbm must contain one value for every configured antenna")
            desired = [integer(p, 0, 30, "powers_dbm value") for p in powers_dbm]
        with self.transport.lock:
            # Check vector support and physical port count BEFORE changing power.
            actual = self._read_powers()
            if desired is not None:
                values = [dbm] if dbm is not None else desired
                self._call(0x2F, bytes(p | (0 if persist else 128) for p in values))
                actual = self._read_powers()
            if desired is not None and actual != desired:
                raise ProtocolError("Power readback mismatch; setting may have changed")
            uniform = len(set(actual)) == 1
            return {"dbm": actual[0] if uniform else None, "powers_dbm": actual,
                    "scope": "global" if uniform else "per_antenna", "simulated": self.simulated}

    def _read_powers(self):
        data = self._call(0x94)
        if len(data) != self.antennas or any(p > 30 for p in data):
            raise ProtocolError("Power vector does not match configured antennas or 0..30 dBm range")
        return list(data)

    def region(self, band=None, min_channel=None, max_channel=None, persist=False):
        if type(persist) is not bool:
            raise ValueError("persist must be boolean")
        setting = any(v is not None for v in (band, min_channel, max_channel))
        if setting:
            validate_region(band, min_channel, max_channel)
        with self.transport.lock:
            # Preflight avoids writing through firmware without readback support.
            current = self._read_region()
            if setting:
                self._call(0x22, bytes((0 if persist else 1, band, max_channel, min_channel)))
                current = self._read_region()
                if current != (band, min_channel, max_channel):
                    raise ProtocolError("Region readback mismatch; setting may have changed")
            return dict(region_description(*current), simulated=self.simulated)

    def _read_region(self):
        data = self._call(0x9E)
        if len(data) != 3 or data[2] > data[1]:
            raise ProtocolError("Invalid region response")
        return data[0], data[2], data[1]

    def profile(self, profile_id=None, persist=False, format="auto"):
        if format not in ("auto", "legacy", "extended"):
            raise ValueError("format must be auto, legacy or extended")
        if type(persist) is not bool:
            raise ValueError("persist must be boolean")
        if profile_id is not None:
            integer(profile_id, 0, 63 if format == "legacy" else 65535, "profile_id")
        def read(selected_format):
            query = b"\x00" if selected_format == "legacy" else b"\x00\x00\x00"
            size = 1 if selected_format == "legacy" else 2
            data = self._call(0x7F, query)
            if len(data) != size or (selected_format == "legacy" and data[0] > 63):
                raise ProtocolError("Invalid profile response for requested format")
            return int.from_bytes(data, "big")

        with self.transport.lock:
            if format == "auto":
                try:
                    actual = read("extended")
                    format = "extended"
                except DeviceError as error:
                    # Only a definitive format/command rejection permits a read-only
                    # fallback. Never retry after timeout, bad CRC or any Set attempt.
                    if error.status not in (0xFD, 0xFE):
                        raise
                    actual = read("legacy")
                    format = "legacy"
            else:
                actual = read(format)
            size = 1 if format == "legacy" else 2
            if profile_id is not None:
                integer(profile_id, 0, 63 if format == "legacy" else 65535, "profile_id")
                payload = (bytes((profile_id | 128 | (0 if persist else 64),)) if format == "legacy"
                           else bytes((1 if persist else 2,)) + profile_id.to_bytes(2, "big"))
                echoed = self._call(0x7F, payload)
                if len(echoed) != size:
                    raise ProtocolError("Invalid profile set response; setting may have changed")
                actual = read(format)
                if actual != profile_id:
                    raise ProtocolError("Profile readback mismatch; setting may have changed")
            # These are raw ZK IDs, never Nation/R2000 mode numbers.
            return {"profile_id": actual, "format": format, "namespace": "zk",
                    "simulated": self.simulated}

    def query(self, q=None, session=None, persist=False, *, phase=None, fast_tid=None):
        """Read/set native Ex10 CFG9. Ordinary inventory has separate per-call Q/Session."""
        if type(persist) is not bool:
            raise ValueError("persist must be boolean")
        if any(value is not None and type(value) is not bool for value in (phase,fast_tid)):
            raise ValueError("phase and fast_tid must be boolean or None")
        if q is not None:
            integer(q, 0, 15, "q")
        if session is not None:
            integer(session, 0, 255, "session")
            if session not in (0, 1, 2, 3, 255):
                raise ValueError("session must be 0..3 or 255 (Auto)")
        with self.transport.lock:
            actual = self._read_query()
            if q is not None or session is not None or phase is not None or fast_tid is not None:
                q_flags = (actual[0] & 0x30) | (actual[0] & 15 if q is None else q)
                for value,bit in ((phase,0x10),(fast_tid,0x20)):
                    if value is not None: q_flags=(q_flags|bit) if value else (q_flags&~bit)
                desired = (q_flags,
                           actual[1] if session is None else session)
                # CFG9 writes/saves both fields together, including the preserved field.
                reply = self._call(0xEA, bytes((0 if persist else 1, 9, *desired)))
                if reply:
                    raise ProtocolError("Invalid query set response; setting may have changed")
                actual = self._read_query()
                if actual != desired:
                    raise ProtocolError("Query readback mismatch; setting may have changed")
            return {"q": actual[0]&15, "session": actual[1],
                    "phase":bool(actual[0]&0x10),"fast_tid":bool(actual[0]&0x20),
                    "q_flags_raw":actual[0],"simulated": self.simulated}

    def _read_query(self):
        data = self._call(0xEB, b"\x09")
        if len(data) != 2 or data[0] & 0xC0 or data[1] not in (0, 1, 2, 3, 255):
            raise ProtocolError("Invalid CFG9 Q/Session response")
        return tuple(data)

    def inventory(self, antennas=None, scan_time=3, q=4, session=0, target=0, selector=None,
                  phase=False, fast_tid=False):
        if antennas is None:
            antennas = list(range(1, self.antennas + 1))
        if not isinstance(antennas, (list, tuple)) or not 1 <= len(antennas) <= self.antennas:
            raise ValueError("antennas must be a nonempty list")
        for antenna in antennas:
            integer(antenna, 1, self.antennas, "antenna")
        if len(set(antennas)) != len(antennas):
            raise ValueError("Duplicate antennas")
        integer(scan_time, 3, 20, "scan_time")
        integer(q, 0, 15, "q")
        integer(session, 0, 3, "session")
        integer(target, 0, 1, "target")
        if type(phase) is not bool or type(fast_tid) is not bool:
            raise ValueError("phase and fast_tid must be boolean")
        mask = b"" if selector is None else selector_bytes(selector)
        tags, rounds = [], []
        with self.transport.lock:
            for antenna in antennas:
                flags = q | (0x10 if phase else 0) | (0x20 if fast_tid else 0)
                payload = bytes((flags, session)) + mask + bytes((target, 0x80 + antenna - 1, scan_time))
                replies = self.transport.exchange(1, payload, inventory=True)
                for reply in replies:
                    tags.extend(parse_inventory(reply, antenna))
                rounds.append({"antenna": antenna, "status": replies[-1].status,
                               "partial": replies[-1].status in (2, 4)})
        return {"tags": tags, "rounds": rounds, "simulated": self.simulated}

    @contextmanager
    def _selected_antenna(self, antenna):
        with self.transport.lock:
            if self.antennas == 1:
                yield
                return
            previous = self.info()["antenna_mask"]
            if previous == 0:
                raise ProtocolError("Cannot restore empty antenna mask")
            succeeded, cause = False, None
            try:
                self._call(0x3F, bytes((0x80 | (1 << (antenna - 1)),)))
                yield
                succeeded = True
            except BaseException as error:
                cause = error
                raise
            finally:
                try:
                    self._call(0x3F, bytes((0x80 | previous,)))
                except Exception as restore_error:
                    raise RestoreError(succeeded, cause or restore_error) from restore_error

    def read(self, *, antenna, bank, word_address, words, selector, password="00000000"):
        integer(antenna, 1, self.antennas, "antenna")
        integer(bank, 0, 3, "bank")
        integer(word_address, 0, 65535, "word_address")
        integer(words, 1, 120, "words")
        if word_address + words > 65536:
            raise ValueError("Read address wraps")
        mask = selector_bytes(selector)
        pwd = hex_bytes(password, "password", 4, 4)
        extended = word_address > 255
        payload = b"\xff" + bytes((bank,)) + word_address.to_bytes(2 if extended else 1, "big")
        payload += bytes((words,)) + pwd + mask
        with self._selected_antenna(antenna):
            data = self._call(0x15 if extended else 2, payload)
            if len(data) != words * 2:
                raise ProtocolError("Read response length does not match requested word count")
        return {"hex": data.hex().upper(), "words": words, "simulated": self.simulated}

    def write(self, *, antenna, bank, word_address, hex, selector, password="00000000"):
        integer(antenna, 1, self.antennas, "antenna")
        if type(bank) is not int or bank not in (1, 3):
            raise ValidationError("bank", "Write supports EPC (1) and User (3) only")
        integer(word_address, 0, 65535, "word_address")
        if bank == 1 and word_address == 0:
            raise ValidationError("word_address", "Do not write EPC CRC word 0")
        data = hex_bytes(hex, "hex", 2, 64, words=True)
        if word_address + len(data)//2 > 65536:
            raise ValidationError("word_address", "Write address wraps")
        mask = selector_bytes(selector)
        pwd = hex_bytes(password, "password", 4, 4)
        extended = word_address > 255
        payload = bytes((len(data)//2, 255, bank)) + word_address.to_bytes(2 if extended else 1, "big")
        payload += data + pwd + mask
        with self._selected_antenna(antenna):
            reply = self._call(0x16 if extended else 3, payload)
            if reply:
                raise ProtocolError("Unexpected write response; verify tag before retrying")
        return {"acknowledged": True, "verified": False, "words": len(data)//2,
                "simulated": self.simulated}
