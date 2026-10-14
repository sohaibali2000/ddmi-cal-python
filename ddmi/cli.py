"""Command line: python -m ddmi run | tx-loss | rx-table."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config, stationfiles
from .runner import ROOT, run_slot
from .sim.sim_module import FAULTS
from .station import build_station
from .tools.rx_table import build_rx_table
from .tools.tx_loss import calibrate_tx_loss


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--station", default=str(config.CONFIG_DIR / "station_sim.yaml"))
    parser.add_argument("--product", default=str(config.CONFIG_DIR / "product_aws_2xfr4.yaml"))
    parser.add_argument("--slot", type=int, default=1)


def cmd_run(args) -> int:
    station_cfg = config.load_station(args.station)
    product = config.load_product(args.product)
    record = run_slot(
        station_cfg=station_cfg, product=product, slot=args.slot, scanned_sn=args.sn,
        user=args.user, fault=args.fault,
        dry_run=True if args.dry_run else None,
        allow_unverified=True if args.allow_unverified else None,
    )
    print()
    for item, cells in record["results"].items():
        values = "  ".join(f"{'' if c['ok'] else '!'}{c['value']}" for c in cells.values())
        print(f"{item:<18} {values}")
    print(f"\n{record['sn']}: {record['result']}"
          + (f" ({', '.join(record['failing_items'])})" if record["failing_items"] else ""))
    return 0 if record["result"] == "PASS" else 1


def cmd_tx_loss(args) -> int:
    station_cfg = config.load_station(args.station)
    product = config.load_product(args.product)
    station = build_station(station_cfg, slot=args.slot)
    if args.golden:
        golden = [float(v) for v in args.golden.split(",")]
        if len(golden) != product["channels"]:
            print(f"--golden needs {product['channels']} comma-separated dBm values")
            return 2
    elif station.sim_module is not None:
        golden = [station.sim_module.tx_out_dbm(ch) for ch in range(1, product["channels"] + 1)]
        print("Simulator: using the simulated module's true output power as the golden record")
    else:
        print("--golden is required on real hardware (the golden sample's recorded power per channel)")
        return 2
    losses = calibrate_tx_loss(station.bench, golden, warn_db=product["limits"]["tx_loss_warn_db"])
    path = stationfiles.tx_loss_path(args.slot, _path(station_cfg["tx_loss_file"]))
    stationfiles.write_tx_loss(path, losses)
    print(f"Saved to {path}")
    return 0


def cmd_rx_table(args) -> int:
    station_cfg = config.load_station(args.station)
    product = config.load_product(args.product)
    station = build_station(station_cfg, slot=args.slot)
    table = build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"])
    folder = stationfiles.rx_table_dir(args.slot, _path(station_cfg["rx_table_dir"]))
    stationfiles.write_rx_rows(folder, table)
    print(f"Saved to {folder}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ddmi", description="800G DDMI Cal (AWS 2xFR4)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run DDMI Cal on one module")
    _common(run)
    run.add_argument("--sn", default="", help="scanned serial number")
    run.add_argument("--user", default="UNKNOWN")
    run.add_argument("--dry-run", action="store_true", help="print every module write, send nothing")
    run.add_argument("--allow-unverified", action="store_true",
                     help="allow writes to registers marked UNVERIFIED on real hardware")
    run.add_argument("--fault", choices=FAULTS, default="none", help="simulator fault injection")
    run.set_defaults(func=cmd_run)

    tx = sub.add_parser("tx-loss", help="Tx Loss calibration with a golden sample")
    _common(tx)
    tx.add_argument("--golden", help="golden sample true power, 8 comma-separated dBm values")
    tx.set_defaults(func=cmd_tx_loss)

    rx = sub.add_parser("rx-table", help="build the RX attenuation table")
    _common(rx)
    rx.set_defaults(func=cmd_rx_table)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, NotImplementedError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
