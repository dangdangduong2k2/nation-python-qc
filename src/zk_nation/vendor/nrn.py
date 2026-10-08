import logging
import threading
import time
from enum import IntEnum
from typing import Optional

import serial
import serial.tools.list_ports

# === SDK Configuration ===
SDK_NAME = "Nextwaves RFID SDK"
SDK_VERSION = "1.0.0"
DEFAULT_LOG_LEVEL = logging.INFO

# === Protocol Constants ===
CRC16_CCITT_INIT = 0x0000
CRC16_CCITT_POLY = 0x8005

FRAME_HEADER = 0x5A
PROTO_TYPE = 0x00
PROTO_VER = 0x01
RS485_FLAG = 0x00
READER_NOTIFY_FLAG = 0x00  # Set to 0 for upper computer commands


# === Logging Configuration ===
def setup_logging(
    level: int = DEFAULT_LOG_LEVEL, logger_name: str = "nextwaves_rfid"
) -> logging.Logger:
    """
    Setup logging configuration for the Nextwaves RFID SDK.

    Args:
        level: Logging level (default: INFO)
        logger_name: Name of the logger

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(logger_name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)

    return logger


# Initialize default logger
logger = setup_logging()


# === SDK Exceptions ===
class NextwavesSDKError(Exception):
    """Base exception for Nextwaves SDK errors."""

    pass


class ConnectionError(NextwavesSDKError):
    """Raised when connection to the RFID reader fails."""

    pass


class ProtocolError(NextwavesSDKError):
    """Raised when protocol communication fails."""

    pass


class ConfigurationError(NextwavesSDKError):
    """Raised when reader configuration is invalid."""

    pass


class TagOperationError(NextwavesSDKError):
    """Raised when tag operations fail."""

    pass


class UARTConnection:
    """
    Handles UART communication with the RFID reader.

    This class manages the serial connection, provides thread-safe communication,
    and handles basic UART operations like sending/receiving data.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 0.5,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the UARTConnection class.

        Args:
            port: Serial port path (e.g. COM3, /dev/ttyUSB0)
            baudrate: Baud rate (default: 115200)
            timeout: Read timeout in seconds
            logger: Logger instance (optional, uses default if not provided)
        """
        self.port_name = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.lock = threading.Lock()
        self._inventory_running = False
        self._inventory_thread = None
        self.logger = logger or logging.getLogger("nextwaves_rfid.uart")

        # 0 = No Beep, 1 = Always Beep, 2 = Beep on New Tag
        self.beeper_mode = 0

    def open(self):
        """
        Open and configure the serial port.

        Raises:
            RuntimeError: If the serial port cannot be opened
        """
        if self.ser and self.ser.is_open:
            return  # Already open

        try:
            self.ser = serial.Serial(
                port=self.port_name,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            if not self.ser.is_open:
                self.ser.open()
            self.logger.info(f"UART Connected to {self.port_name} @ {self.baudrate}bps")
        except serial.SerialException as e:
            self.logger.error(f"Failed to open serial port: {e}")
            raise ConnectionError(f"Failed to open serial port: {e}") from e

    def close(self):
        """
        Close the serial port if open.
        """
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.logger.info(f"UART Disconnected from {self.port_name}")

    def send(self, data: bytes):
        """
        Send raw bytes through UART.

        Args:
            data: Byte array to send

        Raises:
            RuntimeError: If UART port is not open
        """
        if not self.ser or not self.ser.is_open:
            raise ConnectionError("UART port is not open")
        with self.lock:
            self.ser.write(data)

    def receive(self, size: int = 64) -> bytes:
        """
        Receive a fixed number of bytes from UART.

        Args:
            size: Number of bytes to read

        Returns:
            Bytes read from the port

        Raises:
            RuntimeError: If UART port is not open
        """
        if not self.ser or not self.ser.is_open:
            raise ConnectionError("UART port is not open")
        with self.lock:
            data = self.ser.read(size)
        return data

    def send_raw_bytes(self, frame: bytes):
        """Send raw bytes through the serial port.

        Args:
            frame: Byte array to send

        Raises:
            IOError: If serial port is not open
        """
        if not self.ser or not self.ser.is_open:
            raise OSError("Serial port not open")
        self.logger.debug(f"Sending ({len(frame)} bytes): {frame.hex()}")
        with self.lock:
            self.ser.write(frame)
            self.ser.flush()

    def flush_input(self):
        """
        Flush input buffer to discard old data.
        """
        if self.ser:
            self.ser.reset_input_buffer()

    def is_open(self) -> bool:
        return self.ser.is_open if self.ser else False


class MID(IntEnum):
    """
    Message ID constants for RFID reader communication protocol.

    These constants define the various command and response types
    used in communication with the NRN RFID reader.
    """

    # === Reader Configuration ===
    QUERY_INFO = 0x0100
    CONFIRM_CONNECTION = 0x12

    # === RFID Inventory ===
    READ_EPC_TAG = (0x02 << 8) | 0x10
    PHASE_INVENTORY = 0x0214
    STOP_INVENTORY = (0x02 << 8) | 0xFF
    STOP_OPERATION = 0xFF
    READ_END = 0x1231
    WRITE_EPC_TAG = 0x0211

    # === Error Handling ===
    ERROR_NOTIFICATION = 0x00

    # === RFID Baseband ===
    CONFIG_BASEBAND = 0x020B
    QUERY_BASEBAND = 0x020C

    # === Session Management ===
    SESSION = 0x03

    # === Power Control ===
    CONFIGURE_READER_POWER = 0x0201
    QUERY_READER_POWER = 0x0202
    READER_POWER_CALIBRATION = 0x0103
    QUERY_POWER_CALIBRATION = 0x0104

    # === Filter Settings ===
    SET_FILTER_SETTINGS = 0x0209
    QUERY_FILTER_SETTINGS = 0x020A

    # === RF Band & Frequency ===
    SET_RF_BAND = 0x0203
    QUERY_RF_BAND = 0x0204
    SET_WORKING_FREQUENCY = 0x0205
    QUERY_WORKING_FREQUENCY = 0x0206

    # === RFID Ability ===
    QUERY_RFID_ABILITY = 0x1000

    # === Antenna Control ===
    CONFIGURE_ANTENNA_ENABLE = 0x0203
    QUERY_ANTENNA_ENABLE = 0x0202

    # === Buzzer Control ===
    BUZZER_SWITCH = (0x01 << 8) | 0x1E

    # === GPIO Commands (Category 1) ===
    CONFIGURE_GPO = 0x0109
    QUERY_GPI = 0x010A
    CONFIGURE_GPI_TRIGGER = 0x010B
    QUERY_GPI_TRIGGER = 0x010C

    @classmethod
    def all_read_end_mids(cls) -> list:
        """Return all MIDs that indicate read end."""
        return [0x01, 0x21, 0x31]


# === Beeper Modes ===
BEEPER_MODES = {
    "QUIET": 0x00,
    "BEEP_AFTER_INVENTORY": 0x01,
    "BEEP_AFTER_TAG": 0x02,
}

# === RF Profiles ===
RF_PROFILES = {
    0: {"id": 0, "name": "Profile 0", "description": "Default baseband profile"},
    1: {"id": 1, "name": "Profile 1", "description": "High performance profile"},
    2: {"id": 2, "name": "Profile 2", "description": "Dense tag profile"},
}


class NRNReader:
    """
    Main class for communicating with NRN RFID readers.

    This class provides a high-level interface for RFID operations including
    inventory, tag reading/writing, antenna control, and reader configuration.

    This is the primary SDK class for the Nextwaves RFID module.
    """

    DEFAULT_TIMEOUT = 0.5

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the NRN RFID reader SDK.

        Args:
            port: Serial port path (e.g. COM3, /dev/ttyUSB0)
            baudrate: Baud rate for communication
            timeout: Communication timeout in seconds
            logger: Logger instance (optional, uses default if not provided)
        """
        self.port = port or NRNReader.DEFAULT_PORT
        self.baudrate = baudrate or NRNReader.DEFAULT_BAUDRATE
        self.timeout = timeout or NRNReader.DEFAULT_TIMEOUT
        self.logger = logger or logging.getLogger("nextwaves_rfid.reader")
        self.uart = UARTConnection(self.port, self.baudrate, self.timeout, self.logger)
        self.rs485 = False

        # Initialize antenna configuration
        self._ext_ant_masks: dict[int, int] = {i: 0 for i in range(1, 33)}  # Main Ant 1-32
        self.antenna_mask = 0x00000001  # Default to Main Antenna 1

        self.logger.info(f"Initialized {SDK_NAME} v{SDK_VERSION}")
        self.logger.info(f"Reader configured: {self.port} @ {self.baudrate}bps")

    @classmethod
    def set_uart_defaults(cls, port: str, baudrate: int, timeout: float = 0.5):
        cls.DEFAULT_PORT = port
        cls.DEFAULT_BAUDRATE = baudrate
        cls.DEFAULT_TIMEOUT = timeout

    # === SDK Information ===

    @classmethod
    def get_sdk_info(cls) -> dict[str, str]:
        """Get SDK information.

        Returns:
            Dictionary containing SDK name and version
        """
        return {
            "name": SDK_NAME,
            "version": SDK_VERSION,
            "description": "Nextwaves RFID Reader SDK for Python",
        }

    def get_reader_info(self) -> dict[str, str]:
        """Get current reader configuration information.

        Returns:
            Dictionary containing reader configuration
        """
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout": self.timeout,
            "rs485": self.rs485,
            "antenna_mask": hex(self.antenna_mask),
        }

    def set_log_level(self, level: int):
        """Set the logging level for the SDK.

        Args:
            level: Logging level (logging.DEBUG, logging.INFO, etc.)
        """
        self.logger.setLevel(level)
        self.uart.logger.setLevel(level)
        self.logger.info(f"Log level set to {logging.getLevelName(level)}")

    # === Connection Management ===

    def open(self):
        """Open the UART connection to the reader."""
        self.logger.info("Opening connection to RFID reader")
        self.uart.open()

    def close(self):
        """Close the UART connection to the reader."""
        self.logger.info("Closing connection to RFID reader")
        self.uart.flush_input()
        self.uart.close()

    def send(self, data: bytes):
        """Send data to the reader."""
        self.uart.send(data)

    def receive(self, size: int) -> bytes:
        """Receive data from the reader."""
        return self.uart.receive(size)

    # === Protocol Utilities ===

    @staticmethod
    def crc16_ccitt(data: bytes) -> int:
        """
        Calculate CRC16-CCITT checksum for data validation.

        Args:
            data: Input data bytes

        Returns:
            16-bit CRC checksum
        """
        crc = 0x0000

        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                crc = (crc << 1 ^ 4129) & 65535 if crc & 32768 else crc << 1 & 65535
        return crc

    @classmethod
    def build_frame(
        cls, mid, payload: bytes = b"", rs485: bool = False, notify: bool = False
    ) -> bytes:
        """
        Build a complete protocol frame for communication.

        Args:
            mid: Message ID (can be MID enum or integer)
            payload: Data payload bytes
            rs485: Whether to include RS485 address field
            notify: Whether this is a notification frame

        Returns:
            Complete frame bytes ready for transmission
        """
        frame_header = b"\x5a"

        mid_value = getattr(mid, "value", mid)
        category = (mid_value >> 8) & 0xFF
        mid_code = mid_value & 0xFF

        pcw = cls.build_pcw(category, mid_code, rs485=rs485, notify=notify)
        pcw_bytes = pcw.to_bytes(4, "big")
        addr_bytes = b"\x00" if rs485 else b""
        length_bytes = len(payload).to_bytes(2, "big")

        frame_content = pcw_bytes + addr_bytes + length_bytes + payload
        crc_bytes = cls.crc16_ccitt(frame_content).to_bytes(2, "big")

        return frame_header + frame_content + crc_bytes

    @classmethod
    def build_pcw(cls, category: int, mid: int, rs485: bool = False, notify: bool = False) -> int:
        """
        Build Protocol Control Word (PCW) for frame header.

        Args:
            category: Protocol category
            mid: Message ID
            rs485: Whether RS485 addressing is used
            notify: Whether this is a notification frame

        Returns:
            32-bit PCW value
        """
        pcw = (PROTO_TYPE << 24) | (PROTO_VER << 16)
        if rs485:
            pcw |= 1 << 13
        if notify:
            pcw |= 1 << 12
        pcw |= (category << 8) | mid
        return pcw

    @classmethod
    def parse_frame(cls, raw: bytes) -> dict:
        """
        Parses a received frame from the RFID reader.
        Validates CRC and extracts PCW fields and data.
        :param raw: Full raw frame bytes including header
        :return: Parsed dictionary or raises ValueError
        """
        if len(raw) < 9:
            raise ValueError("Frame too short")

        if raw[0] != FRAME_HEADER:
            raise ValueError("Invalid frame header")

        offset = 1  # skip header

        # === Protocol Control Word ===
        pcw_bytes = raw[offset : offset + 4]
        pcw = int.from_bytes(pcw_bytes, "big")
        offset += 4

        proto_type = (pcw >> 24) & 0xFF
        proto_ver = (pcw >> 16) & 0xFF
        rs485_flag = (pcw >> 13) & 0x01
        notify_flag = (pcw >> 12) & 0x01
        category = (pcw >> 8) & 0xFF
        mid = pcw & 0xFF

        response_type = "notification" if notify_flag else "response"

        # === Optional Serial Address ===
        if rs485_flag:
            addr = raw[offset]
            offset += 1
        else:
            addr = None

        # === Data Length ===
        data_len = int.from_bytes(raw[offset : offset + 2], "big")
        offset += 2

        if offset + data_len + 2 > len(raw):
            raise ValueError("Frame length mismatch or truncated")

        # === Data ===
        data = raw[offset : offset + data_len]
        offset += data_len

        # === CRC ===
        received_crc = int.from_bytes(raw[offset : offset + 2], "big")
        calculated_crc = cls.crc16_ccitt(raw[1:offset])  # exclude frame header

        if received_crc != calculated_crc:
            raise ValueError(
                f"CRC mismatch! Got 0x{received_crc:04X}, expected 0x{calculated_crc:04X}"
            )

        return {
            "valid": True,
            "type": response_type,
            "proto_type": proto_type,
            "proto_ver": proto_ver,
            "rs485": bool(rs485_flag),
            "notify": bool(notify_flag),
            "pcw": pcw,
            "category": category,
            "mid": mid,
            "address": addr,
            "data_length": data_len,
            "data": data,
            "crc": received_crc,
            "raw": raw,
        }

    def extract_valid_frames(self, data: bytes) -> list[bytes]:
        """
        Extracts all valid protocol frames from raw byte stream based on:
        - Header = 0x5A
        - Length field (2 bytes)
        - CRC16-CCITT check
        """
        frames = []
        i = 0
        while i < len(data):
            if data[i] != 0x5A:
                i += 1
                continue

            # Minimum valid length
            if i + 9 > len(data):
                break

            length = int.from_bytes(data[i + 5 : i + 7], "big")
            addr_len = 1 if self.rs485 else 0
            full_len = 1 + 4 + addr_len + 2 + length + 2

            if i + full_len > len(data):
                break

            frame = data[i : i + full_len]
            crc_calc = self.crc16_ccitt(frame[1:-2])
            crc_recv = int.from_bytes(frame[-2:], "big")

            if crc_calc == crc_recv:
                frames.append(frame)
            else:
                self.logger.debug(
                    f" CRC mismatch at index {i}: expected={hex(crc_calc)}, got={hex(crc_recv)}"
                )

            i += full_len

        return frames

    def Connect_Reader_And_Initialize(self) -> bool:
        try:
            self.uart.flush_input()

            self.logger.debug(" Sending STOP command to ensure Idle state...")
            stop_frame = self.build_frame(MID.STOP_INVENTORY, payload=b"")
            self.uart.send(stop_frame)

            time.sleep(0.1)
            raw = self.uart.receive(64)

            if not raw:
                self.logger.debug(" No response received.")
                return False

            frame = self.parse_frame(raw)
            self.logger.debug(f" MID: {frame['mid']}, Data: {frame['data'].hex()}")

            if frame["mid"] in [0x01, MID.STOP_INVENTORY & 0xFF] and frame["data"][0] == 0x00:
                self.logger.info(" Reader successfully initialized.")
                return True

            self.logger.error(" Invalid STOP response.")
            return False

        except Exception as e:
            self.logger.error(f"Exception during init: {e}")
            return False

    def query_rfid_ability(self) -> dict:
        """
        Sends MID=0x00 CAT=0x10 'Query RFID Ability' to get reader capability.
        Corrects for real-world byte order (MAX first, MIN second).
        """
        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=0x1000, payload=b"", rs485=self.rs485)
            self.uart.send(frame)

            time.sleep(0.1)
            raw = self.uart.receive(128)
            if not raw:
                raise Exception("No response from reader.")

            frames = self.extract_valid_frames(raw)
            self.logger.debug(f" Raw frames count: {len(frames)}")

            for idx, frame in enumerate(frames):
                self.logger.debug(f" Frame[{idx}]: {frame.hex()}")
                pcw = int.from_bytes(frame[1:5], "big")
                mid = pcw & 0xFF
                cat = (pcw >> 8) & 0xFF
                self.logger.debug(f"🔎 MID: {mid:02X}, CAT: {cat:02X}")
                if cat != 0x10 or mid != 0x00:
                    continue

                offset = 8 if self.rs485 else 7
                payload = frame[offset:-2]
                self.logger.debug(f" Payload Bytes: {payload.hex()}")

                if len(payload) < 3:
                    raise Exception("Payload too short.")

                max_power = payload[0]
                min_power = payload[1]
                antenna_count = payload[2]

                freq_list = []
                protocols = []

                try:
                    freq_list_len = payload[3]
                    freq_list = list(payload[4 : 4 + freq_list_len])
                    self.logger.info(f" Frequency List Length: {freq_list_len}, Data: {freq_list}")

                    protocol_offset = 4 + freq_list_len
                    protocol_list_len = payload[protocol_offset]
                    protocols = list(
                        payload[protocol_offset + 1 : protocol_offset + 1 + protocol_list_len]
                    )
                    self.logger.info(
                        f" Protocol List Length: {protocol_list_len}, Data: {protocols}"
                    )
                except IndexError:
                    self.logger.info(" Reader reports no frequencies or protocols.")

                return {
                    "min_power_dbm": min_power,
                    "max_power_dbm": max_power,
                    "antenna_count": antenna_count,
                    "frequencies": freq_list,
                    "rfid_protocols": protocols,
                }

            raise Exception("No matching response frame for RFID ability.")

        except Exception as e:
            self.logger.error(f"Error in query_rfid_ability: {e}")
            return {}

    def Query_Reader_Information(self) -> dict:
        """
        Sends MID=0x00 Category=0x01 'Query Reader Information' and parses the response.
        """
        try:
            self.uart.flush_input()

            frame = self.build_frame(MID.QUERY_INFO, payload=b"")
            self.uart.send(frame)

            time.sleep(0.1)
            raw = self.uart.receive(128)
            if not raw:
                self.logger.debug(" No response received.")
                return {}

            frame_data = self.parse_frame(raw)

            if frame_data["mid"] != 0x00 or frame_data["category"] != 0x01:
                self.logger.error(" Unexpected MID or Category.")
                return {}

            return self._parse_query_info_data(frame_data["data"]) or {}

        except Exception as e:
            self.logger.error(f"Exception in Query_Reader_Information: {e}")
            return {}

    @staticmethod
    def _parse_query_info_data(data: bytes) -> dict:
        result = {}
        try:
            offset = 0

            # 1. Serial Number (next byte is length)
            if offset + 2 > len(data):
                return result
            sn_length = data[offset + 1]
            serial = data[offset + 2 : offset + 2 + sn_length].decode("ascii", errors="ignore")
            result["serial_number"] = serial.strip()
            offset += 2 + sn_length

            # 2. Power-on time (U32)
            if offset + 4 > len(data):
                return result
            result["power_on_time_sec"] = int.from_bytes(data[offset : offset + 4], "big")
            offset += 4

            # 3. Baseband compile time (tag 0x00, then length, then ASCII string)
            if offset + 2 > len(data):
                return result
            bb_len = data[offset + 1]
            baseband = data[offset + 2 : offset + 2 + bb_len].decode("ascii", errors="ignore")
            result["baseband_compile_time"] = baseband.strip()
            offset += 2 + bb_len

            # 4. Optional tags (0x01: app_version, 0x02: os_version, 0x03: app_compile_time)
            while offset + 2 <= len(data):
                tag = data[offset]
                length = data[offset + 1]
                value = data[offset + 2 : offset + 2 + length]
                offset += 2 + length

                if tag == 0x01 and len(value) == 4:
                    v = int.from_bytes(value, "big")
                    result["app_version"] = f"V{(v>>24)&0xFF}.{(v>>16)&0xFF}.{(v>>8)&0xFF}.{v&0xFF}"
                elif tag == 0x02:
                    result["os_version"] = value.decode("ascii", errors="ignore").strip()
                elif tag == 0x03:
                    result["app_compile_time"] = value.decode("ascii", errors="ignore").strip()
                else:
                    continue

        except Exception as e:
            result["error"] = f"Parsing exception: {e}"

        return result

    # write for me function save antennmask from frontend
    def save_antenna_mask(self, antenna_mask: int) -> bool:
        """Save the antenna mask for the reader.
        :param antenna_mask: 32-bit integer representing the antenna mask.
        :return: True if successful, False otherwise.
        """
        if not (0 <= antenna_mask <= 0xFFFFFFFF):
            raise ValueError("Antenna mask must be a 32-bit unsigned integer")
        self.antenna_mask = antenna_mask
        return self.antenna_mask

    def build_epc_read_payload(
        self, antenna_mask: int, continuous: bool = True, include_tid: bool = False
    ) -> bytes:
        """
        Build payload for MID = 0x10 (Read EPC Tag).
        Aligned with protocols.ts generateFastSwitchData.

        Payload Structure:
        - [4 bytes] Antenna mask (bitmask for antennas 0-31), big-endian
        - [1 byte]  M: Continuous flag (0x01 = continuous, 0x00 = single read)
        - [Optional] TID reading parameters if include_tid is True
        """
        if not antenna_mask:
            antenna_mask = 0x00000001
        if not (0 <= antenna_mask <= 0xFFFFFFFF):
            raise ValueError("Antenna mask must be a 32-bit unsigned integer")

        data = list(antenna_mask.to_bytes(4, byteorder="big"))
        data.append(0x01 if continuous else 0x00)  # M: Constant reading (Continuous)

        # Only add TID reading parameters if include_tid is true
        if include_tid:
            data.append(0x02)  # PID 0x02: TID reading parameters
            data.append(0x00)  # Byte 0: TID reading mode (0: self-adaptable)
            data.append(0x00)  # Byte 1: Word length (0x00 = Auto / Max)

        return bytes(data)

    @staticmethod
    def calculate_rssi(rssi_raw: int) -> int:
        """
        Calculate RSSI in dBm from raw byte value.
        Formula aligned with protocols.ts NATION protocol.
        """
        return -100 + round((rssi_raw * 70) / 255)

    @staticmethod
    def calculate_frequency(ch_idx: int) -> float:
        """
        Calculate frequency in MHz from channel index.
        Formula aligned with protocols.ts NATION protocol.
        """
        return 920.0 + ch_idx * 0.5

    def parse_epc(self, data: bytes) -> dict:
        """
        Parse EPC tag data with optional PIDs (RSSI, TID, phase, frequency).
        Aligned with protocols.ts NATION protocol parsing.
        """
        try:
            # 1. Read EPC (Mandatory)
            epc_len = int.from_bytes(data[0:2], "big")
            epc = data[2 : 2 + epc_len].hex().upper()

            # 2. Read PC (Mandatory - 2 bytes)
            pc = data[2 + epc_len : 2 + epc_len + 2].hex().upper()

            # 3. Read Antenna ID (Mandatory - 1 byte)
            antenna_id = data[2 + epc_len + 2]

            # 4. Parse optional PIDs starting after Antenna
            rssi = None
            tid = None
            phase = None
            frequency = None
            cursor = 2 + epc_len + 3

            while cursor < len(data):
                pid = data[cursor]
                cursor += 1

                if pid == 0x01:  # RSSI (1 byte)
                    if cursor < len(data):
                        rssi_raw = data[cursor]
                        # Convert to dBm using formula from protocols.ts
                        rssi = -100 + round((rssi_raw * 70) / 255)
                        cursor += 1
                    else:
                        break
                elif pid == 0x02:  # Reading Result (1 byte)
                    if cursor < len(data):
                        cursor += 1
                    else:
                        break
                elif pid == 0x03:  # TID DATA (Variable length)
                    if cursor + 1 < len(data):
                        tid_byte_len = int.from_bytes(data[cursor : cursor + 2], "big")
                        cursor += 2
                        if cursor + tid_byte_len <= len(data):
                            tid = data[cursor : cursor + tid_byte_len].hex().upper()
                            cursor += tid_byte_len
                        else:
                            break
                    else:
                        break
                elif pid == 0x04 or pid == 0x05:  # Tag data area or reserve area (Variable length)
                    if cursor + 1 < len(data):
                        byte_len = int.from_bytes(data[cursor : cursor + 2], "big")
                        cursor += 2 + byte_len
                    else:
                        break
                elif pid == 0x06:  # Sub-antenna No (1 byte)
                    cursor += 1
                elif pid == 0x07:  # UTC reading time (8 bytes)
                    cursor += 8
                elif pid == 0x08:  # Current frequency (4 bytes, KHz)
                    if cursor + 3 < len(data):
                        freq_khz = int.from_bytes(data[cursor : cursor + 4], "big")
                        frequency = freq_khz / 1000.0
                        cursor += 4
                    else:
                        break
                elif pid == 0x09:  # Current tag phase (1 byte, 0~128)
                    if cursor < len(data):
                        phase_raw = data[cursor]
                        phase = (phase_raw / 128.0) * 2 * 3.14159265359
                        cursor += 1
                    else:
                        break
                else:
                    # Unknown PID - skip and continue
                    continue

            result = {"epc": epc, "pc": pc, "antenna_id": antenna_id, "rssi": rssi}
            if tid:
                result["tid"] = tid
            if phase is not None:
                result["phase"] = phase
            if frequency is not None:
                result["frequency"] = frequency
            return result
        except Exception as e:
            return {"error": f"Parse error: {e}"}

    #
    def query_reader_power(self) -> dict[int, int]:
        """
        Queries the current transmit power settings for all antenna ports.

        :return: A dictionary where keys are antenna IDs (1-64)
                 and values are power levels in dBm, or an empty dict on failure.
        """
        try:
            self.stop_inventory()
            self.logger.debug(" Sending Query Reader Power command...")
            # Use the consistent MID value for querying RFID powers
            command_frame = self.build_frame(MID.QUERY_READER_POWER, payload=b"", rs485=self.rs485)
            self.uart.flush_input()
            self.uart.send(command_frame)

            time.sleep(0.1)  # Give reader time to respond
            raw = self.uart.receive(128)  # Receive enough bytes for multiple antenna responses

            if not raw:
                self.logger.debug(" No response received from reader.")
                return {}

            frame = self.parse_frame(raw)
            if not frame:
                self.logger.error(" Failed to parse response frame.")
                return {}

            mid = frame["mid"]
            data = frame["data"]

            self.logger.debug(f" Response MID: 0x{mid:02X}, Data: {data.hex()}")

            # Expected response MID is 0x02 (from QUERY_READER_POWER) [19]
            if mid == (MID.QUERY_READER_POWER & 0xFF):
                power_settings = {}
                offset = 0
                while offset + 2 <= len(data):  # Each power entry is 2 bytes (PID + Value)
                    ant_id = data[offset]  # PID is antenna ID [19]
                    power_dbm = data[offset + 1]  # Value is power in dBm [19]
                    power_settings[ant_id] = power_dbm
                    offset += 2
                return power_settings
            else:
                self.logger.error(" Unexpected response MID for power query.")
                return {}

        except Exception as e:
            self.logger.error(f" Exception during power query: {e}")
            return {}

    def configure_reader_power(
        self, antenna_powers: dict[int, int], persistence: Optional[bool] = None
    ) -> bool:
        """
        Configures the transmit power for specified antenna ports.
        :param antenna_powers: A dictionary where keys are antenna IDs (1-64)
                               and values are power levels in dBm (0-36).
        :param persistence: If True, settings are saved after power-down.
                            If False, settings are temporary. If None, uses reader default (typically save).
        :return: True if configuration was successful, False otherwise.
        """
        if not antenna_powers:
            self.logger.info(" No antenna powers provided for configuration.")
            return False
        if not isinstance(antenna_powers, dict):
            self.logger.info(f" antenna_powers must be a dictionary of {int: int}.")
            return False

        for ant_id, power_dbm in antenna_powers.items():
            if not isinstance(ant_id, int) or not isinstance(power_dbm, int):
                self.logger.error(
                    f" Invalid types: antenna ID and power must both be integers. Got ({type(ant_id)}, {type(power_dbm)})."
                )
                return False

        payload_parts = []
        for ant_id, power_dbm in antenna_powers.items():
            if not (1 <= ant_id <= 64):
                self.logger.error(f" Invalid antenna ID: {ant_id}. Must be between 1 and 64.")
                return False
            if not (0 <= power_dbm <= 33):  # Max power is 36dBm [18]
                self.logger.error(
                    f" Invalid power level for antenna {ant_id}: {power_dbm}dBm. Must be between 0 and 36dBm."
                )
                return False
            payload_parts.append(ant_id.to_bytes(1, "big"))  # PID for antenna [17]
            payload_parts.append(power_dbm.to_bytes(1, "big"))  # Value for power [17]

        if persistence is not None:
            payload_parts.append(b"\xff")  # PID for Parameter persistence [17]
            payload_parts.append(
                (0x01 if persistence else 0x00).to_bytes(1, "big")
            )  # Value for persistence [17]

        full_payload = b"".join(payload_parts)

        try:
            self.uart.flush_input()
            # self.logger.debug(f" Sending Configure Reader Power command with payload: {full_payload.hex()}")
            command_frame = self.build_frame(
                MID.CONFIGURE_READER_POWER, payload=full_payload, rs485=self.rs485
            )
            self.uart.send(command_frame)

            time.sleep(0.1)  # Give reader time to respond
            raw = self.uart.receive(64)

            if not raw:
                self.logger.debug(" No response received from reader.")
                return False

            frame = self.parse_frame(raw)
            if not frame:
                self.logger.error(" Failed to parse response frame.")
                return False

            mid = frame["mid"]
            data = frame["data"]

            self.logger.debug(f" Response MID: 0x{mid:02X}, Data: {data.hex()}")

            # Expected response MID is 0x01 (from CONFIGURE_READER_POWER) [5]
            # Data should contain configuration result (0x00 for success) [19]
            if mid == (MID.CONFIGURE_READER_POWER & 0xFF) and len(data) >= 1:
                result_code = data[0]
                if result_code == 0x00:
                    self.logger.info(" Reader power configured successfully.")
                    return True
                else:
                    result_code = data if len(data) >= 1 else -1
                    error_map = {
                        0x01: "the reader hardware does not support the port parameter",
                        0x02: "The reader does not support the power parameter",
                        0x03: "Save failed",
                    }
                    error_msg = error_map.get(result_code, "unknown error")
                    self.logger.error(
                        f" Failed to configure reader power. Result code: 0x{result_code:02X} ({error_msg})"
                    )
                    return False

        except Exception as e:
            self.logger.error(f" Exception during power configuration: {e}")
            return False

    # 19/06/2025
    ################################################################################
    #                            INVENTORY HEADER                                  #
    ################################################################################
    def is_inventory_running(self):
        return self._inventory_running

    # still work.
    def start_inventory_with_mode(self, antenna_mask, callback=None) -> bool:
        try:
            self.stop_inventory()

            self._inventory_running = True
            self._on_tag = callback
            self._on_inventory_end = None
            antenna_mask = self.build_antenna_mask(antenna_mask)
            self.logger.info(" Starting inventory with antenna mask:", antenna_mask)
            payload = self.build_epc_read_payload(antenna_mask, continuous=True)
            frame = self.build_frame(mid=MID.READ_EPC_TAG, payload=payload, rs485=self.rs485)

            self.send(frame)

            self._inventory_thread = threading.Thread(target=self._receive_inventory_loop_optimized)
            self._inventory_thread.start()
            return True
        except Exception as e:
            self.logger.error(f" Exception in start_inventory_with_mode: {e}")
            return False

    def _receive_inventory_loop_optimized(self):
        """
        Nhận dữ liệu inventory tối ưu: buffer, tách frame, không bỏ sót tag.
        """
        buffer = b""
        while self._inventory_running:
            try:
                raw = self.receive(128)  # Đọc nhiều bytes hơn/lần

                if not raw:
                    time.sleep(0.01)
                    continue
                buffer += raw
                frames = self.extract_valid_frames(buffer)
                # Xóa khỏi buffer các bytes đã xử lý thành frame
                if frames:
                    last_frame = frames[-1]
                    idx = buffer.find(last_frame) + len(last_frame)
                    buffer = buffer[idx:]

                for frame in frames:
                    try:
                        parsed = self.parse_frame(frame)
                        cat = parsed["category"]
                        mid = parsed["mid"]
                        if cat == 0x10 or mid == 0x00:  # EPC tag
                            tag = self.parse_epc(parsed["data"])
                            if "error" in tag:
                                # self.logger.error(f" Parse error: {tag['error']}")
                                continue
                            else:
                                if self._on_tag:
                                    self._on_tag(tag)
                        elif mid in MID.all_read_end_mids():
                            reason = parsed["data"][0] if parsed["data"] else None
                            self.logger.info(f" Inventory ended. Reason: {reason}")
                            if self._on_inventory_end:
                                self._on_inventory_end(reason)
                            self._inventory_running = False
                            return
                    except Exception:
                        # self.logger.error(f" Frame parse error: {e}")
                        continue
            except Exception as e:
                self.logger.error(f" Error in inventory loop: {e}")
                continue

    def set_filter_settings(self, repeated_time_ms: int = 0, rssi_threshold: int = 0):
        """
        Set Repeated Tag Filtering Time and RSSI Threshold on the reader.

        MID = 0x0209 (Configure tag uploading parameter)
        Payload Format:
            PID 0x01 (U16): repeated time (in 10ms units)
            PID 0x02 (U8) : RSSI threshold
        """

        MID_SET_FILTER = 0x0209

        try:
            #  Validate inputs
            if not (0 <= repeated_time_ms <= 655350):
                raise ValueError("Repeated time must be 0-655350 ms (0-65535 * 10ms)")
            if not (0 <= rssi_threshold <= 255):
                raise ValueError("RSSI threshold must be 0-255")

            #  Convert ms to 10ms units as required by protocol
            repeated_time_10ms_units = repeated_time_ms // 10

            #  Build payload with proper PIDs
            payload = (
                b"\x01"
                + repeated_time_10ms_units.to_bytes(2, byteorder="big")
                + b"\x02"
                + bytes([rssi_threshold])
            )

            #  Debug
            self.logger.debug(f" Sending payload: {payload.hex().upper()}")

            #  Build and send frame
            frame = self.build_frame(mid=MID_SET_FILTER, payload=payload)
            self.send(frame)

            #  Wait for response
            response = self.receive_response(mid=MID_SET_FILTER)

            #  Handle response
            status = response.get("status")
            if status == 0x00:
                self.logger.info(
                    f" Filter settings applied successfully: Time={repeated_time_ms} ms, RSSI={rssi_threshold}"
                )
            else:
                error_map = {0x01: " Parameter error", 0x02: " Save failed"}
                desc = error_map.get(status, " Unknown error")
                raise Exception(f"Failed with status 0x{status:02X}: {desc}")

        except Exception as e:
            self.logger.error(f" Error setting filter settings: {e}")

    def receive_response(self, mid: int, timeout: float = 1.0) -> dict:
        """
        Nhận frame phản hồi từ đầu đọc NRN tương ứng với MID yêu cầu.
        Trả về dict: {mid, status, data}
        """
        import time

        start_time = time.time()
        buffer = b""

        while time.time() - start_time < timeout:
            raw = self.receive(128)
            if not raw:
                time.sleep(0.01)
                continue

            buffer += raw
            frames = self.extract_valid_frames(buffer)

            for frame in frames:
                try:
                    parsed = self.parse_frame(frame)
                    if parsed["mid"] == mid:
                        status = parsed["data"][0] if len(parsed["data"]) >= 1 else None
                        data = parsed["data"][1:] if len(parsed["data"]) > 1 else b""
                        return {"mid": mid, "status": status, "data": data}
                except Exception as e:
                    self.logger.debug(f" Lỗi parse frame phản hồi: {e}")
                    continue

            # Nếu không có frame hợp lệ, tiếp tục chờ
            time.sleep(0.01)

        raise TimeoutError(f" Không nhận được phản hồi MID=0x{mid:02X} sau {timeout} giây")

    def _receive_inventory_loop(self):
        while self._inventory_running:
            try:
                raw = self.receive(128)
                if not raw:
                    continue

                frame = self.parse_frame(raw)
                mid = frame["mid"]

                if mid == 0x00:  # EPC tag
                    tag = self.parse_epc(frame["data"])
                    if "error" in tag:
                        self.logger.error(f" Parse error: {tag['error']}")
                    else:
                        # self.logger.info(f" Tag EPC: {tag['epc']}, RSSI: {tag['rssi']}, Ant: {tag['antenna_id']}")
                        if self._on_tag:
                            self._on_tag(tag)

                elif mid in MID.all_read_end_mids():  # MID=0x01/0x21/0x31
                    reason = frame["data"][0] if frame["data"] else None
                    self.logger.info(f" Inventory ended. Reason: {reason}")
                    if self._on_inventory_end:
                        self._on_inventory_end(reason)
                    self._inventory_running = False
                    break

            except Exception:
                # self.logger.error(f" Error in inventory loop: {e}")
                continue

    def stop_inventory(self) -> bool:
        """
        Sends the Stop command (MID=0xFF) to halt RFID operations and confirm idle state.
        Returns True if the reader acknowledges stop or issues a valid 'read end' notification.
        """

        # Step 1: Stop any running inventory thread if needed
        self._inventory_running = False
        if (
            hasattr(self, "_inventory_thread")
            and self._inventory_thread
            and self._inventory_thread.is_alive()
        ):
            self._inventory_thread.join(timeout=1)
            self.logger.info(" Inventory thread stopped.")

        # Step 2: Clear any unread UART data
        try:
            self.uart.flush_input()
        except Exception as e:
            self.logger.error(f" UART flush failed: {e}")

        # Step 3: Send STOP command frame
        stop_frame = self.build_frame(mid=MID.STOP_INVENTORY, payload=b"")
        print(stop_frame.hex())
        self.send(stop_frame)

        # Step 4: Wait for confirmation via response or notification
        for attempt in range(10):
            # time.sleep(0.2)
            try:
                raw = self.receive(256)
                frames = self.extract_valid_frames(raw)

                for idx, f in enumerate(frames):
                    try:
                        resp = self.parse_frame(f)
                        mid = resp["mid"]
                        data = resp["data"]

                        if mid == MID.STOP_OPERATION:  # MID=0xFF, response
                            result = data[0] if data else -1
                            if result == 0x00:
                                self.logger.info(" Reader responded: STOP successful, now IDLE.")
                                return True
                            else:
                                self.logger.error(
                                    f" Reader responded: STOP error code {result:#02x}"
                                )
                                return False

                        elif mid in NRNReader.all_read_end_mids():
                            reason = data[0] if data else -1
                            if reason == 1:
                                self.logger.info(" Read end notification: stopped by STOP command.")
                                return True
                            else:
                                self.logger.info(
                                    f" Read ended with reason code {reason}, not STOP command."
                                )

                        else:
                            self.logger.debug(f" Unrelated frame, MID={mid:#04x}")
                    except Exception as e:
                        self.logger.error(f" Frame parse error [{idx}]: {e}")
            except Exception as e:
                self.logger.error(f" UART receive error on attempt {attempt+1}: {e}")

        self.logger.error(
            " STOP failed: no valid response or reading end notification after 10 attempts."
        )
        return False

    @staticmethod
    def all_read_end_mids():
        return [0x01, 0x21, 0x31]

    ################################################################################
    #                           WRITE EPC TAG                                      #
    ################################################################################

    def write_epc_tag(
        self,
        epc_hex: str,
        antenna_id: int = 1,
        match_epc_hex: Optional[str] = None,
        access_password: Optional[int] = None,
        start_word: int = 2,
        timeout: float = 2.0,
    ) -> dict:
        """
        Write an EPC value to a tag.
        :param epc_hex: EPC value as hex string (e.g. 'ABCD9999')
        :param antenna_id: Antenna port (1-based)
        :param match_epc_hex: Optional EPC hex string to match before writing
        :param access_password: Optional access password (U32)
        :param start_word: Word address in EPC area (default 2)
        :param timeout: Timeout for response
        :return: dict with success, result_code, result_msg, failed_addr
        """
        try:
            # 1. Ensure reader is idle
            self.stop_inventory()
            self.uart.flush_input()
            time.sleep(0.2)

            # 2. Build payload
            payload = bytearray()
            # Antenna mask (U32, big-endian)
            antenna_mask = 1 << (antenna_id - 1)
            payload += antenna_mask.to_bytes(4, "big")
            # Data area (EPC = 0x01)
            payload += b"\x01"
            # Word starting address (U16, big-endian)
            payload += start_word.to_bytes(2, "big")
            # Data content: length (U16) + EPC bytes
            epc_bytes = bytes.fromhex(epc_hex)
            payload += len(epc_bytes).to_bytes(2, "big")
            payload += epc_bytes

            # Optional: match parameter (PID 0x01)
            if match_epc_hex:
                match_bytes = bytes.fromhex(match_epc_hex)
                # PID (0x01), length (U16), content
                # Content: [area=0x01][start_addr=0x0002][bit_len][data]
                match_area = 0x01
                match_start = start_word
                match_bitlen = len(match_bytes) * 8
                match_content = (
                    bytes([match_area])
                    + match_start.to_bytes(2, "big")
                    + bytes([match_bitlen])
                    + match_bytes
                )
                payload += b"\x01" + len(match_content).to_bytes(2, "big") + match_content

            # Optional: access password (PID 0x02)
            if access_password is not None:
                payload += b"\x02\x00\x04" + access_password.to_bytes(4, "big")

            # 3. Build frame
            frame = self.build_frame(mid=0x0211, payload=payload, rs485=self.rs485)
            self.logger.debug(f" Sending write frame: {frame.hex().upper()}")

            # 4. Send frame
            self.send(frame)

            # 5. Wait for response
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self.receive(256)
                if not raw:
                    continue
                try:
                    frames = self.extract_valid_frames(raw)
                except Exception as e:
                    self.logger.error(f" Frame extraction error: {e}")
                    continue
                for f in frames:
                    try:
                        resp = self.parse_frame(f)
                    except Exception as e:
                        self.logger.error(f" Frame parse error: {e}")
                        continue
                    self.logger.debug(
                        f" [WRITE-EPC-TAG] Received frame: MID={resp['mid']:02X}, Data={resp['data'].hex()}"
                    )
                    # Success response
                    if resp["mid"] == 0x11:
                        data = resp["data"]
                        result = data[0]
                        result_map = {
                            0x00: "Write successful",
                            0x01: "Antenna parameter error",
                            0x02: "Match parameter error",
                            0x03: "Write parameter error",
                            0x04: "CRC check error",
                            0x05: "Insufficient power",
                            0x06: "Data area overflow",
                            0x07: "Data area locked",
                            0x08: "Password error",
                            0x09: "Other tag error",
                            0x0A: "Tag lost",
                            0x0B: "Reader send error",
                        }
                        failed_addr = None
                        # Optional: failed word address (PID 0x01, U16)
                        if len(data) >= 4 and data[1] == 0x01 and data[2] == 0x02:
                            failed_addr = int.from_bytes(data[3:5], "big")
                        return {
                            "success": result == 0x00,
                            "result_code": result,
                            "result_msg": result_map.get(result, "Unknown error"),
                            "failed_addr": failed_addr,
                        }
                    # Error/illegal instruction
                    elif resp["mid"] == 0x00:
                        error_code = resp["data"][0] if resp["data"] else -1
                        error_map = {
                            0x01: "Unsupported instruction",
                            0x02: "CRC or mode error",
                            0x03: "Parameter error",
                            0x04: "Busy",
                            0x05: "Invalid state",
                        }
                        return {
                            "success": False,
                            "result_code": error_code,
                            "result_msg": f"Reader error: {error_map.get(error_code, f'Unknown error code 0x{error_code:02X}')}",
                            "failed_addr": None,
                        }
            # Timeout
            return {
                "success": False,
                "result_code": -2,
                "result_msg": "Timeout waiting for write response",
                "failed_addr": None,
            }
        except Exception as e:
            return {
                "success": False,
                "result_code": -99,
                "result_msg": f"Exception: {e}",
                "failed_addr": None,
            }

    def write_epc_tag_auto(
        self,
        new_epc_hex: str,
        match_epc_hex: Optional[str] = None,
        antenna_id: int = 1,
        access_password: Optional[int] = None,
        timeout: float = 0,
    ) -> dict:
        """
        Write new EPC to tag. Automatically calculates start_word and PC bits.
        :param new_epc_hex: New EPC hex string, e.g. '4321'
        :param match_epc_hex: EPC to match, optional
        :param antenna_id: Antenna number (1-based)
        :param access_password: Optional password
        :param timeout: Timeout in seconds
        """
        try:
            self.stop_inventory()
            self.uart.flush_input()
            time.sleep(0.2)

            # Step 1: Format EPC content
            epc_hex = new_epc_hex.strip().upper()
            word_len = (len(epc_hex) + 3) // 4  # Word count (4 hex chars = 2 bytes)
            pc_bits = word_len << 11  # PC word: upper 5 bits = word_len
            pc_hex = f"{pc_bits:04X}"
            full_epc_hex = pc_hex + epc_hex.ljust(word_len * 4, "0")
            epc_bytes = bytes.fromhex(full_epc_hex)

            # Step 2: Build payload
            payload = bytearray()
            antenna_mask = 1 << (antenna_id - 1)
            payload += antenna_mask.to_bytes(4, "big")  # Antenna mask
            payload += b"\x01"  # EPC area
            payload += (1).to_bytes(2, "big")  # start_word = 1
            payload += len(epc_bytes).to_bytes(2, "big")
            payload += epc_bytes

            # Step 3: Optional match EPC filter
            if match_epc_hex:
                match_hex = match_epc_hex.strip().upper()
                match_bytes = bytes.fromhex(match_hex)
                bit_len = len(match_bytes) * 8
                match_content = (
                    b"\x01"  # area = EPC
                    + (1).to_bytes(2, "big")  # start_word = 1
                    + bytes([bit_len])
                    + match_bytes
                )
                payload += b"\x01" + len(match_content).to_bytes(2, "big") + match_content

            # Step 4: Optional password
            if access_password is not None:
                payload += b"\x02\x00\x04" + access_password.to_bytes(4, "big")

            # Step 5: Build & send frame
            frame = self.build_frame(mid=0x0211, payload=payload, rs485=self.rs485)
            self.logger.debug(f" Write EPC frame: {frame.hex().upper()}")
            self.send(frame)

            # Step 6: Await response
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self.receive(256)
                if not raw:
                    continue
                try:
                    frames = self.extract_valid_frames(raw)
                except Exception as e:
                    self.logger.error(f" Frame extract error: {e}")
                    continue
                for f in frames:
                    try:
                        resp = self.parse_frame(f)
                    except Exception as e:
                        self.logger.error(f" Frame parse error: {e}")
                        continue
                    self.logger.debug(
                        f" Write-EPC response: MID={resp['mid']:02X}, Data={resp['data'].hex()}"
                    )
                    if resp["mid"] == 0x11:
                        result = resp["data"][0]
                        failed_addr = (
                            int.from_bytes(resp["data"][3:5], "big")
                            if len(resp["data"]) >= 5 and resp["data"][1] == 0x01
                            else None
                        )
                        result_map = {
                            0x00: "Write successful",
                            0x01: "Antenna error",
                            0x02: "Match error",
                            0x03: "Write parameter error",
                            0x04: "CRC error",
                            0x05: "Low power",
                            0x06: "Overflow",
                            0x07: "Locked",
                            0x08: "Password error",
                            0x09: "Tag error",
                            0x0A: "Tag lost",
                            0x0B: "Send error",
                        }
                        return {
                            "success": result == 0x00,
                            "result_code": result,
                            "result_msg": result_map.get(result, "Unknown error"),
                            "failed_addr": failed_addr,
                        }
                    elif resp["mid"] == 0x00:
                        error_code = resp["data"][0]
                        return {
                            "success": False,
                            "result_code": error_code,
                            "result_msg": f"Reader error: {error_code:02X}",
                            "failed_addr": None,
                        }
            return {
                "success": False,
                "result_code": -2,
                "result_msg": "Timeout waiting for write response",
                "failed_addr": None,
            }
        except Exception as e:
            return {
                "success": False,
                "result_code": -99,
                "result_msg": f"Exception: {e}",
                "failed_addr": None,
            }

    def check_write_epc(self, epcHex) -> bool:
        """
        Check if the reader supports writing EPC tags.
        Returns True if supported, False otherwise.
        """
        self.stop_inventory()
        self.uart.flush_input()

        # Ensure reader is idle
        def on_tag_callback(tag: dict):
            epc = tag.get("epc", "").upper()
            if epc == epcHex.upper():
                self.logger.info(f" Tag with EPC {epc} found during write check.")
                return True
            else:
                self.logger.info(f"Tag with EPC {epc} found, but not the one we are checking for.")
                return False

        self.start_inventory_with_mode(antenna_mask=[1], callback=on_tag_callback)
        return True

    def write_epc_to_target_auto(
        self,
        target_tag_epc: str,
        new_epc_hex: str,
        access_pwd: Optional[int] = None,
        timeout: float = 2.0,
        scan_timeout: float = 2.0,
        verify: bool = True,
        overwrite_pc: bool = True,
        prefix_words: int = 0,
    ):
        """
        Scan for a tag with EPC `target_tag_epc`, then write `new_epc_hex` to it,
        automatically handling PC bits, word length, and start_word.
        """
        self.logger.warning(
            f" Scanning for tag with EPC '{target_tag_epc}' (up to {scan_timeout}s) …"
        )
        found_event = threading.Event()

        def tag_callback(tag: dict):
            epc = tag.get("epc", "").upper()
            self.logger.info(f"Tag seen: {epc}")
            if epc == target_tag_epc.upper():
                print(
                    f"   Found target tag: EPC={epc}  "
                    f"(RSSI={tag.get('rssi')}, Antenna={tag.get('antenna_id')})"
                )
                found_event.set()

        self.start_inventory_with_mode(mode=0, callback=tag_callback)

        try:
            if not found_event.wait(timeout=scan_timeout):
                print(
                    f" Tag '{target_tag_epc}' not found within {scan_timeout}s. "
                    "Place the tag closer to the antenna and try again."
                )
                return

            # Validate EPCs
            try:
                self.validate_epc_hex(new_epc_hex)
                self.validate_epc_hex(target_tag_epc)
            except Exception as e:
                self.logger.error(f" EPC validation error: {e}")
                return

            # --- Auto-calculate PC bits and word length ---
            epc_hex = new_epc_hex.strip().upper()
            word_len = (len(epc_hex) + 3) // 4  # Word count (4 hex chars = 2 bytes)
            pc_bits = word_len << 11  # PC word: upper 5 bits = word_len
            pc_hex = f"{pc_bits:04X}"
            full_epc_hex = pc_hex + epc_hex.ljust(word_len * 4, "0")

            # --- Calculate start_word ---
            start_word = self.calculate_start_word(
                new_epc_hex, overwrite_pc=overwrite_pc, prefix_words=prefix_words
            )

            self.logger.debug(
                f" Writing new EPC '{new_epc_hex}' (full hex: {full_epc_hex}, start_word={start_word}) …"
            )
            result = self.write_epc_tag(
                epc_hex=full_epc_hex,
                antenna_id=1,
                match_epc_hex=target_tag_epc,
                access_password=access_pwd,
                start_word=start_word,
                timeout=timeout,
            )
            self.logger.info("Write EPC result:", result)

            # Optional verification
            if verify and result.get("success"):
                self.logger.info(" Verifying new EPC …")
                verified_event = threading.Event()

                def verify_cb(tag):
                    if tag.get("epc", "").upper() == new_epc_hex.upper():
                        verified_event.set()

                self.start_inventory_with_mode(mode=0, callback=verify_cb)
                if verified_event.wait(timeout=1.5):
                    self.logger.info(" Verification OK — tag now reports new EPC.")
                else:
                    self.logger.info(
                        "  Write may have succeeded, but tag not seen with new EPC yet."
                    )
                self.stop_inventory()
            elif not result.get("success"):
                self.logger.error("  Write failed; no verification performed.")

        finally:
            self.stop_inventory()

    @staticmethod
    def validate_epc_hex(epc_hex: str) -> bytes:
        """
        Validates and converts EPC hex string to bytes, ensuring:
        - Only valid hex characters
        - Even number of hex digits (1 byte = 2 hex chars)
        - Word-aligned (pad with 0x00 if needed)

        Returns:
            A word-aligned bytes object ready to be sent.
        """
        epc_hex = epc_hex.strip().replace(" ", "")

        if not all(c in "0123456789abcdefABCDEF" for c in epc_hex):
            raise ValueError("EPC hex contains non-hex characters.")

        if len(epc_hex) % 2 != 0:
            raise ValueError("EPC hex must contain an even number of characters.")

        data = bytes.fromhex(epc_hex)

        if len(data) % 2 != 0:
            data += b"\x00"  # Pad to next word (16-bit)

        return data

    @staticmethod
    def calculate_start_word(
        epc_hex: str, *, overwrite_pc: bool = False, prefix_words: int = 0
    ) -> int:
        """
        Calculates the start word for writing EPC data.

        Parameters:
            epc_hex        : str   - The EPC you want to write.
            overwrite_pc   : bool  - If True, includes PC word (start at word 1).
                                    Otherwise, starts at word 2 (normal safe zone).
            prefix_words   : int   - Extra prefix words to skip (e.g. locked data).

        Returns:
            int - the word address to use in the write command.
        """
        # Validate and ensure word-aligned input
        _ = NRNReader.validate_epc_hex(epc_hex)

        base_word = 1 if overwrite_pc else 2
        return base_word + prefix_words

    ################################################################################
    #                            ANT HEADER                                        #
    ################################################################################
    def query_enabled_ant_mask(self) -> int:
        """
        Query the enabled antenna mask.
        MID = 0x02, CAT = 0x01
        Returns an integer mask where each bit represents an enabled antenna.
        """
        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=0x0202, payload=b"", rs485=False)
            self.logger.debug(f" Sent frame: {frame.hex().upper()}")
            self.uart.send(frame)
            time.sleep(0.1)
            raw = self.uart.receive(64)
            if not raw:
                self.logger.info(" No response for enabled antenna mask query.")
                return 0

            frames = self.extract_valid_frames(raw)
            if not frames:
                self.logger.debug(" No valid frames extracted.")
                return 0

            for f in frames:
                parsed = self.parse_frame(f)
                if parsed["mid"] != 0x02:
                    self.logger.error(f" Unexpected MID in response: 0x{parsed['mid']:02X}")
                    continue

                data = parsed["data"]
                if len(data) < 2:
                    self.logger.error(" Invalid data length in response.")
                    continue

                mask = int.from_bytes(data[:2], byteorder="big")
                self.logger.info(f" Queried enabled antenna mask: {mask:#06X}")
                return mask

            self.logger.debug(" No MID=0x02 frame found.")
            return 0
        except Exception as e:
            self.logger.error(f" Exception in query_enabled_ant_mask: {e}")
            return 0

    def parse_reader_power_response(self, payload: bytes):
        """
        Phân tích phản hồi từ lệnh MID=0x02 (Query Reader Power)
        Payload là danh sách các cặp (Antenna ID, Power)
        """
        if len(payload) % 2 != 0:
            raise ValueError("Payload không hợp lệ, độ dài phải là bội số của 2")

        antenna_power_list = []

        for i in range(0, len(payload), 2):
            antenna_id = payload[i]
            power_dbm = payload[i + 1]
            antenna_power_list.append((antenna_id, power_dbm))

        return antenna_power_list

    def enable_ant(self, ant_id: int, save: bool = True) -> bool:
        """
        Enable a single antenna and optionally save the state.
        """
        if not (1 <= ant_id <= 32):
            self.logger.error(f" Invalid antenna ID: {ant_id}")
            return False
        try:
            # Query current mask
            mask = self.query_enabled_ant_mask()
            new_mask = mask | (1 << (ant_id - 1))
            # Build payload: [mask (4 bytes)] + [PID=0xFF, persist flag]
            payload = new_mask.to_bytes(4, "big")
            if save is not None:
                payload += b"\xff" + (b"\x01" if save else b"\x00")
            frame = self.build_frame(mid=0x0203, payload=payload)
            self.uart.flush_input()
            self.uart.send(frame)
            raw = self.uart.receive(64)
            parsed = self.parse_frame(raw)
            if parsed["mid"] == 0x03 and parsed["data"] and parsed["data"][0] == 0x00:
                self.logger.info(f" Enabled antenna {ant_id} (mask={new_mask:08X}, save={save})")
                return True
            else:
                self.logger.error(f" Failed to enable antenna {ant_id}")
                return False
        except Exception as e:
            self.logger.error(f" Exception in enable_ant: {e}")
            return False

    def disable_ant(self, ant_id: int, save: bool = True) -> bool:
        """
        Disable a single antenna and optionally save the state.
        """
        if not (1 <= ant_id <= 32):
            self.logger.error(f" Invalid antenna ID: {ant_id}")
            return False
        try:
            # Query current mask
            mask = self.query_enabled_ant_mask()
            new_mask = mask & ~(1 << (ant_id - 1))
            # Build payload: [mask (4 bytes)] + [PID=0xFF, persist flag]
            payload = new_mask.to_bytes(4, "big")
            if save is not None:
                payload += b"\xff" + (b"\x01" if save else b"\x00")
            frame = self.build_frame(mid=0x0203, payload=payload)
            self.uart.flush_input()
            self.uart.send(frame)
            raw = self.uart.receive(64)
            parsed = self.parse_frame(raw)
            if parsed["mid"] == 0x03 and parsed["data"] and parsed["data"][0] == 0x00:
                self.logger.info(f" Disabled antenna {ant_id} (mask={new_mask:08X}, save={save})")
                return True
            else:
                self.logger.error(f" Failed to disable antenna {ant_id}")
                return False
        except Exception as e:
            self.logger.error(f" Exception in disable_ant: {e}")
            return False

    def build_antenna_mask(self, antenna_ids: list[int]) -> int:
        """
        Chuyển danh sách số ăng-ten (1-based) thành giá trị mask 32-bit
        Ví dụ: [1,2,3,4] → 0x0000000F
        """
        mask = 0
        for aid in antenna_ids:
            if not (1 <= aid <= 32):
                raise ValueError(f"Antenna ID {aid} ngoài phạm vi hợp lệ (1-32)")
            mask |= 1 << (aid - 1)
        return mask

    ################################################################################
    #                            PROFILE HEADER                                    #
    ################################################################################

    def select_profile(self, profile_id: int) -> bool:
        """
        Selects a baseband profile by ID (0, 1, or 2).
        Returns True if successful, False otherwise.
        """

        if profile_id not in (0, 1, 2):
            self.logger.error(f" Invalid profile ID: {profile_id}")
            return False

        try:
            self.uart.flush_input()
            payload = bytes([profile_id])
            frame = self.build_frame(mid=0x020A, payload=payload, rs485=self.rs485)
            self.uart.send(frame)

            time.sleep(0.1)
            raw = self.uart.receive(64)
            if not raw:
                self.logger.info(" No response from reader.")
                return False

            parsed = self.parse_frame(raw)
            if parsed["mid"] != 0x0A:
                self.logger.error(f" Unexpected MID in response: 0x{parsed['mid']:02X}")
                return False

            data = parsed["data"]
            if len(data) < 1 or data[0] != profile_id:
                self.logger.error(
                    f" Profile selection failed. Expected ID {profile_id}, got {data[0] if data else 'N/A'}"
                )
                return False

            self.logger.info(f" Profile {profile_id} selected successfully.")
            return True

        except Exception as e:
            self.logger.error(f" Exception during profile selection: {e}")
            return False

    def get_profile(self) -> dict:
        profile = {}

        try:
            # Enabled antennas
            enabled_mask = self.query_enabled_ant_mask()
            profile["enabled_antennas"] = [i for i in range(1, 65) if (enabled_mask >> (i - 1)) & 1]

            # Antenna powers
            powers = self.query_reader_power()
            profile["antenna_powers"] = powers

            # Baseband parameters
            baseband = self.query_baseband_profile()
            profile["baseband"] = {
                "speed": baseband.get("speed"),  # e.g. Tari/Modulation
                "q_value": baseband.get("q_value"),
                "session": baseband.get("session"),
                "inventory_flag": baseband.get("inventory_flag"),
            }

            # Frequency band
            freq_band = self.query_rf_band()
            profile["rf_band"] = freq_band  # e.g., FCC, ETSI, JP, etc.

            # Working frequency channels
            working_freq = self.query_working_frequency()
            profile["working_frequency"] = working_freq  # e.g., auto or list of channels

            # Tag filtering
            filtering = self.query_filter_settings()
            profile["filtering"] = {
                "repeat_time_ms": filtering.get("repeat_time", 0) * 10,
                "rssi_threshold": filtering.get("rssi_threshold"),
            }

            # # Optional: Reader info
            info = self.Query_Reader_Information()
            profile["reader_info"] = info

            return profile

        except Exception as e:
            self.logger.error(f" Failed to get profile: {e}")
            return {"error": str(e)}

    # Inside your NRNReader class
    def query_rf_band(self):
        RF_BAND_CODES = {
            0: "CN 920-925 MHz",
            1: "CN 840-845 MHz",
            2: "CN Dual-band 840-845 + 920-925 MHz",
            3: "FCC 902-928 MHz",
            4: "ETSI 866-868 MHz",
            5: "JP 916.8-920.4 MHz",
            6: "TW 922.25-927.75 MHz",
            7: "ID 923.125-925.125 MHz",
            8: "RUS 866.6-867.4 MHz",
        }
        CAT_RF_BAND = 0x02
        MID_RF_BAND = 0x04

        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=(CAT_RF_BAND << 8) | MID_RF_BAND, payload=b"")
            self.logger.debug(f" Sending frame: {frame.hex().upper()}")
            self.send(frame)
            raw = self.receive(64)
            parsed = self.parse_frame(raw)
            self.logger.debug(
                f" Parsed frame: CAT=0x{parsed['category']:02X}, MID=0x{parsed['mid']:02X}, Data={parsed['data'].hex().upper()}"
            )

            if parsed["category"] != CAT_RF_BAND or parsed["mid"] != MID_RF_BAND:
                raise ValueError(
                    f" Unexpected response: CAT=0x{parsed['category']:02X}, MID=0x{parsed['mid']:02X}"
                )

            data = parsed.get("data", b"")
            if len(data) < 1:
                raise ValueError(" Invalid response length for RF band query")

            band_code = data[0]
            band_name = RF_BAND_CODES.get(band_code, f"Unknown ({band_code})")
            self.logger.info(f" Current RF Band: {band_name} [Code={band_code}]")
            return {"band_code": band_code, "band_name": band_name}
        except Exception as e:
            self.logger.error(f" Error querying RF band: {e}")
            return None

    def set_rf_band(self, band_code: int, persist: bool = True):
        """
        Set the RF frequency band of the reader (MID = 0x03, CAT = 0x02).
        - band_code: 0-8 (see protocol)
        - persist: True to save after power-down, False for temporary
        Returns True if successful, False otherwise.
        """
        RF_BAND_CODES = {
            0: "CN 920-925 MHz",
            1: "CN 840-845 MHz",
            2: "CN Dual-band 840-845 + 920-925 MHz",
            3: "FCC 902-928 MHz",
            4: "ETSI 866-868 MHz",
            5: "JP 916.8-920.4 MHz",
            6: "TW 922.25-927.75 MHz",
            7: "ID 923.125-925.125 MHz",
            8: "RUS 866.6-867.4 MHz",
        }
        CAT_SET_RF_BAND = 0x02
        MID_SET_RF_BAND = 0x03

        try:
            if not (0 <= band_code <= 8):
                raise ValueError("Invalid band_code. Must be between 0-8.")

            self.stop_inventory()
            self.is_idle()
            self.uart.flush_input()
            time.sleep(0.5)

            # Build payload and frame
            payload = bytes([band_code])
            frame = self.build_frame(mid=(CAT_SET_RF_BAND << 8) | MID_SET_RF_BAND, payload=payload)
            self.logger.info(
                f" Setting RF Band: {RF_BAND_CODES.get(band_code, 'Unknown')} [Persist={'Yes' if persist else 'No'}]"
            )
            self.logger.debug(f" Sending frame: {frame.hex().upper()}")
            self.send(frame)

            # Wait and parse response
            timeout = 1.0
            start_time = time.time()

            while time.time() - start_time < timeout:
                raw = self.receive(64)
                if not raw:
                    time.sleep(0.01)
                    continue

                self.logger.debug(f" Received raw data: {raw.hex().upper()}")

                try:
                    parsed = self.parse_frame(raw)
                    self.logger.debug(
                        f" Parsed frame: CAT=0x{parsed['category']:02X}, MID=0x{parsed['mid']:02X}, Data={parsed['data'].hex().upper()}"
                    )
                    data = parsed.get("data", b"")
                except Exception as e:
                    self.logger.error(f" Frame parse error: {e}")
                    continue

                # Check both category and mid
                if parsed["category"] != CAT_SET_RF_BAND or parsed["mid"] != MID_SET_RF_BAND:
                    self.logger.debug(
                        f" Ignored frame with CAT=0x{parsed['category']:02X}, MID=0x{parsed['mid']:02X} (expecting CAT=0x02, MID=0x03)"
                    )
                    continue
                self.logger.debug(f" RF Band set response: {data.hex().upper()}")

                if len(data) < 1:
                    raise ValueError(" Invalid response length for set RF band")

                status = data[0]
                if status == 0x00:
                    self.logger.info(
                        f" RF Band set to {RF_BAND_CODES[band_code]} [Persist={'Yes' if persist else 'No'}]"
                    )
                    return True
                else:
                    error_map = {0x01: "Unsupported frequency by hardware", 0x02: "Save failed"}
                    reason = error_map.get(status, "Unknown error")
                    self.logger.error(f" Failed to set RF band (status=0x{status:02X}): {reason}")
                    return False

            self.logger.warning(" No valid response for set_rf_band within timeout.")
            return False

        except Exception as e:
            self.logger.error(f" Error setting RF band: {e}")
            return False

    def query_working_frequency(self) -> dict:
        """
        Queries the reader's working frequency configuration.
        MID = 0x0206
        Returns:
            {
                'mode': 'auto' | 'manual',
                'channels': list of channel numbers (if manual)
            }
        """
        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=0x0206, payload=b"", rs485=self.rs485)
            self.uart.send(frame)

            time.sleep(0.1)
            raw = self.uart.receive(64)
            if not raw:
                raise Exception("No response for working frequency query")

            parsed = self.parse_frame(raw)
            if parsed["mid"] != 0x06:
                raise Exception("Unexpected MID")

            data = parsed["data"]
            if not data:
                return {"mode": "unknown", "channels": []}

            if data[0] == 0x00:
                return {"mode": "auto", "channels": []}
            elif data[0] == 0x01:
                channels = list(data[1:])
                return {"mode": "manual", "channels": channels}
            else:
                return {"mode": f"unknown (0x{data[0]:02X})", "channels": []}

        except Exception as e:
            self.logger.error(f" Error in query_working_frequency: {e}")
            return {"mode": "error", "channels": []}

    def query_filter_settings(self) -> dict:
        """
        Queries tag filtering settings:
        - Repeat tag suppression time (in 10ms units)
        - RSSI threshold
        MID = 0x020A
        """
        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=0x020A, payload=b"", rs485=self.rs485)
            self.uart.send(frame)

            time.sleep(0.1)
            raw = self.uart.receive(64)
            if not raw:
                raise Exception("No response for filter settings query")

            parsed = self.parse_frame(raw)
            if parsed["mid"] != 0x0A:
                raise Exception("Unexpected MID")

            data = parsed["data"]
            if len(data) < 2:
                raise Exception("Insufficient data")

            repeat_time = int.from_bytes(data[0:2], "big")  # units of 10ms
            rssi_threshold = data[2] if len(data) >= 3 else None

            return {"repeat_time": repeat_time, "rssi_threshold": rssi_threshold}

        except Exception as e:
            self.logger.error(f" Error in query_filter_settings: {e}")
            return {"repeat_time": 0, "rssi_threshold": None}

    ################################################################################
    #                            BEEPER HEADER                                     #
    ################################################################################

    def get_beeper(self) -> bool:
        """
        Return True if beeping is allowed under current mode:
        - Mode 1: Continuous beep
        - Mode 2: Beep on new tag
        - Mode 0: No beep
        """
        return getattr(self, "beeper_mode", 0) in (1, 2)

    def set_beeper(self, mode: int) -> bool:
        """
        Set the beeper mode:
        0 = No Beep → stop buzzer now
        1 = Continuous Beep → start buzzing immediately
        2 = Beep on New Tag → do not buzz now, only on new tag events

        Returns True if the internal mode was set and hardware command (if any) succeeded.
        """
        if mode not in (0, 1, 2):
            raise ValueError("Invalid mode: must be 0 (No Beep), 1 (Continuous), or 2 (New Tag)")

        success = True

        if mode == 0:
            success = self._send_beeper_command(ring=0, duration=0)  # Stop beep
        elif mode == 1:
            success = self._send_beeper_command(ring=1, duration=1)  # Continuous beep
        elif mode == 2:
            # Do not buzz now; just set mode, used later during tag detection
            pass

        if success or mode == 2:
            self.beeper_mode = (
                mode  # Only set if command succeeded, or mode is 2 (no hardware call)
            )
        else:
            self.logger.error(f" Failed to apply buzzer command for mode {mode}")

        return success

    def _send_beeper_command(self, ring: int, duration: int) -> bool:
        """
        Controls the buzzer directly.
        Sends MID=0x1F to make the buzzer ring or stop.

        :param ring: 0 = stop, 1 = ring
        :param duration: 0 = ring once, 1 = keep ringing
        :return: True if the command succeeded, False otherwise.
        """
        if ring not in (0, 1) or duration not in (0, 1):
            raise ValueError("Invalid parameters: ring and duration must be 0 or 1")

        try:
            self.uart.flush_input()  # Clear any previous data in the UART buffer
            # Build the payload for the buzzer control command
            payload = bytes([ring, duration])
            # self.logger.debug(f" Buzzer control payload: {payload.hex()}")

            # Build the communication frame with the correct category and MID
            mid = (0x01 << 8) | 0x1F  # Category 0x01, MID 0x1F
            frame = self.build_frame(mid=mid, payload=payload, rs485=self.rs485)
            # self.logger.debug(f" Sending buzzer control frame: {frame.hex()}")

            # Send the frame
            self.send(frame)

            # Receive and parse the response
            raw_response = self.receive(64)
            # self.logger.debug(f" Received raw response: {raw_response.hex()}")

            # Validate the response frame
            if len(raw_response) < 9:
                raise ValueError("Frame too short")
            if raw_response[0] != FRAME_HEADER:
                raise ValueError("Invalid frame header")

            # Parse the response
            response = self.parse_frame(raw_response)

            # Check the response MID and result
            if response["mid"] == (mid & 0xFF):  # Compare only the MID part
                result = response["data"][0] if len(response["data"]) > 0 else -1
                if result == 0x00:
                    self.logger.info(" Buzzer control succeeded.")
                    return True
                else:
                    self.logger.error(f" Buzzer control failed. Result code: 0x{result:02X}")
                    return False
            elif response["mid"] == 0x00:  # Handle illegal instruction response
                response["data"][0] if len(response["data"]) > 0 else -1

                # self.logger.error(f" Illegal instruction response. Error code: 0x{error_code:02X}")
                return False
            else:
                # self.logger.error(f" Unexpected MID in response: 0x{response['mid']:02X}")
                return False

        except Exception:
            # self.logger.error(f" Exception in control_buzzer: {e}")
            return False

    #################################################################################
    #                            SESSION HEADER                                     #
    #################################################################################

    def get_session(self) -> Optional[int]:
        try:
            self.uart.flush_input()
            frame = self.build_frame(mid=MID.QUERY_BASEBAND, payload=b"", rs485=False)
            self.uart.send(frame)

            raw = self.uart.receive(64)
            if not raw:
                self.logger.info(" No response for session query.")
                return None

            # self.logger.debug(f" Raw response: {raw.hex()}")
            frames = self.extract_valid_frames(raw)
            frame_data = frames[0]  # first valid frame

            response = self.parse_frame(frame_data)

            data = response["data"]
            if len(data) < 4:
                self.logger.info(" Response too short for session info.")
                return None

            session = data[2]
            if session in (0, 1, 2, 3):
                self.logger.info(f" Current session: {session}")
                return session
            else:
                self.logger.error(f" Invalid session value: {session}")
                return None

        except Exception as e:
            self.logger.error(f" Exception in get_session: {e}")
            return None

    def is_idle(self, retry: int = 3, delay: float = 0.3, settle_delay: float = 0.5) -> bool:
        """
        Checks if the reader is in the Idle state by sending STOP.
        After confirming idle, waits an additional settle_delay to ensure hardware is ready.
        """
        for attempt in range(retry):
            try:
                self.uart.flush_input()
                stop_frame = self.build_frame(mid=MID.STOP_INVENTORY, payload=b"", rs485=self.rs485)
                self.uart.send(stop_frame)

                raw_response = self.uart.receive(64)
                if not raw_response:
                    self.logger.debug(
                        f" Attempt {attempt+1}/{retry}: No response received for Idle check."
                    )
                    time.sleep(delay)
                    continue

                response = self.parse_frame(raw_response)
                if response["mid"] == (MID.STOP_INVENTORY & 0xFF) and response["data"][0] == 0x00:
                    self.logger.info(" Reader responded with STOP success.")
                    self.logger.warning(f" Waiting {settle_delay}s for hardware to fully settle...")
                    time.sleep(settle_delay)
                    return True
                else:
                    self.logger.info(
                        f" Attempt {attempt+1}/{retry}: Not idle yet. Response: {response}"
                    )
                    time.sleep(delay)

            except Exception as e:
                self.logger.error(f" Exception in is_idle (attempt {attempt+1}): {e}")
                time.sleep(delay)

        self.logger.info(" Reader did not enter Idle state after retries.")
        return False

    def configure_baseband(
        self, speed: int, q_value: int, session: int, inventory_flag: int
    ) -> bool:
        """
        Configure the EPC baseband parameters using TLV encoding.
        Uses MID = 0x0B, Category = 0x01 per protocol spec.
        """

        # --- Step 1: Validate Inputs ---
        if speed not in (0, 1, 2, 3, 4, 255):
            self.logger.error(f" Invalid speed: {speed}")
            return False
        if not (0 <= q_value <= 15):
            self.logger.error(f" Invalid Q: {q_value}")
            return False
        if session not in (0, 1, 2, 3):
            self.logger.error(f" Invalid session: {session}")
            return False
        if inventory_flag not in (0, 1, 2):
            self.logger.error(f" Invalid flag: {inventory_flag}")
            return False

        try:
            # --- Step 2: Ensure Reader Idle ---
            self.stop_inventory()
            if not self.is_idle():
                self.logger.info(" Reader not idle")
                return False
            time.sleep(0.1)
            self.uart.flush_input()

            # --- Step 3: Encode TLV Payload ---
            payload = bytes([0x01, speed, 0x02, q_value, 0x03, session, 0x04, inventory_flag])

            # --- Step 4: Build Frame with Correct MID Format ---
            mid_value = MID.CONFIG_BASEBAND
            frame = self.build_frame(mid=mid_value, payload=payload, rs485=False, notify=False)
            self.logger.debug(f" sent Frame: {frame}")

            # --- Step 5: Send Frame ---
            self.uart.send(frame)

            # --- Step 6: Wait + Parse Response ---
            raw = self.uart.receive(64)
            self.logger.debug(f" Raw: {raw.hex()}")
            frames = self.extract_valid_frames(raw)
            if not frames:
                self.logger.info(" No valid response")
                return False

            for f in frames:
                resp = self.parse_frame(f)
                if resp["mid"] == 0x0B and resp["category"] in (0x01, 0x02):
                    code = resp["data"][0]
                    if code == 0x00:
                        self.logger.info(" CONFIG_BASEBAND OK")
                        return True
                    else:
                        errors = {
                            0x01: "Unsupported baseband",
                            0x02: "Q param error",
                            0x03: "Session error",
                            0x04: "Flag error",
                            0x05: "Other param error",
                            0x06: "Save failed",
                        }
                        self.logger.error(f" Error: {errors.get(code, f'Code 0x{code:02X}')}")
                        return False

                elif resp["mid"] == 0x00:  # Generic error
                    err = resp["data"][0] if resp["data"] else None
                    generic = {
                        0x01: "Unsupported instruction",
                        0x02: "CRC or mode error",
                        0x03: "Param error",
                        0x04: "Busy",
                        0x05: "Invalid state",
                    }
                    self.logger.error(f" Generic error: {generic.get(err, f'0x{err:02X}')}")
                    return False

            self.logger.info(" No valid CONFIG_BASEBAND reply")
            return False

        except Exception as e:
            self.logger.error(f" Exception: {e}")
            return False

    def query_baseband_profile(self) -> dict:
        """
        Query the current EPC baseband parameters.
        Returns a dict: {speed, q_value, session, inventory_flag}
        """
        try:
            self.stop_inventory()  # Ensure reader is idle before querying
            self.uart.flush_input()
            # MID = 0x0C in category 0x01 (see MID.QUERY_BASEBAND)
            frame = self.build_frame(mid=MID.QUERY_BASEBAND, payload=b"", rs485=False)
            self.uart.send(frame)

            raw = self.uart.receive(64)
            if not raw:
                self.logger.info(" No response for baseband profile query.")
                return {}

            frames = self.extract_valid_frames(raw)

            if not frames:
                self.logger.debug(" No valid frames extracted.")
                return {}

            frame_data = frames[0]  # first valid frame

            response = self.parse_frame(frame_data)
            # Data: [speed, q_value, session, inventory_flag]
            data = response["data"]

            if len(data) < 4:
                self.logger.error(" Invalid baseband profile length.")
                return {}

            return {
                "speed": data[0],
                "q_value": data[1],
                "session": data[2],
                "inventory_flag": data[3],
            }

        except Exception as e:
            self.logger.error(f" Exception in query_baseband_profile: {e}")
            return {}


# === SDK Documentation and Examples ===

"""
Nextwaves RFID SDK for Python
=============================

This SDK provides a comprehensive interface for communicating with NRN RFID readers.
It supports all major RFID operations including inventory, tag reading/writing,
antenna control, and reader configuration.

Basic Usage:
-----------

```python
from sdk.nation.nrn import NRNReader, setup_logging
import logging

# Setup logging (optional)
logger = setup_logging(level=logging.INFO)

# Initialize reader
reader = NRNReader(port="/dev/ttyUSB0", baudrate=115200)

# Connect to reader
reader.open()

# Get reader information
info = reader.Query_Reader_Information()
print(f"Reader Serial: {info.get('serial_number')}")

# Start inventory
def on_tag(tag):
    print(f"Tag found: EPC={tag['epc']}, RSSI={tag['rssi']}")

reader.start_inventory_with_mode(antenna_mask=[1], callback=on_tag)

# Stop inventory
reader.stop_inventory()

# Close connection
reader.close()
```

Advanced Features:
-----------------

1. **Logging Configuration**:
   ```python
   # Set custom log level
   reader.set_log_level(logging.DEBUG)
   ```

2. **Error Handling**:
   ```python
   from sdk.nation.nation import ConnectionError, ProtocolError

   try:
       reader.open()
   except ConnectionError as e:
       print(f"Connection failed: {e}")
   ```

3. **Reader Configuration**:
   ```python
   # Configure power settings
   power_settings = {1: 30, 2: 25}  # Antenna 1: 30dBm, Antenna 2: 25dBm
   reader.configure_reader_power(power_settings)

   # Set RF band
   reader.set_rf_band(band_code=3)  # FCC 902-928 MHz
   ```

4. **Tag Operations**:
   ```python
   # Write EPC to tag
   result = reader.write_epc_tag_auto(
       target_tag_epc="12345678",
       new_epc_hex="87654321"
   )

   if result['success']:
       print("EPC written successfully")
   else:
       print(f"Write failed: {result['result_msg']}")
   ```

SDK Information:
---------------

- **Name**: Nextwaves RFID SDK
- **Version**: 1.0.0
- **Supported Readers**: NRN RFID Readers
- **Protocol**: UART-based communication
- **Python Version**: 3.6+

For more information and examples, visit the Nextwaves documentation.
"""


# === SDK Entry Point ===
def create_reader(
    port: str, baudrate: int = 115200, timeout: float = 0.5, log_level: int = logging.INFO
) -> NRNReader:
    """
    Factory function to create a configured NRNReader instance.

    This is the recommended way to create a reader instance for SDK usage.

    Args:
        port: Serial port path (e.g. COM3, /dev/ttyUSB0)
        baudrate: Baud rate for communication (default: 115200)
        timeout: Communication timeout in seconds (default: 0.5)
        log_level: Logging level (default: logging.INFO)

    Returns:
        Configured NRNReader instance

    Example:
        ```python
        from sdk.nation.nrn import create_reader
        import logging

        reader = create_reader("/dev/ttyUSB0", log_level=logging.DEBUG)
        reader.open()
        # ... use reader
        reader.close()
        ```
    """
    logger = setup_logging(level=log_level)
    return NRNReader(port, baudrate, timeout, logger)
