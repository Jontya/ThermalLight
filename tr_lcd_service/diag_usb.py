"""
USB diagnostic — dumps endpoints and strings for VID 87AD / PID 70DB.

Usage:
    python diag_usb.py

Requires:
    pip install pyusb
    libusb-1.0.dll in this directory (get from libusb GitHub releases)
"""

import os
import sys

try:
    import usb.core
    import usb.util
    import usb.backend.libusb1
except ImportError:
    print("ERROR: pyusb not installed. Run: pip install pyusb libusb")
    sys.exit(1)

# Try bundled libusb package first, fall back to DLL next to this script
def _find_libusb(_):
    try:
        import libusb
        return libusb.dll._name
    except Exception:
        pass
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'libusb-1.0.dll')
    return local if os.path.exists(local) else None

backend = usb.backend.libusb1.get_backend(find_library=_find_libusb)
if backend is None:
    print("ERROR: libusb not found. Run: pip install libusb")
    sys.exit(1)

VID = 0x87AD
PID = 0x70DB

dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
if dev is None:
    print(f"ERROR: Device VID_{VID:04X}&PID_{PID:04X} not found.")
    print("       Check libusb-1.0.dll is in the same folder as this script.")
    sys.exit(1)

def get_str(dev, idx):
    try:
        return usb.util.get_string(dev, idx) if idx else ''
    except Exception:
        return '<error>'

print(f"Found device VID=0x{VID:04X} PID=0x{PID:04X}")
print(f"  Manufacturer : {get_str(dev, dev.iManufacturer)}")
print(f"  Product      : {get_str(dev, dev.iProduct)}")
print(f"  Serial       : {get_str(dev, dev.iSerialNumber)}")
print(f"  bNumConfigs  : {dev.bNumConfigurations}")

for cfg in dev:
    print(f"\nConfiguration {cfg.bConfigurationValue}  (numInterfaces={cfg.bNumInterfaces})")
    for intf in cfg:
        print(f"  Interface {intf.bInterfaceNumber}  alt={intf.bAlternateSetting}"
              f"  class=0x{intf.bInterfaceClass:02X}"
              f"  sub=0x{intf.bInterfaceSubClass:02X}"
              f"  proto=0x{intf.bInterfaceProtocol:02X}"
              f"  numEP={intf.bNumEndpoints}")
        for ep in intf:
            direction = 'IN ' if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else 'OUT'
            ep_type   = {0: 'CTRL', 1: 'ISO', 2: 'BULK', 3: 'INT '}.get(
                usb.util.endpoint_type(ep.bmAttributes), '?   ')
            print(f"    EP 0x{ep.bEndpointAddress:02X}  {ep_type} {direction}  maxpkt={ep.wMaxPacketSize}"
                  f"  interval={ep.bInterval}")
