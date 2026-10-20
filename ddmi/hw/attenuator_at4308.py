"""TriBrer / Semight AT4308 8-channel attenuator, through the vendor's 32-bit DLLs.

The DLLs (Inst_ST_OptAtt.dll + LX_VOA.dll) are 32-bit, so they run in a small 32-bit
host (voa_host/voa_host.cs) that is compiled on first use with the C# compiler that
ships with Windows (.NET Framework 4); no install or admin rights needed.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

CSC = Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe")   # 32-bit compiler
HOST_DIR = Path(__file__).resolve().parent / "voa_host"
BIN_DIR = HOST_DIR / "bin"                                            # git-ignored
VENDOR_DLLS = ("Inst_ST_OptAtt.dll", "LX_VOA.dll")


def build_host(dll_dir: Path) -> Path:
    """Copy the vendor DLLs next to the host and compile it if needed. Returns the exe path."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name in VENDOR_DLLS:
        src = Path(dll_dir) / name
        if not src.is_file():
            raise FileNotFoundError(f"attenuator DLL not found: {src}")
        dst = BIN_DIR / name
        if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
    exe, source = BIN_DIR / "voa_host.exe", HOST_DIR / "voa_host.cs"
    if not exe.is_file() or exe.stat().st_mtime < source.stat().st_mtime:
        if not CSC.is_file():
            raise FileNotFoundError(f"C# compiler not found: {CSC}")
        result = subprocess.run(
            [str(CSC), "-nologo", "-platform:x86", f"-out:{exe}", f"-r:{BIN_DIR / VENDOR_DLLS[0]}", str(source)],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"could not build the attenuator host:\n{result.stdout}{result.stderr}")
    return exe


class AttenuatorAT4308:
    SET_DELAY_S = 0.25        # Semight AT4208 IP Control.vi V6.8: 0.25 s after each Set
    MAX_ATT_DB = 60.0

    def __init__(self, com_port: str, dll_dir: str, log: Callable[[str], None] = print):
        self.com_port = com_port
        self.dll_dir = Path(dll_dir)
        self.log = log
        self.proc = None
        self.wave_ids: dict[int, int] = {}      # nm -> wavelength ID used by setWaveLength

    def _cmd(self, line: str) -> str:
        if self.proc is None:
            raise RuntimeError("attenuator is not open")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        reply = self.proc.stdout.readline().strip()
        if not reply.startswith("OK"):
            raise RuntimeError(f"attenuator {self.com_port}: {line!r} failed: {reply or 'no reply'}")
        return reply[2:].strip()

    def connect(self) -> None:
        exe = build_host(self.dll_dir)
        self.proc = subprocess.Popen([str(exe)], cwd=str(BIN_DIR), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        port = int("".join(ch for ch in self.com_port if ch.isdigit()))
        self._cmd(f"open {port}")
        product, serial, version, channels = self._cmd("info").split("|")
        self.channels = int(channels)
        self.wave_ids = {int(nm): i for i, nm in enumerate(self._cmd("wavelengths").split(), start=1)}
        self.log(f"Attenuator on {self.com_port}: {product}, SN {serial}, version {version}, "
                 f"{self.channels} channels, wavelengths {sorted(self.wave_ids)} nm")

    def close(self) -> None:
        if self.proc is not None:
            try:
                self._cmd("close")
            except Exception:
                pass
            self.proc.wait(timeout=5)
            self.proc = None

    def read(self) -> dict:
        """Per channel: attenuation (dB), input and output power (dBm), wavelength (nm)."""
        fields = dict(part.split("=", 1) for part in self._cmd("read").split(";"))
        by_id = {i: nm for nm, i in self.wave_ids.items()}
        return {
            "att_db": [float(v) for v in fields["att"].split(",")],
            "in_dbm": [float(v) for v in fields["in"].split(",")],
            "out_dbm": [float(v) for v in fields["out"].split(",")],
            "wavelength_nm": [by_id.get(int(v)) for v in fields["wlid"].split(",")],
        }

    # Attenuator protocol (hw.base)
    def set_wavelength(self, ch: int, nm: float) -> None:
        nm = int(round(nm))
        if nm not in self.wave_ids:
            raise ValueError(f"attenuator has no calibration for {nm} nm (has {sorted(self.wave_ids)})")
        self._cmd(f"set_wl {ch} {self.wave_ids[nm]}")
        time.sleep(self.SET_DELAY_S)

    def set_attenuation(self, ch: int, db: float) -> None:
        if not 0 <= db <= self.MAX_ATT_DB:
            raise ValueError(f"attenuation {db} dB is outside 0-{self.MAX_ATT_DB} dB")
        self._cmd(f"set_att {ch} {db:.2f}")
        time.sleep(self.SET_DELAY_S)
