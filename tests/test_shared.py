import threading

from ddmi.hw import shared
from ddmi.results import limits_from


class _Dev:
    def __init__(self):
        self.closed, self.active, self.overlaps = False, 0, 0

    def work(self):
        self.active += 1
        if self.active > 1:
            self.overlaps += 1
        threading.Event().wait(0.002)
        self.active -= 1
        return "ok"

    def close(self):
        self.closed = True


def test_two_slots_share_one_instrument_closed_by_the_last():
    opened = []
    factory = lambda: opened.append(_Dev()) or opened[-1]
    a, release_a = shared.acquire("test:dev", factory)
    b, release_b = shared.acquire("test:dev", factory)
    assert len(opened) == 1 and shared.users("test:dev") == 2
    release_a()
    assert not opened[0].closed
    release_b()
    release_b()                                       # releasing twice is harmless
    assert opened[0].closed and shared.users("test:dev") == 0


def test_calls_from_two_slots_never_overlap():
    dev = _Dev()
    a, rel_a = shared.acquire("test:lock", lambda: dev)
    b, rel_b = shared.acquire("test:lock", lambda: dev)
    threads = [threading.Thread(target=lambda h=h: [h.work() for _ in range(30)]) for h in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rel_a(), rel_b()
    assert dev.overlaps == 0


def test_mes_spec_limits_by_item_or_production_column_name():
    product = {"dtxp_db": [-0.5, 0.5]}
    spec = {"dtxp_db": [-0.4, 0.4], "Txp_Slope": [10, 100], "Power(dBm)": [-2, 5], "Tops_Mid": [0.2, 3]}
    limits = limits_from(product, spec)
    assert limits["dTxP(dB)"] == (-0.4, 0.4)          # config key
    assert limits["Txp_Slope"] == (10, 100)           # our item name
    assert limits["TxP(dBm)"] == (-2, 5)              # production column name
    assert limits["Tops_Mid(V)"] == (0.2, 3)
