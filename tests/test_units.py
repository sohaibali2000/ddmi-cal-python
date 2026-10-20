import pytest

from ddmi.calc import units


@pytest.mark.parametrize("dbm, lin", [(-2, 6310), (-10, 1000), (-3, 5012), (3.2, 20893)])
def test_dbm_to_lin(dbm, lin):
    assert units.dbm_to_lin(dbm) == lin


@pytest.mark.parametrize("lin, dbm", [(11629, 0.66), (2517, -5.99), (10000, 0.0)])
def test_lin_to_dbm(lin, dbm):
    assert units.lin_to_dbm(lin) == pytest.approx(dbm, abs=0.005)


def test_lin_to_dbm_of_zero_is_minus_infinity():
    assert units.lin_to_dbm(0) == float("-inf")


def test_volts():
    assert units.volts_to_lin(3.13) == pytest.approx(31300)
    assert units.lin_to_volts(33000) == pytest.approx(3.3)


@pytest.mark.parametrize("raw, temp", [(0x2A80, 42.5), (0xFF00, -1.0)])
def test_temperature(raw, temp):
    assert units.temp_from_raw(raw) == temp


def test_dbm_to_lin_exact_is_not_rounded_like_ddmi_cal_vi():
    assert units.dbm_to_lin_exact(-2) == pytest.approx(6309.573, abs=0.001)
    assert units.dbm_to_lin_exact(-10) == pytest.approx(1000.0)
