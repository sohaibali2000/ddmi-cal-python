import pytest

from ddmi.calc.rxtable import RxTable

CH1 = [(3, 1.26), (4, 2.26), (7, 5.26), (8, 6.26), (11, 9.27), (12, 10.27)]


@pytest.mark.parametrize("target_dbm, att", [(-2, 3.74), (-10, 11.73), (-6, 7.74)])
def test_ch1_examples(target_dbm, att):
    assert RxTable(CH1).att_for_power(target_dbm) == pytest.approx(att, abs=0.005)


def test_interpolation_uses_the_attenuation_step():
    table = RxTable([(2, 0.0), (4, 2.0)])   # 2 dB steps
    assert table.att_for_power(-1.0) == pytest.approx(3.0)


def test_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        RxTable(CH1).att_for_power(-15)
    with pytest.raises(ValueError):
        RxTable(CH1).att_for_power(0)


def test_from_file(tmp_path):
    path = tmp_path / "CH1_Rx_calibration.txt"
    path.write_text("".join(f"{a:.2f}\t{p:.2f}\t{p - a:.2f}\r\n" for a, p in CH1))
    assert RxTable.from_file(path).att_for_power(-6) == pytest.approx(7.74, abs=0.005)
