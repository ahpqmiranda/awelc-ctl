# SPDX-License-Identifier: GPL-3.0-or-later
"""awelc-ctl - controle do backlight RGB Alienware AW-ELC (187c:0550)."""

__version__ = "0.3.0"

from .device import AwelcDevice, AwelcError, DeviceNotFound, PermissionDenied
from .protocol import Action, DeviceInfo, Elc

__all__ = [
    "AwelcDevice", "AwelcError", "DeviceNotFound", "PermissionDenied",
    "Action", "DeviceInfo", "Elc", "__version__",
]
