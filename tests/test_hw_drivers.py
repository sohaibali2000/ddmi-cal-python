"""Hardware drivers tested against fakes (the protocol details were confirmed on the lab station)."""
from types import SimpleNamespace

import pytest

from ddmi import config
from ddmi.hw.ports import resolve_port
from ddmi.hw.power_supply_gpp import GppSupply, SupplyError
from ddmi.station import build_station

PORTS = [
    SimpleNamespace(device="COM14", serial_number="00071351", location="1-2.3"),
    SimpleNamespace(device="COM16", serial_number="00071349", location="1-2.4.1"),
    SimpleNamespace(device="COM20", serial_number="EZ201279", location="1-2.5"),
    SimpleNamespace(device="COM23", serial_number=None, location="1-2.4.4.1"),
    SimpleNamespace(device="COM24", serial_number=None, location="1-2.4.4.2"),
]


@pytest.mark.parametrize("resource, port", [
    ("COM7", "COM7"),
    ("usb-sn:00071351", "COM14"),
    ("usb-sn:ez201279", "COM20"),
    ("usb-loc:.4.4.1", "COM23"),
    ("usb-loc:.4.4.2", "COM24"),
])
def test_ports_are_found_by_serial_number_or_hub_position(resource, port):
    assert resolve_port(resource, PORTS) == port


def test_a_missing_port_lists_what_is_connected():
    with pytest.raises(LookupError, match=r"usb-sn:12345678.*COM14 \(SN 00071351"):
        resolve_port("usb-sn:12345678", PORTS)


def test_an_ambiguous_position_is_refused():
    with pytest.raises(LookupError, match="more than one"):
        resolve_port("usb-loc:.1", PORTS + [SimpleNamespace(device="COM30", serial_number=None, location="9.1")]
                     + [SimpleNamespace(device="COM31", serial_number=None, location="8.1")])


class FakeGpp:
    """Answers like the lab GPP-3060 (firmware V1.08)."""

    def __init__(self, ident="GW Instek,GPP-3060,SN:GEZ201279,V1.08", output="ON"):
        self.ident, self.output = ident, output
        self.vset, self.sent, self._reply = 3.502, [], b""

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        replies = {"*IDN?": self.ident, ":OUTPut1:STATe?": self.output, "VSET1?": f"{self.vset:06.3f}",
                   "VOUT1?": "03.506V", "IOUT1?": "1.9775A"}
        if cmd.startswith("VSET1:"):
            self.vset = float(cmd.split(":")[1])
        elif cmd in replies:
            self._reply = (replies[cmd] + "\n").encode()

    def readline(self):
        reply, self._reply = self._reply, b""
        return reply

    def close(self):
        pass


def supply(fake, **kwargs):
    return GppSupply("COM20", serial_factory=lambda _port: fake, **kwargs).open()


def test_gpp_reads_like_the_lab_unit():
    fake = FakeGpp()
    psu = supply(fake)
    assert psu.output_on()
    assert psu.read_current() == pytest.approx(1.9775)
    assert psu.read_module_voltage() == pytest.approx(3.506)


def test_gpp_sets_and_verifies_the_voltage():
    fake = FakeGpp()
    supply(fake).set_voltage(3.47)
    assert "VSET1:3.470" in fake.sent and fake.vset == pytest.approx(3.47)


def test_gpp_never_sets_above_the_station_limit():
    fake = FakeGpp()
    with pytest.raises(SupplyError, match="limit"):
        supply(fake, max_voltage_v=3.9).set_voltage(4.2)
    assert not any(cmd.startswith("VSET1:") for cmd in fake.sent)


def test_gpp_never_switches_the_output():
    fake = FakeGpp()
    psu = supply(fake)
    psu.set_voltage(3.3)
    psu.output_on()
    assert not any(cmd.startswith(":OUTP") and not cmd.endswith("?") for cmd in fake.sent)


def test_something_else_on_the_port_is_refused():
    with pytest.raises(SupplyError, match="not a GW Instek GPP"):
        supply(FakeGpp(ident="Semight,AT4308,x,v3.08"))


def test_lab_config_loads_and_unknown_instruments_are_named():
    lab = config.load_station(config.CONFIG_DIR / "station_lab.yaml")
    assert lab["i2c"]["resource"].startswith("usb-sn:")
    lab["power_supply"] = {"type": "keysight_e36"}
    with pytest.raises(NotImplementedError, match="power_supply: keysight_e36"):
        build_station(lab)


def test_calcheck_identifies_the_storage_format(monkeypatch, capsys):
    """A fake module storing slope/offset like the real AOI module."""
    import struct

    from ddmi import cli
    from ddmi.hw import i2c_usbiss
    from ddmi.hw.regmap import load_regmap

    regmap = load_regmap()
    slopes = [60.1, 55.2, 70.3, 40.4, 61.5, 57.6, 69.7, 33.8]
    offsets = [100.0, -50.0, 20.0, 5.0, 0.0, 10.0, -10.0, 40.0]
    txmon = [235, 320, 298, 477, 220, 359, 262, 523]
    mem = {
        "R_TXMON": regmap["R_TXMON"].encode(txmon),
        "R_DTXP": regmap["R_DTXP"].encode([s * m + o for s, m, o in zip(slopes, txmon, offsets)]),
        "R_RSSI": regmap["R_RSSI"].encode([4] * 8),
        "R_DRXP": regmap["R_DRXP"].encode([0] * 8),
        "R_VCCADC": regmap["R_VCCADC"].encode(1330),
        "R_DVCC": regmap["R_DVCC"].encode(3.2450),
        # the layout found on the real module: little-endian, offsets first
        "DTXPCal_A": struct.pack("<8f", *(offsets[:4] + slopes[:4])),
        "DTXPCal_B": struct.pack("<8f", *(offsets[4:] + slopes[4:])),
        "DRXPCal_A": bytes(32), "DRXPCal_B": bytes(32),
        "VccCal": struct.pack("<2f", 0.0, 32450 / 1330),
    }
    by_addr = {(regmap[n].page, regmap[n].byte): data for n, data in mem.items()}

    class FakeBus:
        def __init__(self, port, **_kw):
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def read(self, page, byte, length):
            return by_addr.get((page, byte), bytes(length))[:length]

        def write(self, page, byte, data):
            pass

    monkeypatch.setattr(i2c_usbiss, "UsbIssBus", FakeBus)
    assert cli.main(["calcheck", "--port", "COM14"]) == 0
    out = capsys.readouterr().out
    tx_lines = [l for l in out.splitlines() if "MATCH" in l and "EC = CH1-4" in l or "MATCH" in l and "ED = CH1-4" in l]
    assert len(tx_lines) == 1, out
    assert "float32 little-endian" in tx_lines[0] and "EC = CH1-4" in tx_lines[0] and "offsets first" in tx_lines[0]
    vcc_matches = [l for l in out.split("VCC:")[1].splitlines() if "MATCH" in l]
    assert len(vcc_matches) == 1 and "little-endian" in vcc_matches[0] and "offset first" in vcc_matches[0]


def test_temperature_stabilization_matches_the_production_tool():
    """Readings from the production tool's screen (2026-10-14): same statuses, stable at the same reading."""
    from ddmi.tools.temp_stabilize import wait_for_stable_temperature

    temps = iter([33.075, 33.227, 33.285, 33.316, 33.375, 99.0])
    samples, lines = [], []
    stable = wait_for_stable_temperature(lambda: next(temps), interval_s=15, threshold_c=0.1, consecutive=3,
                                         log=lines.append, on_sample=samples.append, sleep=lambda _s: None)
    assert stable
    assert [s["status"] for s in samples] == ["initial", "Warming", "Stabilizing 1/3", "Stabilizing 2/3",
                                              "STABLE (3/3)"]
    assert [round(s["delta_c"], 3) for s in samples[1:]] == [0.152, 0.058, 0.031, 0.059]
    assert "you can now measure the power" in lines[-1]


def test_a_jump_resets_the_count_and_stop_ends_the_run():
    import threading

    from ddmi.tools.temp_stabilize import wait_for_stable_temperature

    temps = iter([20.0, 20.05, 20.08, 21.0, 21.02])
    samples, abort = [], threading.Event()

    def read():
        if len(samples) == 5:
            abort.set()
        return next(temps, 21.02)

    assert not wait_for_stable_temperature(read, log=lambda _l: None, on_sample=samples.append,
                                           abort=abort, sleep=lambda _s: None)
    assert [s["status"] for s in samples][:5] == ["initial", "Stabilizing 1/3", "Stabilizing 2/3",
                                                 "Warming (counter reset)", "Stabilizing 1/3"]


def test_tx_loss_waits_for_temperature_first(station_cfg, product):
    from ddmi.tools.tx_loss import calibrate_tx_loss

    events = []
    rows = calibrate_tx_loss(station_cfg, product, log=lambda line: events.append(line),
                             on_temp=lambda s: events.append(("temp", s["status"])))
    temps = [e for e in events if isinstance(e, tuple)]
    first_meter_line = next(i for i, e in enumerate(events) if isinstance(e, str) and "Tx Loss" in e)
    last_temp = max(i for i, e in enumerate(events) if isinstance(e, tuple))
    assert temps[-1][1].startswith("STABLE") and last_temp < first_meter_line
    assert len(rows) == 8
