class ReaderError(Exception):
    """Base error; never implies a failed write is safe to retry."""


class ValidationError(ValueError):
    """ValueError with a stable parameter name for Nation result mapping."""
    def __init__(self, parameter, message):
        self.parameter = parameter
        super().__init__(message)


class UnsupportedError(ReaderError):
    pass


class SessionStateError(ReaderError):
    """Application requested an operation in the wrong session state."""


class VerificationError(ReaderError):
    """Write was acknowledged but subsequent verification failed; never retry blindly."""
    def __init__(self, cause=None, actual_hex=None):
        self.write_acknowledged = True
        self.verified = False
        self.cause = cause
        self.actual_hex = actual_hex
        super().__init__("Write acknowledged, but readback verification failed; do not automatically repeat the write")


class ProtocolError(ReaderError):
    pass


class TransportError(ReaderError):
    pass


class DeviceError(ReaderError):
    def __init__(self, command, status, data=b""):
        self.command, self.status, self.data = command, status, data
        super().__init__(f"Command 0x{command:02X}: device status 0x{status:02X}")


class RestoreError(ReaderError):
    def __init__(self, action_succeeded, cause):
        self.action_succeeded = action_succeeded
        self.cause = cause
        super().__init__("Antenna restore failed; action_succeeded=" + str(action_succeeded)
                         + ". Check reader state before continuing; do not retry a write blindly.")
