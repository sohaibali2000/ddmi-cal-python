"""Register map: name -> page/byte/length/format, with encode and decode."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import CONFIG_DIR, ConfigError

FORMATS = ("u8", "u16", "u16le", "s16_div256", "u16_scaled", "ascii", "u16_array", "u16le_array", "bytes",
           "u16_scaled_array", "volt_array", "f32_array")
CONFIDENCE = ("STD", "CODE", "HW", "UNVERIFIED")


def _u16(value) -> int:
    return max(0, min(0xFFFF, round(value)))


@dataclass(frozen=True)
class Register:
    name: str
    page: int
    byte: int
    length: int
    fmt: str
    conf: str
    scale: float = 1.0
    full_scale: float = 5.0

    @property
    def verified(self) -> bool:
        return self.conf != "UNVERIFIED"

    @property
    def address(self) -> str:
        return f"{self.page:02X}h/0x{self.byte:02X}/{self.length}"

    def decode(self, data: bytes):
        if len(data) != self.length:
            raise ValueError(f"{self.name}: expected {self.length} bytes, got {len(data)}")
        fmt = self.fmt
        if fmt == "u8":
            return data[0]
        if fmt == "u16":
            return int.from_bytes(data, "big")
        if fmt == "u16le":
            return int.from_bytes(data, "little")
        if fmt == "bytes":
            return list(data)
        if fmt == "u16le_array":
            return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        if fmt == "s16_div256":
            return int.from_bytes(data, "big", signed=True) / 256
        if fmt == "u16_scaled":
            return int.from_bytes(data, "big") * self.scale
        if fmt == "ascii":
            return data.decode("ascii", errors="replace")
        words = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]
        if fmt == "u16_array":
            return words
        if fmt == "u16_scaled_array":
            return [w / self.scale for w in words]
        if fmt == "volt_array":
            return [w / 4095 * self.full_scale for w in words]
        if fmt == "f32_array":
            return list(struct.unpack(f">{len(data) // 4}f", data))
        raise ValueError(f"{self.name}: unknown format {fmt}")

    def encode(self, value) -> bytes:
        fmt = self.fmt
        if fmt == "u8":
            data = bytes([int(value) & 0xFF])
        elif fmt == "u16":
            data = _u16(value).to_bytes(2, "big")
        elif fmt == "u16le":
            data = _u16(value).to_bytes(2, "little")
        elif fmt == "bytes":
            data = bytes(int(v) & 0xFF for v in value)
        elif fmt == "u16le_array":
            data = b"".join(_u16(v).to_bytes(2, "little") for v in value)
        elif fmt == "s16_div256":
            data = max(-0x8000, min(0x7FFF, round(value * 256))).to_bytes(2, "big", signed=True)
        elif fmt == "u16_scaled":
            data = _u16(value / self.scale).to_bytes(2, "big")
        elif fmt == "ascii":
            data = str(value).encode("ascii").ljust(self.length)[:self.length]
        elif fmt == "u16_array":
            data = b"".join(_u16(v).to_bytes(2, "big") for v in value)
        elif fmt == "u16_scaled_array":
            data = b"".join(_u16(v * self.scale).to_bytes(2, "big") for v in value)
        elif fmt == "volt_array":
            data = b"".join(_u16(v / self.full_scale * 4095).to_bytes(2, "big") for v in value)
        elif fmt == "f32_array":
            data = struct.pack(f">{len(value)}f", *value)
        else:
            raise ValueError(f"{self.name}: unknown format {fmt}")
        if len(data) != self.length:
            raise ValueError(f"{self.name}: value encodes to {len(data)} bytes, register is {self.length}")
        return data


class RegMap:
    def __init__(self, registers: dict[str, Register], values: dict[str, int]):
        self.registers = registers
        self.values = values
        self._by_address = {(r.page, r.byte): r for r in registers.values()}

    def __getitem__(self, name: str) -> Register:
        try:
            return self.registers[name]
        except KeyError:
            raise KeyError(f"register {name!r} is not in the register map") from None

    def at(self, page: int, byte: int) -> Register | None:
        return self._by_address.get((page, byte))

    def value(self, name: str) -> int:
        return self.values[name]


def load_regmap(path=CONFIG_DIR / "registers_aws.yaml") -> RegMap:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load register map {path}: {exc}") from exc
    registers = {}
    for name, entry in raw.get("registers", {}).items():
        try:
            reg = Register(name=name, page=entry["page"], byte=entry["byte"], length=entry["len"],
                           fmt=entry["fmt"], conf=entry["conf"], scale=entry.get("scale", 1.0),
                           full_scale=entry.get("full_scale", 5.0))
        except KeyError as exc:
            raise ConfigError(f"register {name}: missing {exc}") from exc
        if reg.fmt not in FORMATS:
            raise ConfigError(f"register {name}: unknown fmt {reg.fmt!r}")
        if reg.conf not in CONFIDENCE:
            raise ConfigError(f"register {name}: conf must be one of {CONFIDENCE}")
        registers[name] = reg
    return RegMap(registers, dict(raw.get("values", {})))
