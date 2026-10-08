"""Ex10 manual v2.25 section 3; no vendor binary dependency."""
from dataclasses import dataclass
import math
import os
import threading
import time

from .errors import ProtocolError, TransportError, ValidationError


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(name, f"{name} must be an integer in {low}..{high}")
    return value


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8408 if value & 1 else 0)
    return value


def command_frame(address, command, data=b""):
    integer(address, 0, 255, "address")
    integer(command, 0, 255, "command")
    if len(data) > 251:
        raise ValueError("Command exceeds 256-byte frame")
    body = bytes((len(data) + 4, address, command)) + data
    return body + crc16(body).to_bytes(2, "little")


@dataclass(frozen=True)
class Response:
    address: int
    command: int
    status: int
    data: bytes


def decode_response(frame):
    if len(frame) < 6 or frame[0] + 1 != len(frame):
        raise ProtocolError("Invalid response length")
    if crc16(frame) != 0:
        raise ProtocolError("CRC mismatch")
    return Response(frame[1], frame[2], frame[3], frame[4:-2])


class SerialTransport:
    """One outstanding command. Any uncertain stream becomes unusable until reopened."""
    def __init__(self, serial_port, address=255, timeout=3.0):
        integer(address, 0, 255, "address")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("timeout must be finite and in (0, 30]")
        self.serial = serial_port
        self.address, self.timeout = address, timeout
        self.lock = threading.RLock()
        self.failed = False
        self.closed = False
        # Configure once, before transmitting. pySerial's Windows timeout setter
        # also calls SetCommState; calling it between response fragments can
        # disrupt USB UART traffic. Deadlines below poll this fixed read timeout.
        poll_timeout = min(0.05, timeout)
        if self.serial.timeout != poll_timeout:
            self.serial.timeout = poll_timeout

    @classmethod
    def open(cls, port, baud=115200, **kwargs):
        import serial
        if baud not in (9600, 19200, 38400, 57600, 115200, 921600):
            raise ValueError("Unsupported module baud")
        options = {"exclusive": True} if os.name != "nt" else {}
        try:
            device = serial.Serial(port, baud, timeout=0.05, write_timeout=1,
                                   xonxoff=False, rtscts=False, dsrdtr=False, **options)
        except OSError as error:
            raise TransportError("Cannot open serial port; check port, permissions and other applications") from error
        try:
            device.reset_input_buffer()
            return cls(device, **kwargs)
        except OSError as error:
            device.close()
            raise TransportError("Cannot initialize serial port") from error
        except BaseException:
            device.close()
            raise

    def _read_exact(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError("Response timeout; outcome may be unknown. Close and reopen reader.")
            chunk = self.serial.read(size - len(data))
            data.extend(chunk)
        return bytes(data)

    def exchange(self, command, data=b"", *, inventory=False):
        wire = command_frame(self.address, command, data)
        with self.lock:
            if self.closed or self.failed:
                raise TransportError("Transport closed/desynchronized; close and reopen reader")
            try:
                deadline = time.monotonic() + self.timeout
                if self.serial.write(wire) != len(wire):
                    raise TransportError("Short serial write; outcome unknown")
                responses = []
                for _ in range(2048):
                    size = self._read_exact(1, deadline)[0]
                    if size < 5:
                        raise ProtocolError("Invalid response length byte")
                    reply = decode_response(bytes((size,)) + self._read_exact(size, deadline))
                    if reply.command != command or (self.address != 255 and reply.address != self.address):
                        raise ProtocolError("Unexpected command/address; refusing stale response")
                    if self.address == 255:
                        if reply.address == 255:
                            raise ProtocolError("Reader returned broadcast address")
                        self.address = reply.address
                    responses.append(reply)
                    if not inventory or reply.status != 3:
                        return responses
                raise ProtocolError("Inventory frame limit exceeded")
            except (OSError, ProtocolError, TransportError) as error:
                self.failed = True
                if isinstance(error, OSError):
                    raise TransportError("Serial I/O failed; close and reopen reader") from error
                raise

    def close(self):
        with self.lock:
            self.closed = True
            self.serial.close()
