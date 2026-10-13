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
| Operator UI (`ddmi/ui`) | Working: Dashboard, Settings, Loss, RX table popup, Records, Slots, Runs |
| Test flow behind the UI | Stand-in (`ddmi/ui/sim_worker.py`): right step order and maths, made-up module data |
| Config files and loader (`config/`, `ddmi/config.py`) | Done, validated on load |
| Calibration maths (`ddmi/calc`) | Done, unit-tested against the build map's worked examples |
| Register map, write guard, module simulator, real steps, MES | Not started |
| Hardware drivers | Not started |

## Using the UI

- **Dashboard**: one card per slot. Scan an SN (or leave it blank in the simulator) and press Start Test.
- **Slots**: results per channel, green = pass, red = fail.
- **Loss**: Tx Loss per channel, Tx Loss Calibration, and the RX Attenuation Table.
- **Records / Runs**: saved runs with their logs, and pass/fail history.
- **Settings**: unlock with the engineer password (default `admin`). "Simulator fault
  injection" makes a run fail on purpose so the failure path can be seen.

Runs are saved under `results/` (records as JSON, one log file per run). That folder,
the local settings file `config/ui_settings.json` and the decoded LabVIEW XML
(`labview_xml/`) are not in git.

## Unconfirmed items

These came out of the LabVIEW decode but are not confirmed, so they live in config
and must not be relied on for a real module:

1. Operating-point fractions `[0.65, 0.56, 0.50, 0.44]`: whether `Peak Search.vi` uses
   x or 1 − x, of the peak or of the peak−valley span (`mpd_scan.op_point_mode`).
2. Calibration storage format on pages EC/ED/EB (float32? byte order? which page holds CH1–4).
3. AWS mode registers: page/byte order, and any password.
4. IBias scaling, the IBiasPID restore value (15 or 3), TOPPID values.
5. The Adjust Power logic in `Item_Test.vi`.
6. How LabVIEW writes the tab separator in the `Tx Loss` line of `system_Data.ini`
   (written here as `\09`; a literal tab and `\t` are also accepted when reading).
7. Instrument models and addresses, official MES limits, RX slope units.
