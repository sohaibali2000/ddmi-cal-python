"""Unit conversions (build map 7.1). "Linear" power is mW x 10000, i.e. 0.1 uW."""
import math


def dbm_to_lin(dbm: float) -> int:
    return round(10 ** (dbm / 10) * 10000)


def dbm_to_lin_exact(dbm: float) -> float:
    """dBm -> mW x 10000 without rounding, as DDMI_Cal.vi V6.8 feeds the curve fit
    (x / 10, 10^x, x 10000): -2 dBm -> 6309.57."""
    return 10 ** (dbm / 10) * 10000


def lin_to_dbm(lin: float) -> float:
    if lin <= 0:
        return float("-inf")
    return 10 * math.log10(lin / 10000)


def volts_to_lin(volts: float) -> float:
    return volts * 10000


def lin_to_volts(lin: float) -> float:
    return lin / 10000


def temp_from_raw(raw: int) -> float:
    """R_DTEMP: unsigned 16-bit register value read as signed, in 1/256 degC."""
    raw &= 0xFFFF
    if raw >= 0x8000:
        raw -= 0x10000
    return raw / 256
