# ddmi-cal-python

Python port of the LabVIEW station **800G_DDMI_Cal V6.8** (AWS 800G 2×FR4).

## Run it

Needs Python 3.11 or newer on Windows.

```
git clone https://github.com/sohaibali2000/ddmi-cal-python
cd ddmi-cal-python
python -m venv .venv
.venv\Scripts\python -m pip install pyyaml numpy pytest PyQt5==5.15.11
.venv\Scripts\python -m ddmi.ui
```

After the first setup, double-click `run_ui.bat`. Run the tests with
`.venv\Scripts\python -m pytest`.

## Status

Everything runs on a simulator. No hardware driver exists yet and nothing is
written to a real module.

| Part | State |
|---|---|
| Operator UI (`ddmi/ui`) | Working, runs the real flow |
| Test flow (`ddmi/flow.py`, `ddmi/steps`) | Done for V6.8 AWS DDMI Cal: start-up, MPD scan, TX-disable tuning, TX / RX / VCC cal and checks, temperature, ShowPF |
| Register map and write guard (`config/registers_aws.yaml`, `ddmi/hw`) | Done; unverified registers are refused on real hardware |
| Module + instrument simulator (`ddmi/sim`) | Done, with fault injection |
| Station tools (`ddmi/tools`) | Tx Loss calibration and RX table build |
| MES (`ddmi/mes`) | Offline stand-in with the check-in / save / check-out rules |
| Command line (`python -m ddmi`) | `run`, `tx-loss`, `rx-table` |
| Hardware drivers, real MES, Adjust Power | Not started |

## Command line

```
.venv\Scripts\python -m ddmi tx-loss            # Tx Loss calibration (golden sample)
.venv\Scripts\python -m ddmi rx-table           # build the RX attenuation table
.venv\Scripts\python -m ddmi run --sn AWS0001   # one DDMI Cal run
```

`run` takes `--slot`, `--dry-run`, `--allow-unverified` and, on the simulator,
`--fault dead_tx_lane | rx_nonlinear | sn_mismatch`. A new station needs `tx-loss`
and `rx-table` once per slot before `run` will start.

## Using the UI

- **Loss** first on a new station: run Tx Loss Calibration and the RX Attenuation Table for each slot.
  A test will not start without the RX table.
- **Dashboard**: one card per slot. Scan an SN (or leave it blank in the simulator) and press Start Test.
- **Slots**: results per channel, green = pass, red = fail.
- **Records / Runs**: saved runs with their logs, and pass/fail history.
- **Settings**: unlock with the engineer password (default `admin`). "Simulator fault
  injection" makes a run fail on purpose so the failure path can be seen.

Runs are saved under `results/` (a JSON and a CSV record per run, plus a log file with every
register read and write). That folder,
the local settings file `config/ui_settings.json` and the decoded LabVIEW XML
(`labview_xml/`) are not in git.

## Unconfirmed items

These came out of the LabVIEW decode but are not confirmed. Each one is a setting in
`config/`, not a fixed rule in code, and none may be relied on for a real module:

1. Operating-point fractions `[0.65, 0.56, 0.50, 0.44]`: whether `Peak Search.vi` uses
   x or 1 − x, of the peak or of the peak−valley span (`mpd_scan.op_point_mode`).
2. Calibration storage on pages EC/ED/EB: float32, big-endian and "EC = CH1–4" are assumptions.
3. AWS mode registers: page/byte order, the values written, and any password.
4. IBias scaling (×100 assumed), the IBiasPID restore value (15 or 3), TOPPID on-value.
5. The order of the DDMI_Cal parts (`flow.ddmi_cal_order`; TX, RX, VCC, temperature assumed).
6. Where the "real" module voltage for the VCC cal comes from (supply sense or `I2C Read Vcc.vi`).
7. Settle time after each tops_off step, RX wavelengths per channel, RX settle time.
8. The exact probing order of the TX-disable search, and the Adjust Power logic (not ported).
9. How LabVIEW writes the tab separator in the `Tx Loss` line of `system_Data.ini`
   (written here as `\09`; a literal tab and `\t` are also accepted when reading).
10. Instrument models and addresses, official MES limits, RX slope units (that limit is not applied).

Two things this port does that LabVIEW may not: it returns the module to a safe state
(TX enabled, reset, supply at nominal) after an abort or error, and it does not upload
an aborted run to MES.

A wrong Tx Loss cannot be caught by this station: the TX cal and its check use the same
loss, so the error cancels. Only a golden-sample check would show it.
