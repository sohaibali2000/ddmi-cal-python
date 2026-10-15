"""Find a serial port by something that survives replugging the USB hub.

COM numbers change whenever Windows re-enumerates the hub; USB serial numbers do not,
and neither does a device's position inside the hub. A resource can be:
  "COM14"              that COM port, as is
  "usb-sn:00071351"    the port whose USB serial number is 00071351
  "usb-loc:.4.4.1"     the port whose USB location ends with .4.4.1 (for CH340 adapters,
                       which have no serial number)
"""
from __future__ import annotations


def _ports():
    import serial.tools.list_ports   # pyserial

    return list(serial.tools.list_ports.comports())


def resolve_port(resource: str, ports=None) -> str:
    resource = str(resource).strip()
    kind, _, value = resource.partition(":")
    if kind.lower() not in ("usb-sn", "usb-loc"):
        return resource
    ports = _ports() if ports is None else ports
    if kind.lower() == "usb-sn":
        matches = [p for p in ports if (p.serial_number or "").lower() == value.lower()]
    else:
        matches = [p for p in ports if (p.location or "").endswith(value)]
    if len(matches) == 1:
        return matches[0].device
    found = ", ".join(f"{p.device} (SN {p.serial_number or '-'}, at {p.location or '-'})" for p in ports) or "none"
    if not matches:
        raise LookupError(f"no serial port matches {resource}. Connected: {found}")
    raise LookupError(f"{resource} matches more than one port: {', '.join(p.device for p in matches)}")
