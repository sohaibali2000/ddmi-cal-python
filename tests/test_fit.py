import pytest

from ddmi.calc.fit import apply_fit, two_point_fit
from ddmi.calc.units import lin_to_dbm, lin_to_volts


def test_tx_example():
    slope, offset = two_point_fit((5000, 5012), (12200, 20893))
    assert slope == pytest.approx(2.2057, abs=5e-5)
    assert offset == pytest.approx(-6016.5, abs=0.05)
    lin = apply_fit(slope, offset, 8000)
    assert round(lin) == 11629
    assert lin_to_dbm(lin) == pytest.approx(0.66, abs=0.005)


def test_rx_example():
    slope, offset = two_point_fit((25000, 6310), (4000, 1000))
    assert slope == pytest.approx(0.25286, abs=5e-6)
    assert offset == pytest.approx(-11.4, abs=0.05)
    lin = apply_fit(slope, offset, 10000)
    assert round(lin) == 2517
    assert lin_to_dbm(lin) == pytest.approx(-5.99, abs=0.005)


def test_vcc_example():
    slope, offset = two_point_fit((25040, 31300), (27760, 34700))
    assert slope == pytest.approx(1.25)
    assert offset == pytest.approx(0, abs=1e-6)
    assert lin_to_volts(apply_fit(slope, offset, 26400)) == pytest.approx(3.300)


def test_equal_x_is_rejected():
    with pytest.raises(ValueError):
        two_point_fit((100, 1), (100, 2))
