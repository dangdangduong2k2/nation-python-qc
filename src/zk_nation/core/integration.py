"""Opt-in helpers. No popup, automatic reconnect, retry, rollback or RF settings."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, RLock, get_ident

from .errors import SessionStateError
from .reader import hex_bytes, selector_bytes


def epc_selector(epc):
    """Byte-aligned EPC selector starting after CRC/PC, at bit 32."""
    hex_bytes(epc, "epc", 1, 31)
    return {"bank": 1, "bit_address": 32, "hex": epc.upper()}


def tid_selector(tid, bit_address=0):
    result = {"bank": 2, "bit_address": bit_address, "hex": tid}
    selector_bytes(result)
    result["hex"] = tid.upper()
    return result


def error_details(error):
    """Serializable error metadata. Does not decide or execute recovery actions."""
    mapped = getattr(error, "nation", None)
    raw_data = getattr(error, "data", b"")
    result = {"type": type(error).__name__, "message": str(error),
              "nation": mapped.as_dict() if mapped else None,
              "zk_command": getattr(error, "command", None),
              "zk_status": getattr(error, "status", None),
              "zk_data_hex": bytes(raw_data).hex().upper() if isinstance(raw_data, (bytes, bytearray)) else None}
    for field in ("action_succeeded", "write_acknowledged", "verified"):
        if hasattr(error, field):
            result[field] = getattr(error, field)
    cause = getattr(error, "cause", None) or error.__cause__
    if cause is not None and cause is not error:
        result["cause"] = {"type": type(cause).__name__, "message": str(cause),
                           "zk_status": getattr(cause, "status", None)}
    return result


class _OperationFuture(Future):
    def cancel(self):
        # Cancellation must not leave state transitions half-completed.
        # Use stop_inventory()/disconnect() instead of Future.cancel().
        return False


class ReaderSession:
    """Optional single-worker app adapter. Methods return concurrent Futures.

    All reader I/O stays on one worker. Call result()/exception() from application
    polling/completion handling, not by blocking a GUI event loop. A failed call
    is exposed through its Future and last_error; no recovery action is started.
    """
    OPERATIONS = frozenset(("get_reader_info", "get_power", "set_power", "get_frequency",
                            "set_frequency", "get_baseband", "set_baseband", "read_tag",
                            "write_tag", "write_tag_verified", "inventory_once", "info",
                            "power", "region", "profile", "query", "read", "write"))

    def __init__(self, reader_factory):
        if not callable(reader_factory):
            raise TypeError("reader_factory must be callable")
        self._factory = reader_factory
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nation-zk")
        self._lock = RLock()
        self._reader = None
        self._state = "disconnected"
        self._last_error = None
        self._stop = Event()
        self._inventory_future = None
        self._disconnect_future = None
        self._worker_ident = None
        self._shutdown_requested = False

    def _submit(self, function, *args):
        future = _OperationFuture()
        def execute():
            self._worker_ident = get_ident()
            future.set_running_or_notify_cancel()
            try:
                result = function(*args)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)
        self._executor.submit(execute)
        return future

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    def _require(self, *states, allow_shutdown=False):
        if self._state not in states or (self._shutdown_requested and not allow_shutdown):
            raise SessionStateError(f"Operation requires {states}; current state={self._state}")

    def connect(self):
        with self._lock:
            self._require("disconnected")
            self._state, self._last_error = "connecting", None
            return self._submit(self._connect)

    def _connect(self):
        try:
            reader = self._factory()
            with self._lock:
                self._reader = reader
                if self._state != "disconnecting":
                    self._state = "idle"
            return True
        except BaseException as error:
            with self._lock:
                self._last_error = error
                if self._state != "disconnecting":
                    self._state = "disconnected"
            raise

    def call(self, operation, *args, **kwargs):
        if operation not in self.OPERATIONS:
            raise ValueError("Unknown session operation")
        with self._lock:
            self._require("idle")
            self._state, self._last_error = "busy", None
            return self._submit(self._run, operation, args, kwargs)

    def _run(self, operation, args, kwargs):
        try:
            return getattr(self._reader, operation)(*args, **kwargs)
        except BaseException as error:
            with self._lock:
                self._last_error = error
            raise
        finally:
            with self._lock:
                if self._state != "disconnecting":
                    transport = self._reader.transport
                    self._state = "faulted" if transport.failed or transport.closed else "idle"

    def start_inventory(self, on_batch, **kwargs):
        if not callable(on_batch):
            raise TypeError("on_batch must be callable")
        with self._lock:
            self._require("idle")
            self._state, self._last_error = "inventory", None
            self._stop = Event()
            self._inventory_future = self._submit(
                self._run, "run_inventory", (on_batch, self._stop), kwargs)
            return self._inventory_future

    def stop_inventory(self):
        """Signal Stop only; returned Future finishes when the current command ends."""
        with self._lock:
            self._stop.set()
            if self._state == "inventory":
                self._state = "stopping"
            return self._inventory_future

    def disconnect(self):
        """Explicitly stop then close on the worker, never closing mid-packet."""
        with self._lock:
            self._require("disconnected", "connecting", "idle", "busy", "inventory", "stopping", "faulted", "disconnecting", allow_shutdown=True)
            if self._state == "disconnecting":
                return self._disconnect_future
            self._stop.set()
            self._state = "disconnecting"
            self._disconnect_future = self._submit(self._disconnect)
            return self._disconnect_future

    def _disconnect(self):
        try:
            if self._reader is not None:
                self._reader.close()
            with self._lock:
                self._reader = None
                self._state = "disconnected"
        except BaseException as error:
            with self._lock:
                self._last_error = error
                self._state = "faulted"
            raise

    def shutdown(self):
        """Blocking final cleanup, called by the app after disconnect completion.

        Never call from an inventory callback (it runs on the worker being joined).
        """
        with self._lock:
            if self._state == "closed":
                return
            if get_ident() == self._worker_ident:
                raise SessionStateError("shutdown() cannot join its own worker; call from the application thread")
            self._shutdown_requested = True
        try:
            self.disconnect().result()
        finally:
            self._executor.shutdown(wait=True)
            with self._lock:
                self._state = "closed"
