"""In-memory single-tag device for examples, never evidence of RF compatibility."""
from .protocol import crc16


class MemorySerial:
    def __init__(self, antennas=4):
        self.timeout = 0.05
        self.buffer = bytearray()
        self.closed = False
        self.powers = [20] * antennas
        self.region = bytes((1, 19, 0))
        self.profile_id = 7
        self.query = bytes((6, 1))
        self.mask = (1 << antennas) - 1
        self.banks = {0: bytearray(8),
                      1: bytearray.fromhex("00003000E20000000000000000000001") + bytearray(112),
                      2: bytearray.fromhex("E28000000000000000000001"),
                      3: bytearray(256)}

    def read(self, size):
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def close(self):
        self.closed = True

    def _matches(self, mask):
        if len(mask) < 4:
            return False
        bank, start, bits = mask[0], int.from_bytes(mask[1:3], "big"), mask[3]
        if bank not in self.banks or len(mask[4:]) * 8 != bits:
            return False
        memory = self.banks[bank]
        if start + bits > len(memory)*8:
            return False
        target = int.from_bytes(mask[4:], "big")
        actual = int.from_bytes(memory, "big") >> (len(memory)*8 - start - bits)
        return actual & ((1 << bits)-1) == target

    def write(self, wire):
        if self.closed:
            raise OSError("Simulation closed")
        if wire[0]+1 != len(wire) or crc16(wire):
            raise OSError("Bad simulation command")
        command, payload = wire[2], wire[3:-2]
        status, data = 0, b""
        if command == 0x21:
            data = bytes((0, 1, 0x71 if len(self.powers) == 1 else 0x75, 2, 255, 255, self.powers[0], 3, self.mask, 0, 0, 1))
        elif command == 0x2F:
            if len(payload) == 1:
                self.powers = [payload[0] & 127] * len(self.powers)
            elif len(payload) == len(self.powers):
                self.powers = [p & 127 for p in payload]
            else:
                status = 0xFD
        elif command == 0x94:
            data = bytes(self.powers)
        elif command == 0x22:
            if len(payload) == 4:
                self.region = payload[1:]
            else:
                status = 0xFD
        elif command == 0x9E:
            data = self.region
        elif command == 0x7F:
            if len(payload) == 1:
                if payload[0] & 128:
                    self.profile_id = payload[0] & 63
                if self.profile_id > 63:
                    status = 0xFD
                else:
                    data = bytes((self.profile_id,))
            elif len(payload) == 3 and payload[0] in (0, 1, 2):
                if payload[0]:
                    self.profile_id = int.from_bytes(payload[1:], "big")
                data = self.profile_id.to_bytes(2, "big")
            else:
                status = 0xFD
        elif command == 0xEB:
            if payload == b"\x09":
                data = self.query
            else:
                status = 0xFD
        elif command == 0xEA:
            if (len(payload) == 4 and payload[0] in (0, 1) and payload[1] == 9
                    and payload[2] <= 63 and payload[3] in (0, 1, 2, 3, 255)):
                self.query = payload[2:]
            else:
                status = 0xFD
        elif command == 0x3F:
            self.mask = payload[0] & 15
        elif command == 1:
            antenna = (payload[-2] & 15) + 1
            if len(payload) > 5 and not self._matches(payload[2:-3]):
                status = 0xFB
            else:
                words = int.from_bytes(self.banks[1][2:4], "big") >> 11
                epc = bytes(self.banks[1][4:4+words*2])
                status, data = 1, bytes((1 << (antenna-1), 1, len(epc))) + epc + bytes((65,))
        elif command in (2, 3, 0x15, 0x16):
            writing = command in (3, 0x16)
            ptr_size = 2 if command in (0x15, 0x16) else 1
            offset = 1 if writing else 0
            if payload[offset] != 255:
                status = 0xFD
            else:
                bank = payload[offset+1]
                start = int.from_bytes(payload[offset+2:offset+2+ptr_size], "big") * 2
                offset += 2+ptr_size
                words = payload[0] if writing else payload[offset]
                if not writing:
                    offset += 1
                written = payload[offset:offset+2*words] if writing else b""
                offset += len(written)
                password, mask = payload[offset:offset+4], payload[offset+4:]
                if password != bytes(4):
                    status = 5
                elif not self._matches(mask):
                    status = 0xFB
                elif bank not in self.banks or start+2*words > len(self.banks[bank]):
                    status, data = 0xFC, b"\x03"
                elif writing:
                    self.banks[bank][start:start+2*words] = written
                else:
                    data = bytes(self.banks[bank][start:start+2*words])
        else:
            status = 0xFE
        response = bytes((len(data)+5, 0, command, status)) + data
        self.buffer.extend(response + crc16(response).to_bytes(2, "little"))
        return len(wire)
