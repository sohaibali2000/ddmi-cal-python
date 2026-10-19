"""Result rows, limits and equipment list shown by the UI.

Limits come from config/product_aws_2xfr4.yaml (placeholders until the MES
spec replaces them).
"""
from ..config import load_product
from ..flow import STAGES
from ..results import limits_from

_product = load_product()
_rx = _product["rx_cal"]

CHANNELS = _product["channels"]
MAX_SLOTS = 4

PLANS = ["DDMI Cal (V6.8, AWS 2xFR4)"]

# Dashboard step lights, in run order.
STEPS = STAGES

TX_ROWS = [
    "Bias_setpoint", "Tops_Setpoint", "Tops_Mid(V)", "Tops_off(V)", "Tx_Disable(dBm)",
    "Txp_Slope", "Txp_Offset",
    "TxP(dBm)", "DDMI_TxP(dBm)", "dTxP(dB)",
    "TOPS_Relock",                  # Python-only: relock attempts the lane needed (0 = never)
]
RX_ROWS = [
    *(f"RSSI@{p:g}" for p in _rx["points_dbm"]),
    "Rxp_Slope", "Rxp_Offset",
    f"DD_RxP@{_rx['check_dbm']:g}(dBm)", "dRxP(dB)",
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

LIMITS = limits_from(_product["limits"])
TX_LOSS_WARN_DB = _product["limits"]["tx_loss_warn_db"]

# key, label, models (from the LabVIEW driver VIs in the V6.8 project)
EQUIPMENT = [
    ("i2c", "I2C adapter", ["CH341A", "Promira", "USB-ISS"]),
    ("power_supply", "Power supply", ["GWPS320x", "E363X", "PST3202"]),
    ("power_meter", "Power meter (8-ch MPO)", ["LX 8-ch MPO"]),
    ("attenuator_1", "Attenuator 1 (CH1-4)", ["ST OptAtt", "Stelight VOA", "LTB-8", "MAP200"]),
    ("attenuator_2", "Attenuator 2 (CH5-8)", ["ST OptAtt", "Stelight VOA", "LTB-8", "MAP200"]),
]

# Settings label -> simulator fault name
SIM_FAULTS = {
    "None": "none",
    "Dead TX lane (CH3)": "dead_tx_lane",
    "RX non-linear (CH3)": "rx_nonlinear",
    "SN mismatch": "sn_mismatch",
}
