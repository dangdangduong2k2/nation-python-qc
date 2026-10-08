"""Operation-specific Nation error mapping, ported from rs232-bridge RC9.

Code namespaces matter: command_result 3 is NOT protocol_error 3. Raw exceptions
remain catchable; the facade adds .nation, .nation_code and .nation_scope.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps

from .errors import (DeviceError, ProtocolError, ReaderError, RestoreError,
                     TransportError, UnsupportedError, ValidationError)


@dataclass(frozen=True)
class NationErrorInfo:
    operation: str
    scope: str
    code: int | None
    zk_command: int | None
    zk_status: int | None
    zk_data_hex: str
    tag_error: int | None
    original_type: str
    action_succeeded: bool | None = None

    def as_dict(self):
        return asdict(self)


def map_write_result(status, tag_error=0):
    """Exact WriteExecutor.MapResult table (including success 0)."""
    direct = {0: 0, 5: 8, 0x0C: 8, 0x10: 7, 0xFA: 10, 0xFB: 10,
              0xF8: 1, 0xFD: 3, 0xFF: 3, 0x0B: 9, 0x19: 9}
    if status == 0xFC:
        return {3: 6, 4: 7, 0x0B: 5}.get(tag_error, 9)
    return direct.get(status, 11)


OPERATIONS = frozenset(("get_reader_info", "get_power", "set_power", "get_frequency",
                        "set_region", "set_frequency", "get_baseband", "set_baseband",
                        "write_tag", "read_tag", "inventory"))


def map_nation_error(operation, error):
    """Map an exception without I/O, retries or mutations of the original error.

    read_tag maps to the C# inventory tag-result field, since that bridge has no
    standalone read-memory reply. RestoreError has no faithful C# error reply;
    return scope=python_extension/code=None instead of inventing success/failure.
    """
    if operation not in OPERATIONS:
        raise ValueError("Unknown Nation operation")
    if not isinstance(error, (ReaderError, ValueError, OSError)):
        raise TypeError("Expected reader, validation or I/O error")
    raw = error.cause if isinstance(error, RestoreError) else error
    device = raw if isinstance(raw, DeviceError) else None
    status = device.status if device else None
    tag = device.data[0] if device and status == 0xFC and device.data else None
    scope, code = "protocol_error", 9
    parameter = error.parameter if isinstance(error, ValidationError) else None

    if isinstance(error, RestoreError):
        scope, code = "python_extension", None
    elif isinstance(error, UnsupportedError):
        code = 3
    elif isinstance(error, ValueError):
        code = 7  # malformed/unsupported Python argument shape; no invented field
        fields = {
            "set_power": {"antenna": 1, "dbm": 2},
            "set_region": {"nation_band": 1},
            "set_frequency": {"channel": 1, "channels": 2},
            "set_baseband": {"epc_speed": 1, "q": 2, "Nation session": 3},
            "write_tag": {"antenna": 1, "selector": 2, "selector.bank": 2,
                          "selector.bit_address": 2, "selector.hex": 2,
                          "bank": 3, "word_address": 3, "hex": 3},
        }
        if operation == "inventory":
            scope, code = "command_result", 5
        elif parameter in fields.get(operation, {}):
            scope, code = "command_result", fields[operation][parameter]
    elif operation == "write_tag":
        scope, code = "command_result", map_write_result(status, tag or 0) if device else 11
    elif operation == "set_power":
        scope, code = "command_result", 3
    elif operation == "set_region":
        scope, code = "command_result", 1 if status is not None and status >= 0xFD else 2
    elif operation == "set_frequency":
        scope, code = "command_result", 4
    elif operation == "set_baseband":
        scope, code = "command_result", 1 if status is not None and status >= 0xFD else 6
    elif operation == "inventory":
        scope, code = "inventory_end", 2
    elif operation == "read_tag" and device:
        if status in (0x30, 0x33, 0x35, 0x37):
            scope, code = "inventory_end", 2  # C# CheckTransport -> scan failure
        else:
            scope = "tag_result"
            if status == 5:
                code = 5
            elif status in (0xFA, 0xFB):
                code = 1
            elif status == 0xFC:
                code = {3: 4, 4: 3}.get(tag, 6)
            else:
                code = 7
    elif isinstance(error, ProtocolError):
        code = 7

    return NationErrorInfo(operation, scope, code, device.command if device else None,
                           status, device.data.hex().upper() if device else "", tag,
                           type(error).__name__,
                           error.action_succeeded if isinstance(error, RestoreError) else None)


@contextmanager
def nation_error_context(operation):
    try:
        yield
    except (ReaderError, ValueError, OSError) as error:
        error.nation = map_nation_error(operation, error)
        error.nation_code = error.nation.code
        error.nation_scope = error.nation.scope
        raise


def nation_errors(operation):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with nation_error_context(operation):
                return function(*args, **kwargs)
        return call
    return decorate
