"""Devantech USB-ISS I2C adapter (USB 04D8:FFEE, a virtual COM port), as one I2CBus.

Implements the module register access of hw.base.I2CBus with CMIS paging: bytes
0x00-0x7F are the lower page; for bytes 0x80-0xFF the page is selected by writing
byte 0x7F first (skipped when the module is already on that page).
"""
from __future__ import annotations

from typing import Callable

ISS_CMD = 0x5A
ISS_VERSION, ISS_MODE, GET_SER_NUM = 0x01, 0x02, 0x03
I2C_AD1 = 0x55          # device with a 1-byte register address
I2C_TEST = 0x58
IO_GETAD = 0x65         # read an analogue input pin (10-bit); I2C Read Vcc.vi uses it for the module VCC
IO_TYPE_LABVIEW = 0x0F  # I/O pins 1-2 analogue inputs, 3-4 outputs (as I2C Read Vcc.vi sets them)
USB_ISS_ID = 7
I2C_MODES = {0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80}
I2C_H_100KHZ = 0x60
MAX_READ, MAX_WRITE = 60, 59
PAGE_SELECT = 0x7F


class UsbIssBus:
    def __init__(self, port: str, address: int = 0x50, log: Callable[[str], None] = lambda _t: None):
        self.port = port
        self.address = address          # 7-bit; CMIS modules use 0x50
        self.log = log
        self.serial = None
        self._page = None

    # ---- adapter ----
    def open(self) -> "UsbIssBus":
        import serial   # pyserial

        self.serial = serial.Serial(self.port, 9600, timeout=0.5)
        self.serial.reset_input_buffer()
        module_id, firmware, mode = self._xfer(bytes([ISS_CMD, ISS_VERSION]), 3)
        if module_id != USB_ISS_ID:
            self.close()
            raise RuntimeError(f"{self.port} is not a USB-ISS (module id {module_id})")
        if mode & 0xF0 not in I2C_MODES:
            # adapter configuration only (not a module write): hardware I2C at 100 kHz with the
            # I/O pins configured as LabVIEW does on this station
            if self._xfer(bytes([ISS_CMD, ISS_MODE, I2C_H_100KHZ, IO_TYPE_LABVIEW]), 2)[0] != 0xFF:
                raise RuntimeError(f"{self.port}: could not put the USB-ISS into I2C mode")
        serial_no = self._xfer(bytes([ISS_CMD, GET_SER_NUM]), 8).decode(errors="replace")
        self.log(f"USB-ISS {self.port}: serial {serial_no}, firmware {firmware}, mode 0x{mode:02X}")
        return self

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_exc):
        self.close()

    def _xfer(self, data: bytes, reply_len: int) -> bytes:
        if self.serial is None:
            raise RuntimeError(f"USB-ISS {self.port} is not open")
        self.serial.write(data)
        reply = self.serial.read(reply_len)
        if len(reply) != reply_len:
            raise TimeoutError(f"USB-ISS {self.port}: expected {reply_len} bytes, got {len(reply)}")
        return reply

    def read_adc(self, pin: int) -> int:
        """10-bit reading of analogue input pin 1-4 (counts; full scale is the USB 5 V)."""
        reply = self._xfer(bytes([IO_GETAD, pin]), 2)
        return (reply[0] << 8) | reply[1]

    # ---- raw I2C ----
    def present(self) -> bool:
        return self._xfer(bytes([I2C_TEST, self.address << 1]), 1)[0] != 0

    def _read_raw(self, reg: int, length: int) -> bytes:
        out = b""
        while length > 0:
            n = min(length, MAX_READ)
            out += self._xfer(bytes([I2C_AD1, (self.address << 1) | 1, reg, n]), n)
            reg, length = reg + n, length - n
        return out

    def _write_raw(self, reg: int, data: bytes) -> None:
        for start in range(0, len(data), MAX_WRITE):
            chunk = data[start:start + MAX_WRITE]
            if self._xfer(bytes([I2C_AD1, self.address << 1, reg + start, len(chunk)]) + chunk, 1)[0] == 0:
                raise IOError(f"USB-ISS {self.port}: module NAK writing 0x{reg + start:02X}")

    def forget_page(self) -> None:
        """Re-read the page register on the next paged access (after a reset or password entry)."""
        self._page = None

    # ---- I2CBus ----
    def _select_page(self, page: int) -> None:
        if self._page is None:
            self._page = self._read_raw(PAGE_SELECT, 1)[0]
        if self._page != page:
            self._write_raw(PAGE_SELECT, bytes([page]))
            self._page = page

    def read(self, page: int, byte: int, length: int) -> bytes:
        if byte + length > 0x100:
            raise ValueError(f"read 0x{byte:02X}+{length} runs past the end of the page")
        if byte >= 0x80:
            self._select_page(page)
        return self._read_raw(byte, length)

    def write(self, page: int, byte: int, data: bytes) -> None:
        if byte >= 0x80:
            self._select_page(page)
        self._write_raw(byte, data)
