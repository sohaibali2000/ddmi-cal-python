@echo off
rem Opens the app on the REAL lab station (config\station_lab.yaml).
cd /d "%~dp0"
set DDMI_STATION=config\station_lab.yaml
start "" ".venv\Scripts\pythonw.exe" -m ddmi.ui
