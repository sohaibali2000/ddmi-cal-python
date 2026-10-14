"""8-channel MPO power meter (LX), through the vendor's Inst_LX_PowerMeter .NET DLL.

Ported from the production DDMI TX Loss tool (power_meter_lx.py). Needs pythonnet
(`clr`) and the DLL, whose path comes from the station config. Not testable without
the instrument.
"""
from __future__ import annotations

import time
from pathlib import Path

WAVELENGTHS = (1270, 1290, 1310, 1330)
AVG_TIMES_MS = (100, 200, 500)


class PowerMeterLX:
    def __init__(self, com_port: str, dll_path: str, log=print):
        self.com_port = com_port
        self.dll_path = Path(dll_path)
        self.log = log
        self.pm = None
        self._lib = None

    @property
    def connected(self) -> bool:
        return self.pm is not None

    def connect(self) -> None:
        if not self.dll_path.is_file():
            raise FileNotFoundError(f"power meter DLL not found: {self.dll_path}")
        import clr   # pythonnet; only needed on a real station

        clr.AddReference(str(self.dll_path))
        from LX.TestLibrary import AvgTime, PowerMeter, WaveLength

        self._lib = {"AvgTime": AvgTime, "WaveLength": WaveLength}
        self.pm = PowerMeter(self.com_port)
        self.log(f"Power meter on {self.com_port}: {self.pm.GetProductName()}, SN {self.pm.GetSerialNumber()}, "
                 f"version {self.pm.GetVerion()}, {self.pm.GetChannelCount()} channels")

    def close(self) -> None:
        if self.pm is not None:
            try:
                self.pm.CloseDevice()
            finally:
                self.pm = None

    def _require(self):
        if self.pm is None:
            raise RuntimeError(f"power meter on {self.com_port} is not connected")

    def configure(self, wavelengths_nm: list[int], avg_time_ms: int) -> None:
        self._require()
        wl, avg = self._lib["WaveLength"], self._lib["AvgTime"]
        if avg_time_ms not in AVG_TIMES_MS:
            raise ValueError(f"averaging time must be one of {AVG_TIMES_MS} ms, got {avg_time_ms}")
        avg_enum = getattr(avg, f"T{avg_time_ms}ms")
        for ch, nm in enumerate(wavelengths_nm, start=1):
            if nm not in WAVELENGTHS:
                raise ValueError(f"CH{ch}: the meter supports {WAVELENGTHS} nm, not {nm}")
            self.pm.SetWaveLength(ch, getattr(wl, f"W{nm}"))
            time.sleep(0.02)
            self.pm.SetAverageTime(ch, avg_enum)
            time.sleep(0.02)

    def read_all_dbm(self) -> list[float]:
        """GetPower(0) returns every channel, already in dBm."""
        self._require()
        return [float(p) for p in self.pm.GetPower(0)]
