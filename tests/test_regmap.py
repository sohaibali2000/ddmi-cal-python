import pytest

from ddmi.hw.module import Module, UnverifiedWriteError
from ddmi.hw.regmap import load_regmap


@pytest.fixture(scope="module")
def regmap():
    return load_regmap()


class FakeBus:
    def __init__(self):
        self.mem, self.writes = {}, []

    def read(self, page, byte, length):
        return self.mem.get((page, byte), bytes(length))

    def write(self, page, byte, data):
        self.mem[(page, byte)] = bytes(data)
        self.writes.append((page, byte, bytes(data)))

    @property
    def data_writes(self):
        """Writes other than the vendor password entry."""
        return [w for w in self.writes if w[1] != 0x7A]


def test_addresses_match_the_build_map(regmap):
    assert regmap["R_TXMON"].address == "F3h/0x86/16"
    assert regmap["R_DTXP"].address == "11h/0x9A/16"
    assert regmap["Vtops"].address == "E9h/0xA0/16"
    assert regmap["VccCal"].address == "EBh/0x90/8"
    assert regmap.at(0xF3, 0xA6).name == "R_RSSI"


def test_temperature(regmap):
    reg = regmap["R_DTEMP"]
    assert reg.decode(bytes.fromhex("2A80")) == 42.5
    assert reg.decode(bytes.fromhex("FF00")) == -1.0
    assert reg.encode(42.5) == bytes.fromhex("2A80")


def test_dvcc_is_100_microvolt_units(regmap):
    assert regmap["R_DVCC"].decode((33000).to_bytes(2, "big")) == pytest.approx(3.3)


def test_vendor_monitors_are_little_endian(regmap):
    # confirmed on an AOI module: RSSI of a dark receiver reads 04 00, i.e. 4 counts
    assert regmap["R_RSSI"].decode(bytes.fromhex("0400") * 8) == [4] * 8
    assert regmap["R_VCCADC"].decode(bytes.fromhex("3205")) == 1330
    assert regmap["R_TXMON"].encode([235] * 8)[:2] == bytes.fromhex("eb00")


def test_password_register(regmap):
    reg = regmap["W_Password"]
    assert reg.address == "00h/0x7A/4" and reg.verified
    assert reg.encode(regmap.value("vendor_password")) == b"AOIW"


def test_u16_array_is_big_endian(regmap):
    reg = regmap["R_DTXP"]
    data = reg.encode([1850, 2, 3, 4, 5, 6, 7, 65535])
    assert data[:2] == bytes.fromhex("073A") and data[-2:] == b"\xff\xff"
    assert reg.decode(data) == [1850, 2, 3, 4, 5, 6, 7, 65535]


def test_vtops_encoding(regmap):
    reg = regmap["Vtops"]
    data = reg.encode([1.47] * 8)
    assert int.from_bytes(data[:2], "big") == 1204
    assert reg.decode(data)[0] == pytest.approx(1.47, abs=0.001)


def test_ibias_scaling(regmap):
    reg = regmap["IBias"]
    assert int.from_bytes(reg.encode([60, 60, 180, 180])[:2], "big") == 6000
    assert reg.decode(reg.encode([60, 60, 180, 180])) == [60, 60, 180, 180]


def test_cal_values_round_trip(regmap):
    reg = regmap["VccCal"]
    assert reg.decode(reg.encode([1.25, -6016.5])) == [1.25, -6016.5]


def test_information_splits_into_pn_rev_sn(regmap):
    reg = regmap["R_Information"]
    text = reg.decode(reg.encode("PN-ABC".ljust(16) + "01" + "SN123".ljust(16)))
    assert text[:16].strip() == "PN-ABC" and text[18:34].strip() == "SN123"


def test_wrong_length_is_rejected(regmap):
    with pytest.raises(ValueError):
        regmap["R_TXMON"].decode(b"\x00\x01")
    with pytest.raises(ValueError):
        regmap["R_TXMON"].encode([1, 2, 3])


def test_confidence_flags(regmap):
    assert regmap["W_Reset"].verified and regmap["Vtops"].verified
    for name in ("IBias", "W_IBiasPID", "W_TOPPID", "DTXPCal_A", "DRXPCal_B", "VccCal",
                 "AWS_Test_Mode", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "TempOffFunc"):
        assert not regmap[name].verified, name


def test_hardware_refuses_unverified_writes(regmap):
    bus = FakeBus()
    module = Module(bus, regmap, is_sim=False)
    with pytest.raises(UnverifiedWriteError, match="VccCal"):
        module.write("VccCal", [1.25, 0.0])
    assert bus.writes == []
    module.write("W_TXDis", 0xFF)                      # verified registers still go through
    assert bus.writes == [(0x10, 0x82, b"\xff")]


def test_allow_unverified_lets_the_write_through(regmap):
    bus = FakeBus()
    Module(bus, regmap, is_sim=False, allow_unverified=True).write("VccCal", [1.25, 0.0])
    assert len(bus.data_writes) == 1


def test_simulator_accepts_unverified_writes(regmap):
    bus = FakeBus()
    Module(bus, regmap, is_sim=True).write("VccCal", [1.25, 0.0])
    assert len(bus.data_writes) == 1


def test_dry_run_logs_but_sends_nothing(regmap):
    bus, lines = FakeBus(), []
    module = Module(bus, regmap, is_sim=False, dry_run=True, log=lines.append)
    module.write("W_TXDis", 0xFF)
    assert bus.writes == []
    assert "dry-run" in lines[0] and "W_TXDis" in lines[0]


def test_dry_run_still_refuses_unverified_on_hardware(regmap):
    with pytest.raises(UnverifiedWriteError):
        Module(FakeBus(), regmap, is_sim=False, dry_run=True).write("IBias", [60] * 4)


def test_write_cal_splits_channels_across_the_two_pages(regmap):
    bus = FakeBus()
    module = Module(bus, regmap, is_sim=True)
    module.write_cal("DTXPCal", [1, 2, 3, 4, 5, 6, 7, 8], [10, 20, 30, 40, 50, 60, 70, 80])
    assert [(p, b) for p, b, _ in bus.data_writes] == [(0xEC, 0xC0), (0xED, 0xC0)]
    assert module.read_cal("DTXPCal") == ([1, 2, 3, 4, 5, 6, 7, 8], [10, 20, 30, 40, 50, 60, 70, 80])


def test_vendor_password_is_entered_before_the_first_vendor_page_and_after_a_reset(regmap):
    bus = FakeBus()
    module = Module(bus, regmap, is_sim=False)
    module.read("R_DTEMP")                      # lower page: no password needed
    module.read("R_DTXP")                       # standard page 11h: no password needed
    assert bus.writes == []
    module.read("R_TXMON")                      # vendor page F3h
    module.read("R_RSSI")
    assert bus.writes == [(0x00, 0x7A, b"AOIW")]
    module.write("W_Reset", 0x08)               # a reset locks the vendor pages again
    module.read("R_TXMON")
    assert [w for w in bus.writes if w[1] == 0x7A] == [(0x00, 0x7A, b"AOIW")] * 2
