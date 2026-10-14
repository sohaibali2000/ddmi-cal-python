"""Command line: python -m ddmi run | tx-loss | rx-table."""
from __future__ import annotations

import argparse
import math
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
    golden = None
    if args.golden:
        values = [float(v) for v in args.golden.split(",")]
        if len(values) != product["channels"]:
            print(f"--golden needs {product['channels']} comma-separated dBm values")
            return 2
        golden = dict(enumerate(values, start=1))
    try:
        rows = calibrate_tx_loss(station_cfg, product, slot=args.slot, component_id=args.component_id,
                                 golden_dbm=golden)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    path = stationfiles.tx_loss_path(args.slot, _path(station_cfg["tx_loss_file"]))
    if not args.yes and input(f"Save these Tx Loss values to {path}? [y/N] ").strip().lower() != "y":
        print("Not saved")
        return 1
    stationfiles.write_tx_loss(path, [row["loss_db"] for row in rows])
    print(f"Saved to {path}")
    return 0


def cmd_probe(args) -> int:
    """Read-only hardware check: is a module on this USB-ISS port, and what is it."""
    from .hw.i2c_usbiss import UsbIssBus
    from .hw.module import Module
    from .hw.regmap import load_regmap

    regmap = load_regmap()
    ports = args.port or ["COM13", "COM14", "COM15", "COM16"]
    for port in ports:
        try:
            with UsbIssBus(port, log=print) as bus:
                if not bus.present():
                    print(f"{port}: no module")
                    continue
                # Lower-page reads only, unless the module is already on page 00.
                module = Module(bus, regmap, is_sim=False)
                temp, vcc = module.read("R_DTEMP"), module.read("R_DVCC")
                ident = bus.read(0, 0x00, 1)[0]
                current_page = bus.read(0, 0x7F, 1)[0]
                line = f"{port}: identifier 0x{ident:02X}, {temp:.2f} C, {vcc:.3f} V"
                if current_page == 0:
                    info = module.read("R_Information")
                    line += f", PN {info[:16].strip()}, rev {info[16:18].strip()}, SN {info[18:34].strip()}"
                else:
                    line += f" (module is on page 0x{current_page:02X}; SN not read to avoid a page write)"
                print(line)
                if args.monitors:
                    # Page-select writes and the vendor password; values are read only.
                    try:
                        dbm = lambda lin: round(10 * math.log10(lin / 10000), 2) if lin > 0 else None
                        print(f"  DDMI TxP dBm  {[dbm(v) for v in module.read('R_DTXP')]}")
                        print(f"  DDMI RxP dBm  {[dbm(v) for v in module.read('R_DRXP')]}")
                        print(f"  DDMI bias raw {module.read('R_DBias')}")
                        txmon, mpd = module.read("R_TXMON"), module.read("R_MPD_OUT")
                        print(f"  TXMON         {txmon}")
                        print(f"  MPD_OUT       {mpd}")
                        print(f"  TXMON/MPD_OUT*1000 {[round(t / m * 1000) if m else None for t, m in zip(txmon, mpd)]}")
                        print(f"  tops_setpoint {module.read('tops_setpoint')}")
                        print(f"  RSSI          {module.read('R_RSSI')}")
                        print(f"  VCC ADC       {module.read('R_VCCADC')}")
                    finally:
                        bus.write(0, 0x7F, bytes([0]))      # leave the module on page 00
        except Exception as exc:
            print(f"{port}: {type(exc).__name__}: {exc}")
    return 0


def cmd_rx_table(args) -> int:
    station_cfg = config.load_station(args.station)
    product = config.load_product(args.product)
    station = build_station(station_cfg, slot=args.slot)
    def ask(message: str) -> bool:
        if station.sim_module is not None:
            print(message + "  [simulator: continuing]")
            return True
        return input(message + " Press Enter to continue, or type q to stop: ").strip().lower() != "q"

    table = build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], on_prompt=ask)
    if table is None:
        print("Not saved")
        return 1
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
    tx.add_argument("--component-id", default="",
                    help="golden sample Component ID / SN; its power is read from the MES database")
    tx.add_argument("--golden", help="golden power instead of MES: 8 comma-separated dBm values")
    tx.add_argument("--yes", action="store_true", help="save without asking")
    tx.set_defaults(func=cmd_tx_loss)

    probe = sub.add_parser("probe", help="read-only check of the modules on the USB-ISS adapters")
    probe.add_argument("--port", action="append", help="USB-ISS COM port (repeatable); default COM13-COM16")
    probe.add_argument("--monitors", action="store_true",
                       help="also read DDMI and raw monitors (page-select writes + vendor password; values read only)")
    probe.set_defaults(func=cmd_probe)

    rx = sub.add_parser("rx-table", help="build the RX attenuation table")
    _common(rx)
    rx.set_defaults(func=cmd_rx_table)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, NotImplementedError, FileNotFoundError, ImportError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
