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
                                 golden_dbm=golden, stabilize=False if args.no_stabilize else None)
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
    from .hw.ports import resolve_port
    from .hw.regmap import load_regmap

    regmap = load_regmap()
    ports = args.port or ["COM13", "COM14", "COM15", "COM16"]
    for port in ports:
        try:
            with UsbIssBus(resolve_port(port), log=print) as bus:
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


def cmd_calcheck(args) -> int:
    """Read-only: decode the calibration pages (EC/ED/EB) in every candidate format and test
    each against the module's own DDMI readings. Answers how slope/offset are stored."""
    import struct

    from .hw.i2c_usbiss import UsbIssBus
    from .hw.module import Module
    from .hw.ports import resolve_port
    from .hw.regmap import load_regmap

    def candidates(raw: bytes, count: int) -> dict:
        """count values from raw bytes in each plausible encoding."""
        out = {}
        if len(raw) >= 4 * count:
            out["float32 big-endian"] = list(struct.unpack(f">{count}f", raw[:4 * count]))
            out["float32 little-endian"] = list(struct.unpack(f"<{count}f", raw[:4 * count]))
            out["int32 big-endian /256"] = [v / 256 for v in struct.unpack(f">{count}i", raw[:4 * count])]
        return out

    regmap = load_regmap()
    with UsbIssBus(resolve_port(args.port)) as bus:
        module = Module(bus, regmap, is_sim=False)
        try:
            txmon, rssi, vccadc = module.read("R_TXMON"), module.read("R_RSSI"), module.read("R_VCCADC")
            dtxp, drxp, dvcc = module.read("R_DTXP"), module.read("R_DRXP"), module.read("R_DVCC")
            pages = {name: bus.read(regmap[name].page, regmap[name].byte, regmap[name].length)
                     for name in ("DTXPCal_A", "DTXPCal_B", "DRXPCal_A", "DRXPCal_B", "VccCal")}
        finally:
            bus.write(0, 0x7F, bytes([0]))
    for name, raw in pages.items():
        print(f"{name} raw: {raw.hex(' ')}")
    print(f"TXMON {txmon}  DDMI TxP (0.1 uW) {dtxp}")
    print(f"RSSI {rssi}  DDMI RxP (0.1 uW) {drxp}")
    print(f"VCC ADC {vccadc}  DDMI VCC {dvcc:.4f} V")

    def report(label, a_raw, b_raw, monitor, ddmi):
        print(f"\n{label}: does slope * monitor + offset reproduce the module's DDMI value?")
        for order in ("EC = CH1-4", "ED = CH1-4"):
            first, second = (a_raw, b_raw) if order == "EC = CH1-4" else (b_raw, a_raw)
            for fmt in candidates(first, 8):
                a, b = candidates(first, 8)[fmt], candidates(second, 8)[fmt]
                for layout in ("slopes first", "offsets first"):
                    if layout == "slopes first":
                        slopes, offsets = a[:4] + b[:4], a[4:] + b[4:]
                    else:
                        slopes, offsets = a[4:] + b[4:], a[:4] + b[:4]
                    pred = [s * m + o for s, m, o in zip(slopes, monitor, offsets)]
                    err = [abs(p - d) / max(d, 1) for p, d in zip(pred, ddmi)]
                    ok = all(e < 0.02 for e in err)
                    print(f"  {fmt:22} {order}, {layout:13}: {'MATCH' if ok else 'no   '}  "
                          f"slopes {[round(s, 3) for s in slopes]}")

    report("TX", pages["DTXPCal_A"], pages["DTXPCal_B"], txmon, dtxp)
    if any(drxp):
        report("RX", pages["DRXPCal_A"], pages["DRXPCal_B"], rssi, drxp)
    else:
        print("\nRX: DDMI RxP is 0 (no light on the receiver), so the RX format cannot be checked this way.")
    print("\nVCC: does slope * ADC + offset reproduce DDMI VCC (in 100 uV)?")
    for fmt, vals in candidates(pages["VccCal"], 2).items():
        for layout, (slope, offset) in (("slope first", vals), ("offset first", vals[::-1])):
            pred = slope * vccadc + offset
            print(f"  {fmt:22} {layout:12}: slope {slope:.5g}, offset {offset:.5g} -> {pred / 10000:.4f} V "
                  f"({'MATCH' if abs(pred - dvcc * 10000) < 0.02 * dvcc * 10000 else 'no'})")
    return 0


def cmd_regcheck(args) -> int:
    """Read-only: on a module LabVIEW V6.8 has just finished, read every register the run
    writes but whose address/value is not confirmed yet, and compare it with what V6.8
    leaves behind (ShowPF for AWS DDMI CAL; Adjust Power)."""
    from .hw.i2c_usbiss import UsbIssBus
    from .hw.module import Module
    from .hw.ports import resolve_port
    from .hw.regmap import load_regmap

    # Adjust Power (Item_Test.vi V6.8, LC-2*FR4): default [280, 280, 410, 450], then +20 / -30
    allowed = [{d - 30, d, d + 20} for d in (280, 280, 410, 450)]
    regmap = load_regmap()
    with UsbIssBus(resolve_port(args.port)) as bus:
        module = Module(bus, regmap, is_sim=False)
        try:
            sn = module.read("R_Information")[18:34].strip()
            values = {name: module.read(name) for name in (
                "TempOffFunc", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "AWS_Test_Mode",
                "W_IBiasPID", "W_TOPPID", "W_Bias_setpoint", "W_Bias_setpointHT", "W_Bias_setpointLT")}
        finally:
            bus.write(0, 0x7F, bytes([0]))
    rt, ht, lt = values["W_Bias_setpoint"], values["W_Bias_setpointHT"], values["W_Bias_setpointLT"]
    checks = [
        ("TempOffFunc  FEh:95h", values["TempOffFunc"], "59 (ShowPF disables temp offset)", values["TempOffFunc"] == 59),
        ("TestMode     EEh:83h", values["TestMode"], "0xCC (ShowPF exits test mode)", values["TestMode"] == 0xCC),
        ("DSP_TestMode CFh:81h", values["DSP_TestMode"], "0 (ShowPF checks it)", values["DSP_TestMode"] == 0),
        ("DSP_OpMode   CFh:86h", values["DSP_OperatingMode"], "0 (ShowPF checks it)", values["DSP_OperatingMode"] == 0),
        ("AWS_Test_Mode EEh:80h", values["AWS_Test_Mode"], "0 (Set_Temp, DDMI CAL, AWS)", values["AWS_Test_Mode"] == 0),
        ("Bias_setpoint RT E9h:D2h", rt, "280/280/410/450 +20 or -30",
         all(v in a for v, a in zip(rt, allowed))),
        ("Bias_setpoint HT E9h:B0h", ht, "same as RT (Adjust Power writes all three)", ht == rt),
        ("Bias_setpoint LT E9h:B8h", lt, "same as RT", lt == rt),
        ("W_IBiasPID   E9h:D0h", values["W_IBiasPID"], "information only (TX cal restores 15)", None),
        ("W_TOPPID     E8h:80h", values["W_TOPPID"], "information only", None),
    ]
    print(f"Module {sn}: registers after a LabVIEW V6.8 run (read only)\n")
    for label, value, expect, ok in checks:
        mark = "    " if ok is None else ("OK  " if ok else "DIFF")
        print(f"  {mark} {label:26} = {str(value):24} expected {expect}")
    diffs = [label for label, _, _, ok in checks if ok is False]
    print("\nAll match - these registers can be marked HW." if not diffs else
          f"\n{len(diffs)} differ - do not mark them HW yet: {', '.join(diffs)}")
    return 0 if not diffs else 1


def cmd_rx_settle(args) -> int:
    """Measure how long RSSI takes to settle after an attenuator step (RX settle, VERIFY).
    Writes only the attenuator; the module is only read. Needs light on both RX fibres."""
    import time

    from .station import build_station

    station_cfg = config.load_station(_path(args.station))
    station = build_station(station_cfg, dry_run=False, allow_unverified=False)
    att, module = station.bench.attenuator, station.module
    try:
        for start, end in ((args.low, args.high), (args.high, args.low)):
            for ch in att.physical_channels:
                att.set_channel_attenuation(ch, start)
            time.sleep(3.0)
            before = module.read("R_RSSI")
            for ch in att.physical_channels:
                att.set_channel_attenuation(ch, end)
            t0, trace = time.perf_counter(), []
            while time.perf_counter() - t0 < args.seconds:
                trace.append((time.perf_counter() - t0, module.read("R_RSSI")))
                time.sleep(0.05)
            final = trace[-1][1]
            print(f"\n{start} -> {end} dB: RSSI {before} -> {final}")
            for lane in range(8):
                tol = max(2, 0.02 * final[lane])
                settled = next((t for i, (t, r) in enumerate(trace)
                                if all(abs(x[1][lane] - final[lane]) <= tol for x in trace[i:])), None)
                print(f"  CH{lane + 1}: settled within 2 % after {settled:.2f} s" if settled is not None
                      else f"  CH{lane + 1}: not settled in {args.seconds} s")
        print("\nUse the slowest lane plus a margin as rx_cal.settle_s.")
    finally:
        station.close()
    return 0


def cmd_restore(args) -> int:
    """Write a module register backup (taken by every run before its first write) back."""
    import json

    from . import backup
    from .hw.i2c_usbiss import UsbIssBus
    from .hw.module import Module
    from .hw.ports import resolve_port
    from .hw.regmap import load_regmap

    saved = json.loads(Path(args.file).read_text(encoding="utf-8"))
    with UsbIssBus(resolve_port(args.port)) as bus:
        # Restoring the module's own earlier values, so unverified registers are allowed here.
        module = Module(bus, load_regmap(), is_sim=False, allow_unverified=True, log=print)
        sn = module.read("R_Information")[18:34].strip() if bus.read(0, 0x7F, 1)[0] == 0 else "?"
        print(f"Backup of SN {saved['sn']} taken {saved['time']}; module on {bus.port} is SN {sn}")
        if sn != saved["sn"] and not args.force:
            print("SN does not match the backup - not restoring (use --force to override)")
            return 2
        if not args.yes and input(f"Write {len(saved['registers'])} registers back? [y/N] ").strip().lower() != "y":
            print("Not restored")
            return 1
        try:
            names = backup.restore(module, saved["registers"])
            module.write_value("W_Reset", "reset")
        finally:
            bus.write(0, 0x7F, bytes([0]))
    print(f"Restored {', '.join(names)} and reset the module")
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

    try:
        table = build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], on_prompt=ask)
    finally:
        station.close()
    if table is None:
        print("Not saved")
        return 1
    folder = stationfiles.rx_table_dir(args.slot, _path(station_cfg["rx_table_dir"]))
    stationfiles.write_rx_rows(folder, table)
    print(f"Saved to {folder}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")    # a Windows console can't show every character (e.g. delta)
        except (AttributeError, ValueError):
            pass
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
    tx.add_argument("--no-stabilize", action="store_true",
                    help="skip waiting for the golden sample's temperature to settle")
    tx.set_defaults(func=cmd_tx_loss)

    probe = sub.add_parser("probe", help="read-only check of the modules on the USB-ISS adapters")
    probe.add_argument("--port", action="append", help="USB-ISS COM port (repeatable); default COM13-COM16")
    probe.add_argument("--monitors", action="store_true",
                       help="also read DDMI and raw monitors (page-select writes + vendor password; values read only)")
    probe.set_defaults(func=cmd_probe)

    calcheck = sub.add_parser("calcheck", help="read-only: work out how the module stores slope/offset")
    calcheck.add_argument("--port", default="usb-sn:00071351", help="DUT USB-ISS (COMx or usb-sn:...)")
    calcheck.set_defaults(func=cmd_calcheck)

    regcheck = sub.add_parser("regcheck", help="read-only: check unconfirmed registers on a module LabVIEW just finished")
    regcheck.add_argument("--port", default="usb-sn:00071345", help="DUT USB-ISS (COMx or usb-sn:...)")
    regcheck.set_defaults(func=cmd_regcheck)

    settle = sub.add_parser("rx-settle", help="measure RSSI settling after an attenuator step")
    settle.add_argument("--station", default=str(config.CONFIG_DIR / "station_lab.yaml"))
    settle.add_argument("--low", type=float, default=6.0, help="attenuation, dB")
    settle.add_argument("--high", type=float, default=12.0, help="attenuation, dB")
    settle.add_argument("--seconds", type=float, default=4.0, help="how long to watch RSSI")
    settle.set_defaults(func=cmd_rx_settle)

    rest = sub.add_parser("restore", help="write a module register backup back")
    rest.add_argument("--file", required=True, help="backup file from results/backups")
    rest.add_argument("--port", default="usb-sn:00071345", help="DUT USB-ISS (COMx or usb-sn:...)")
    rest.add_argument("--yes", action="store_true", help="restore without asking")
    rest.add_argument("--force", action="store_true", help="restore even if the module SN differs")
    rest.set_defaults(func=cmd_restore)

    rx = sub.add_parser("rx-table", help="build the RX attenuation table")
    _common(rx)
    rx.set_defaults(func=cmd_rx_table)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, NotImplementedError, FileNotFoundError, ImportError, RuntimeError,
            LookupError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
