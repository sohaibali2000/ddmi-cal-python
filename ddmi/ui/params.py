"""Result rows, limits and equipment list shown by the UI.

Limits are the placeholders from the build map (Section 4.1); the MES spec
replaces them once the real MES client exists.
"""

CHANNELS = 8
MAX_SLOTS = 4

PLANS = ["DDMI Cal (V6.8, AWS 2xFR4)"]

# Dashboard step lights, in run order.
STEPS = ["Startup", "Scan MPD", "TX Cal", "RX Cal", "VCC Cal", "Final"]

TX_ROWS = [
    "Tops_Setpoint", "Tops_off(V)", "Tx_Disable(dBm)",
    "Txp_Slope", "Txp_Offset",
    "TxP(dBm)", "DDMI_TxP(dBm)", "dTxP(dB)",
]
RX_ROWS = [
    "RSSI@-2", "RSSI@-10",
    "Rxp_Slope", "Rxp_Offset",
    "DD_RxP@-6(dBm)", "dRxP(dB)",
]
# Module-level items: one value per module, not per channel.
DDMI_ROWS = [
    "ICC(mA)",
    "Vcc_Slope", "Vcc_Offset",
    "Real_Vcc(V)", "DDMI_Vcc(V)", "dVcc(V)",
    "DDMI_Temp(C)",
]
GROUPS = [("TX", TX_ROWS), ("RX", RX_ROWS), ("DDMI", DDMI_ROWS)]
MODULE_LEVEL = set(DDMI_ROWS)

LIMITS = {
    "Tx_Disable(dBm)": (-60.0, -16.0),
    "dTxP(dB)": (-0.5, 0.5),
    "dRxP(dB)": (-0.5, 0.5),
    "dVcc(V)": (-0.033, 0.033),
    "DDMI_Temp(C)": (30.0, 60.0),
}
TX_LOSS_WARN_DB = 3.5

# key, label, models (from the LabVIEW driver VIs in the V6.8 project)
EQUIPMENT = [
    ("i2c", "I2C adapter", ["CH341A", "Promira", "USB-ISS"]),
    ("power_supply", "Power supply", ["GWPS320x", "E363X", "PST3202"]),
    ("power_meter", "Power meter", ["WG3028", "8163B", "LTB-8"]),
    ("attenuator_1", "Attenuator 1 (CH1-4)", ["ST OptAtt", "Stelight VOA", "LTB-8", "MAP200"]),
    ("attenuator_2", "Attenuator 2 (CH5-8)", ["ST OptAtt", "Stelight VOA", "LTB-8", "MAP200"]),
    ("opt_switch", "Optical switch", ["None", "Stelight OSW", "WG3023"]),
]

SIM_FAULTS = ["None", "Dead TX lane (CH3)", "RX offset (CH6)", "SN mismatch"]


def check(param: str, value) -> bool:
    lim = LIMITS.get(param)
    if lim is None or not isinstance(value, (int, float)):
        return True
    return lim[0] <= value <= lim[1]


def item_name(param: str, ch: int) -> str:
    return param if ch == 0 else f"{param}_CH{ch}"
