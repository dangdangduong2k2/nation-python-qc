"""Enumerate available ports without opening or probing any device."""


def list_ports():
    from serial.tools.list_ports import comports
    return [{"port": p.device, "description": p.description, "vid": p.vid,
             "pid": p.pid, "serial_number": p.serial_number}
            for p in comports()]
