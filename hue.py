"""Control a Philips Hue Bluetooth light directly from the Mac (no bridge).

Usage:
  python hue.py scan                 # find Hue lights nearby, remember the strongest one
  python hue.py info                 # connect and print current state
  python hue.py on | off
  python hue.py bri 0-100            # brightness in percent
  python hue.py ct 2200-6500         # white colour temperature in kelvin
  python hue.py color '#ff6600'      # any hex colour

Must run from a process macOS lets use Bluetooth (Terminal.app or HueLights.app).
"""
import asyncio
import json
import pathlib
import struct
import sys

from bleak import BleakClient, BleakScanner

POWER = "932c32bd-0002-47a2-835a-a8d455b859dd"
BRIGHTNESS = "932c32bd-0003-47a2-835a-a8d455b859dd"
TEMPERATURE = "932c32bd-0004-47a2-835a-a8d455b859dd"
XY = "932c32bd-0005-47a2-835a-a8d455b859dd"
SIGNIFY_SERVICE = "0000fe0f-0000-1000-8000-00805f9b34fb"

CONFIG = pathlib.Path(__file__).with_name("hue_light.json")


def hex_to_xy(value):
    """Convert sRGB hex to CIE xy using the Hue wide-gamut matrix."""
    value = value.lstrip("#")
    rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in rgb]
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z or 1
    return x / total, y / total


def saved_address():
    return json.loads(CONFIG.read_text())["address"]


async def apply(target, address=None):
    """Push a target state to the light.

    target keys: on (bool), bri (0-100), ct (kelvin), color (hex). Missing keys are left alone.
    """
    async with BleakClient(address or saved_address(), timeout=20) as client:
        async def write(char, data):
            await client.write_gatt_char(char, data, response=True)

        if target.get("on") is False:
            await write(POWER, b"\x00")
            return
        await write(POWER, b"\x01")
        if "bri" in target:
            await write(BRIGHTNESS, bytes([max(1, min(254, round(target["bri"] / 100 * 254)))]))
        if "ct" in target:
            mired = max(153, min(454, round(1_000_000 / target["ct"])))
            await write(TEMPERATURE, struct.pack("<H", mired))
        if "color" in target:
            x, y = hex_to_xy(target["color"])
            await write(XY, struct.pack("<HH", round(x * 0xFFFF), round(y * 0xFFFF)))


async def scan():
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    lights = [
        (d, a) for d, a in found.values()
        if SIGNIFY_SERVICE in a.service_uuids or "hue" in (a.local_name or d.name or "").lower()
    ]
    if not lights:
        print("No Hue light found. Is it powered, and is the Hue app on the phone closed?")
        return
    for d, a in lights:
        print(f"{a.rssi:>4} dBm  {a.local_name or d.name}  {d.address}")
    best = max(lights, key=lambda x: x[1].rssi)[0]
    CONFIG.write_text(json.dumps({"address": best.address, "name": best.name}))
    print(f"Saved {best.name} ({best.address}) to {CONFIG.name}")


async def info():
    async with BleakClient(saved_address(), timeout=20) as client:
        power = await client.read_gatt_char(POWER)
        bri = await client.read_gatt_char(BRIGHTNESS)
        print(f"power={'on' if power[0] else 'off'} brightness={round(bri[0] / 254 * 100)}%")
        xy = struct.unpack("<HH", await client.read_gatt_char(XY))
        print(f"xy=({xy[0] / 0xFFFF:.3f}, {xy[1] / 0xFFFF:.3f})")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, arg = argv[0], (argv[1] if len(argv) > 1 else None)
    if cmd == "scan":
        asyncio.run(scan())
        return
    if cmd == "info":
        asyncio.run(info())
        return
    targets = {
        "on": lambda: {"on": True},
        "off": lambda: {"on": False},
        "bri": lambda: {"bri": float(arg)},
        "ct": lambda: {"ct": float(arg)},
        "color": lambda: {"color": arg},
    }
    if cmd not in targets:
        sys.exit(__doc__)
    asyncio.run(apply(targets[cmd]()))
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1:])
