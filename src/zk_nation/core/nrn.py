"""NRNReader API adapter for Nextwaves nrn.py (ef969175), using physical ZK serial.

Independent implementation: no Nation packets, UART bridge or automatic recovery.
See docs/NRN_COMPATIBILITY.md for intentional differences and unsupported APIs.
"""
from copy import deepcopy
from functools import wraps
import logging
import math
import threading
import time

from . import errors as zk_errors
from .integration import epc_selector, error_details
from .nation import NationReader, REGIONS
from .nation_errors import nation_error_context
from .protocol import integer
from .reader import hex_bytes


class NextwavesSDKError(Exception):
    """Adapter error; cause/details preserve the original ZK/Python failure."""
    def __init__(self, cause):
        super().__init__(str(cause))
        self.cause = cause
        self.details = error_details(cause)
        self.nation = getattr(cause, "nation", None)
        self.nation_code = getattr(cause, "nation_code", None)
        self.nation_scope = getattr(cause, "nation_scope", None)


class ConnectionError(NextwavesSDKError):
    pass


class ProtocolError(NextwavesSDKError):
    pass


class ConfigurationError(NextwavesSDKError):
    pass


class TagOperationError(NextwavesSDKError):
    pass


def setup_logging(level=logging.INFO, logger_name="nation_zk.nrn"):
    log = logging.getLogger(logger_name)
    log.setLevel(level)
    return log


def _api(fallback, error_type=ConfigurationError):
    """Keep NRN return conventions; record errors without doing recovery."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            try:
                with self._lock:
                    self.last_error = None
                    return method(self, *args, **kwargs)
            except (zk_errors.ReaderError, ValueError, TypeError, OSError) as cause:
                error = self._record(cause, error_type)
                if self.raise_errors:
                    raise error from cause
                return deepcopy(fallback)
        return wrapped
    return decorate


RF_NAMES = {0: "CN 920-925 MHz", 1: "CN 840-845 MHz", 3: "FCC 902-928 MHz",
            4: "ETSI 866-868 MHz", 5: "JP 916.8-920.4 MHz"}
WRITE_MESSAGES = {0: "Write successful", 1: "Antenna parameter error",
                  2: "Match parameter error", 3: "Write parameter error",
                  4: "CRC check error", 5: "Insufficient power", 6: "Data area overflow",
                  7: "Data area locked", 8: "Password error", 9: "Other tag error",
                  10: "Tag lost", 11: "Reader send error"}


class NRNReader:
    DEFAULT_PORT = None
    DEFAULT_BAUDRATE = 115200
    DEFAULT_TIMEOUT = 0.5

    def __init__(self, port, baudrate, timeout=None, logger=None, *,
                 antennas="auto", allow_approximate=False, raise_errors=False,
                 simulated=False):
        self.port = port or self.DEFAULT_PORT
        self.baudrate = baudrate or self.DEFAULT_BAUDRATE
        self.timeout = self.DEFAULT_TIMEOUT if timeout is None else timeout
        self._validate_timeout(self.timeout)
        if antennas != "auto" and (type(antennas) is not int or antennas not in (1, 4)):
            raise ValueError("antennas must be auto, 1 or 4")
        for option in (allow_approximate, raise_errors, simulated):
            if type(option) is not bool:
                raise ValueError("adapter options must be boolean")
        self.logger = logger or logging.getLogger("nation_zk.nrn")
        self.antennas = antennas
        self.allow_approximate = allow_approximate
        self.raise_errors = raise_errors
        self.simulated = simulated
        self.rs485 = False
        self.antenna_mask = 1
        self.last_error = None
        self.last_inventory_error = None
        self._reader = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._target = 0
        self._repeat_ms = 0
        self._closing = False

    @staticmethod
    def _validate_timeout(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 30:
            raise ValueError("timeout must be finite and in (0, 30]")

    @classmethod
    def set_uart_defaults(cls, port, baudrate, timeout=0.5):
        cls._validate_timeout(timeout)
        cls.DEFAULT_PORT, cls.DEFAULT_BAUDRATE, cls.DEFAULT_TIMEOUT = port, baudrate, timeout

    @classmethod
    def get_sdk_info(cls):
        return {"name": "Nation ZK NRN adapter", "version": "0.1.0a2",
                "description": "ZK serial adapter for Nextwaves NRNReader API"}

    def get_reader_info(self):
        return {"port": self.port, "baudrate": self.baudrate, "timeout": self.timeout,
                "rs485": self.rs485, "antenna_mask": hex(self.antenna_mask)}

    def set_log_level(self, level):
        self.logger.setLevel(level)

    def _record(self, cause, default=ConfigurationError):
        category = (ConnectionError if isinstance(cause, zk_errors.TransportError) else
                    ProtocolError if isinstance(cause, zk_errors.ProtocolError) else default)
        error = category(cause)
        self.last_error = error
        self.logger.debug("NRN adapter failure: %s", error)
        return error

    def _idle(self):
        if self._closing:
            raise zk_errors.SessionStateError("Reader is closing")
        if self._reader is None:
            raise zk_errors.TransportError("Reader is not open")
        if self.rs485:
            raise zk_errors.UnsupportedError("RS485 addressing is not implemented by this adapter")
        if self.is_inventory_running():
            raise zk_errors.SessionStateError("Stop inventory explicitly before issuing another operation")
        return self._reader

    def open(self):
        with self._lock:
            if self._reader is not None or self._closing:
                raise ConnectionError(zk_errors.SessionStateError("Reader already open"))
            self.last_error = None
            try:
                self._reader = (NationReader.simulate(1 if self.antennas == "auto" else self.antennas)
                                if self.simulated else NationReader.open(
                                    self.port, self.baudrate, self.antennas, timeout=self.timeout))
                self._reader.transport.timeout = self.timeout
            except (zk_errors.ReaderError, ValueError, OSError) as cause:
                raise self._record(cause, ConnectionError) from cause

    def close(self):
        if self._thread is threading.current_thread():
            raise ConfigurationError(zk_errors.SessionStateError("Close from the application thread, not callback"))
        with self._lock:
            self._closing = True
        try:
            self.stop_inventory()
            with self._lock:
                if self._reader is not None:
                    self._reader.close()
                    self._reader = None
        finally:
            with self._lock:
                self._closing = False

    @_api(False)
    def Connect_Reader_And_Initialize(self):
        self._idle().get_reader_info()  # Probe only, never silently open/reset/retry.
        return True

    @_api({})
    def Query_Reader_Information(self):
        info = self._idle().get_reader_info()
        return {"serial_number": None, "power_on_time_sec": None,
                "baseband_compile_time": None, "app_version": info["firmware"],
                "os_version": None, "app_compile_time": None, "zk": info}

    @_api({})
    def query_rfid_ability(self):
        reader = self._idle()
        reader.get_reader_info()
        return {"min_power_dbm": 0, "max_power_dbm": 30, "antenna_count": reader.antennas,
                "frequencies": list(RF_NAMES), "rfid_protocols": [0],
                "capability_source": "adapter-supported subset (6C only)"}

    @_api({})
    def query_reader_power(self):
        return dict(enumerate(self._idle().get_power()["powers_dbm"], 1))

    @_api(False)
    def configure_reader_power(self, antenna_powers, persistence=None):
        reader = self._idle()
        with nation_error_context("set_power"):
            if not isinstance(antenna_powers, dict) or not antenna_powers:
                raise ValueError("antenna_powers must be a nonempty dict")
            if persistence is not None and type(persistence) is not bool:
                raise ValueError("persistence must be bool or None")
            for antenna, power in antenna_powers.items():
                integer(antenna, 1, reader.antennas, "antenna")
                integer(power, 0, 30, "dbm")
            values = reader.get_power()["powers_dbm"]
            for antenna, power in antenna_powers.items():
                values[antenna - 1] = power
            reader.power(powers_dbm=values, persist=True if persistence is None else persistence)
        return True

    @_api(False)
    def set_rf_band(self, band_code, persist=True):
        reader = self._idle()
        with nation_error_context("set_region"):
            if type(band_code) is not int or band_code not in RF_NAMES:
                raise zk_errors.ValidationError("nation_band", "NRN band has no verified mapping; supported: 0,1,3,4,5")
            reader.set_frequency(band_code, persist=persist)
        return True

    def _frequency(self):
        result = self._idle().get_frequency()
        candidates = [item for item in result["nation_candidates"] if item["nation_band"] in RF_NAMES]
        return result["zk"], candidates

    @_api(None)
    def query_rf_band(self):
        actual, candidates = self._frequency()
        code = candidates[0]["nation_band"] if len(candidates) == 1 else None
        return {"band_code": code, "band_name": RF_NAMES.get(code),
                "candidates": candidates, "zk": actual}

    @_api({"mode": "error", "channels": []})
    def query_working_frequency(self):
        actual, candidates = self._frequency()
        if len(candidates) != 1:
            return {"mode": "unknown", "channels": [], "zk": actual, "candidates": candidates}
        candidate = candidates[0]
        full = candidate["channels"] == list(range(REGIONS[candidate["nation_band"]][2]))
        # ZK does not expose Nation's auto/manual flag. Return actual channels.
        return {"mode": "manual", "channels": candidate["channels"],
                "full_band": full, "mode_source": "ZK channel range", "zk": actual}

    @_api(False)
    def configure_baseband(self, speed, q_value, session, inventory_flag):
        reader = self._idle()
        with nation_error_context("set_baseband"):
            if type(inventory_flag) is not int or inventory_flag not in (0, 1):
                raise zk_errors.UnsupportedError("Only fixed inventory_flag A=0/B=1; automatic target=2 is unsupported")
            reader.set_baseband(epc_speed=speed, q=q_value, session=session,
                                allow_approximate=self.allow_approximate)
        self._target = inventory_flag
        return True

    @_api({})
    def query_baseband_profile(self):
        actual = self._idle().get_baseband()
        candidates = actual["epc_speed_candidates"]
        return {"speed": candidates[0] if len(candidates) == 1 else None,
                "q_value": actual["q"], "session": actual["session"],
                "inventory_flag": self._target, "inventory_flag_scope": "adapter session",
                "speed_candidates": candidates, "zk": actual}

    @_api(None)
    def get_session(self):
        return self._idle().get_baseband()["session"]

    def is_idle(self, retry=3, delay=0.3, settle_delay=0.5):
        # Signature retained, but no STOP/retry/delay hidden behind a status query.
        return (self._reader is not None and not self.is_inventory_running()
                and not self._reader.transport.failed and not self._reader.transport.closed)

    @staticmethod
    def build_antenna_mask(antenna_ids):
        if not isinstance(antenna_ids, (list, tuple)):
            raise ValueError("antenna_mask argument is a list of antenna IDs, e.g. [1]")
        mask = 0
        for antenna in antenna_ids:
            integer(antenna, 1, 32, "antenna")
            mask |= 1 << (antenna - 1)
        return mask

    def _check_mask(self, mask):
        reader = self._idle()
        integer(mask, 0, (1 << reader.antennas) - 1, "antenna_mask")
        return reader

    @_api(False)
    def save_antenna_mask(self, antenna_mask):
        self._check_mask(antenna_mask)
        self.antenna_mask = antenna_mask
        return antenna_mask  # Upstream implementation returns the mask, not bool.

    @_api(0)
    def query_enabled_ant_mask(self):
        self._idle()
        return self.antenna_mask

    def _enable(self, ant_id, enabled, save):
        reader = self._idle()
        integer(ant_id, 1, reader.antennas, "antenna")
        if type(save) is not bool:
            raise ValueError("save must be boolean")
        if save:
            raise zk_errors.UnsupportedError("Antenna selection is session-local; use save=False and persist in app")
        bit = 1 << (ant_id - 1)
        self.antenna_mask = self.antenna_mask | bit if enabled else self.antenna_mask & ~bit
        return True

    @_api(False)
    def enable_ant(self, ant_id, save=True):
        return self._enable(ant_id, True, save)

    @_api(False)
    def disable_ant(self, ant_id, save=True):
        return self._enable(ant_id, False, save)

    @_api(None)
    def set_filter_settings(self, repeated_time_ms=0, rssi_threshold=0):
        self._idle()
        integer(repeated_time_ms, 0, 655350, "repeated_time_ms")
        if type(rssi_threshold) is not int or rssi_threshold != 0:
            raise zk_errors.UnsupportedError("NRN RSSI threshold cannot be applied to uncalibrated ZK RSSI; only 0 supported")
        self._repeat_ms = repeated_time_ms // 10 * 10
        return None  # Matches upstream; inspect last_error (or raise_errors=True).

    @_api({"repeat_time": 0, "rssi_threshold": None})
    def query_filter_settings(self):
        self._idle()
        return {"repeat_time": self._repeat_ms // 10, "rssi_threshold": 0,
                "scope": "adapter session; EPC+antenna duplicate filter"}

    def is_inventory_running(self):
        return self._thread is not None and self._thread.is_alive()

    @_api(False)
    def start_inventory_with_mode(self, antenna_mask, callback=None):
        reader = self._idle()
        mask = self.build_antenna_mask(antenna_mask)
        self._check_mask(mask)
        if not mask or (callback is not None and not callable(callback)):
            raise ValueError("Provide nonempty antenna list and a callable callback or None")
        # Read configuration synchronously so Start reports configuration failures.
        query = reader.query()
        if query["session"] == 255:
            raise zk_errors.UnsupportedError("Auto Session is not supported by ordinary inventory")
        self.antenna_mask = mask
        self._stop.clear()
        self.last_inventory_error = None
        self._thread = threading.Thread(target=self._inventory_loop,
                                        args=(reader, list(antenna_mask), callback, query),
                                        name="nrn-zk-inventory", daemon=False)
        self._thread.start()
        return True

    def _inventory_loop(self, reader, antennas, callback, query):
        seen = {}
        try:
            for batch in reader.iter_inventory(self._stop, antennas=antennas,
                                               q=query["q"], session=query["session"], target=self._target):
                for raw in batch["tags"]:
                    if self._stop.is_set():
                        break
                    now = time.monotonic()
                    if self._repeat_ms:
                        # Prune expired entries; disabled by default (no lost counts).
                        window = self._repeat_ms / 1000
                        seen = {key: stamp for key, stamp in seen.items() if now-stamp < window}
                        key = (raw["epc"], raw["antenna"])
                        if key in seen:
                            continue
                        seen[key] = now
                    tag = {"epc": raw["epc"], "pc": None, "antenna_id": raw["antenna"],
                           "rssi": None, "rssi_raw": raw["rssi_raw"], "simulated": reader.simulated}
                    if callback:
                        callback(tag)
                if reader.simulated:
                    self._stop.wait(0.001)
        except Exception as cause:
            # App callback exceptions remain identifiable, never treated as RF status.
            error = (self._record(cause, TagOperationError)
                     if isinstance(cause, zk_errors.ReaderError) else cause)
            self.last_error = self.last_inventory_error = error
            self._stop.set()

    def stop_inventory(self):
        # Do not hold _lock while join waits on the user's callback.
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        return True  # Current finite command completes; no extra RF command/retry.

    @_api({})
    def get_profile(self):
        self._idle()
        # Avoid nested fallback calls masking an earlier failure.
        power = NRNReader.query_reader_power.__wrapped__(self)
        baseband = NRNReader.query_baseband_profile.__wrapped__(self)
        band = NRNReader.query_rf_band.__wrapped__(self)
        frequency = NRNReader.query_working_frequency.__wrapped__(self)
        return {"enabled_antennas": [n for n in range(1, self._reader.antennas+1) if self.antenna_mask & (1 << (n-1))],
                "antenna_powers": power, "baseband": baseband, "rf_band": band,
                "working_frequency": frequency,
                "filtering": {"repeat_time_ms": self._repeat_ms, "rssi_threshold": 0},
                "reader_info": NRNReader.Query_Reader_Information.__wrapped__(self)}

    @staticmethod
    def validate_epc_hex(epc_hex):
        if not isinstance(epc_hex, str):
            raise ValueError("EPC must be hex text")
        data = hex_bytes(epc_hex.strip().replace(" ", ""), "hex", 1, 62)
        return data + (b"\x00" if len(data) % 2 else b"")

    @staticmethod
    def calculate_start_word(epc_hex, *, overwrite_pc=False, prefix_words=0):
        NRNReader.validate_epc_hex(epc_hex)
        integer(prefix_words, 0, 65533, "prefix_words")
        return (1 if overwrite_pc else 2) + prefix_words

    def _write_epc(self, epc, antenna, match, password, start, timeout, auto_pc=False):
        with self._lock:
            self.last_error = None
            try:
                reader = self._idle()
                with nation_error_context("write_tag"):
                    integer(antenna, 1, reader.antennas, "antenna")
                    selector = epc_selector(match)  # No unselected/broadcast write.
                    data = self.validate_epc_hex(epc)
                    integer(start, 1, 65535, "word_address")
                    password = 0 if password is None else integer(password, 0, 0xFFFFFFFF, "password")
                    self._validate_timeout(timeout)
                previous_timeout = reader.transport.timeout
                try:
                    reader.transport.timeout = timeout
                    if auto_pc:
                        # One explicit read then ONE write; preserve lower PC flag bits.
                        pc = reader.read_tag(antenna=antenna, bank=1, word_address=1, words=1,
                                             selector=selector, password=f"{password:08X}")["hex"]
                        value = (len(data)//2 << 11) | (int(pc, 16) & 0x07FF)
                        data = value.to_bytes(2, "big") + data
                    result = reader.write_tag(antenna=antenna, bank=1, word_address=start,
                                               hex=data.hex(), selector=selector, password=f"{password:08X}")
                finally:
                    reader.transport.timeout = previous_timeout
                return {"success": True, "result_code": 0, "result_msg": WRITE_MESSAGES[0],
                        "failed_addr": None, "verified": False, "zk": result,
                        "result_scope": "command_result"}
            except (zk_errors.ReaderError, ValueError, TypeError, OSError) as cause:
                error = self._record(cause, TagOperationError)
                if self.raise_errors:
                    raise error from cause
                mapped = getattr(cause, "nation", None)
                # A pre-write READ failure is not a write-result code.
                code = (mapped.code if mapped and mapped.operation == "write_tag"
                        and mapped.scope == "command_result" else -99)
                return {"success": False, "result_code": code,
                        "result_msg": WRITE_MESSAGES.get(code, str(cause)), "failed_addr": None,
                        "result_scope": "command_result" if code >= 0 else "adapter_error",
                        "error": error.details}

    def write_epc_tag(self, epc_hex, antenna_id=1, match_epc_hex=None,
                      access_password=None, start_word=2, timeout=2.0):
        return self._write_epc(epc_hex, antenna_id, match_epc_hex, access_password, start_word, timeout)

    def write_epc_tag_auto(self, new_epc_hex, match_epc_hex=None, antenna_id=1,
                           access_password=None, timeout=0):
        return self._write_epc(new_epc_hex, antenna_id, match_epc_hex, access_password,
                               1, self.timeout if timeout == 0 else timeout, auto_pc=True)

    @_api(False, TagOperationError)
    def check_write_epc(self, epcHex):
        raise zk_errors.UnsupportedError("Inventory cannot prove write capability; use an explicit selected write and verify")

    @_api(False)
    def select_profile(self, profile_id):
        raise zk_errors.UnsupportedError("NRN profile slots 0/1/2 are not ZK RF profile IDs; use configure_baseband")

    @_api(False)
    def get_beeper(self):
        raise zk_errors.UnsupportedError("Beeper is not implemented on the Ex10 module")

    @_api(False)
    def set_beeper(self, mode):
        raise zk_errors.UnsupportedError("Beeper is not implemented on the Ex10 module")

    def write_epc_to_target_auto(self, target_tag_epc, new_epc_hex, access_pwd=None,
                                 timeout=2.0, scan_timeout=2.0, verify=True,
                                 overwrite_pc=True, prefix_words=0):
        # No pretending an EPC scan verifies a possibly changed/partially written tag.
        cause = zk_errors.UnsupportedError("Auto scan/write/verify helper is not mapped; use write_epc_tag_auto with match_epc_hex, or NationReader.write_tag_verified with TID")
        error = self._record(cause, TagOperationError)
        if self.raise_errors:
            raise error from cause
        return {"success": False, "result_code": -99, "result_msg": str(cause),
                "failed_addr": None, "result_scope": "adapter_error", "error": error.details}


def _unsupported_wire(*args, **kwargs):
    raise ProtocolError(zk_errors.UnsupportedError("Nation wire helpers/UART are not supported; use the NRNReader high-level methods"))


# Public Nation frame helpers cannot be sent to ZK; fail explicitly, never passthrough.
for _name in ("send", "receive", "crc16_ccitt", "build_frame", "build_pcw", "parse_frame",
              "extract_valid_frames", "build_epc_read_payload", "calculate_rssi",
              "calculate_frequency", "parse_epc", "receive_response", "all_read_end_mids",
              "parse_reader_power_response"):
    setattr(NRNReader, _name, staticmethod(_unsupported_wire))


def create_reader(port, baudrate=115200, timeout=0.5, log_level=logging.INFO, **options):
    return NRNReader(port, baudrate, timeout, setup_logging(log_level), **options)
