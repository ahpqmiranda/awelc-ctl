# SPDX-License-Identifier: GPL-3.0-or-later
"""Descoberta do dispositivo e posse da interface USB.

A diferenca principal em relacao ao upstream: `AwelcDevice` e um context
manager que SEMPRE reanexa o driver usbhid do kernel ao sair, inclusive em
caso de excecao. O upstream desanexa e nunca religa, deixando o teclado sem
driver ate o proximo replug.
"""
from __future__ import annotations

import usb.core
import usb.util

from .constants import PRODUCT_IDS, REPORT_ID, REPORT_LEN, VENDOR_ID, READ_COMMANDS


class AwelcError(RuntimeError):
    pass


class DeviceNotFound(AwelcError):
    pass


class PermissionDenied(AwelcError):
    pass


def find_device():
    """Devolve o objeto pyusb do controlador, ou levanta DeviceNotFound."""
    for pid in PRODUCT_IDS:
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=pid)
        if dev is not None:
            return dev
    raise DeviceNotFound(
        f"controlador nao encontrado "
        f"({VENDOR_ID:04x}:{'/'.join(f'{p:04x}' for p in PRODUCT_IDS)}). "
        "Confira com: lsusb | grep 187c"
    )


class AwelcDevice:
    """Posse temporaria da interface HID do controlador.

    Uso:
        with AwelcDevice() as dev:
            dev.command("2000")        # ELC_QUERY / GET_VERSION

    Ao sair do bloco o usbhid volta a ser dono da interface.
    """

    def __init__(self, read_only: bool = False):
        self.read_only = read_only
        self._dev = None
        self._intf = None
        self._reattach = False

    def __enter__(self):
        self._dev = find_device()
        self._intf = self._dev[0].interfaces()[0].bInterfaceNumber
        try:
            if self._dev.is_kernel_driver_active(self._intf):
                self._dev.detach_kernel_driver(self._intf)
                self._reattach = True
        except usb.core.USBError as exc:
            if getattr(exc, "errno", None) == 13:
                raise PermissionDenied(
                    "sem permissao para acessar o dispositivo USB.\n"
                    "Instale a regra udev (udev/60-awelc.rules) ou rode com "
                    "pkexec/sudo."
                ) from exc
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._reattach and self._dev is not None:
            try:
                usb.util.dispose_resources(self._dev)
                self._dev.attach_kernel_driver(self._intf)
            except Exception:
                # Nao mascara a excecao original do bloco with.
                pass
        self._dev = None
        return False

    # -- transporte -------------------------------------------------------

    @staticmethod
    def _build(fragment_hex: str) -> bytearray:
        payload = bytearray.fromhex(f"{REPORT_ID:02x}{fragment_hex}")
        if len(payload) > REPORT_LEN:
            raise AwelcError(
                f"comando de {len(payload)} bytes excede o report de {REPORT_LEN}"
            )
        payload += bytearray(REPORT_LEN - len(payload))
        return payload

    def _set_report(self, report: bytearray) -> None:
        # HID SetReport: classe | interface | OUT
        self._dev.ctrl_transfer(0x21, 9, 0x200 + 0, 0x00, report)

    def _get_report(self) -> bytearray:
        # HID GetReport: classe | interface | IN
        return bytearray(self._dev.ctrl_transfer(0xA1, 1, 0x100 + 0, 0x00, REPORT_LEN))

    def command(self, fragment_hex: str) -> bytearray:
        """Envia um comando e devolve a resposta crua de 33 bytes."""
        if self._dev is None:
            raise AwelcError("dispositivo nao aberto (use o context manager)")
        major = int(fragment_hex[:2], 16)
        if self.read_only and major not in READ_COMMANDS:
            raise AwelcError(
                f"instancia read_only recusou comando de escrita 0x{major:02x}"
            )
        self._set_report(self._build(fragment_hex))
        return self._get_report()


def is_unsupported(reply: bytearray) -> bool:
    """O firmware responde com payload todo 0xff para comandos que nao suporta."""
    return len(reply) > 3 and all(b == 0xFF for b in reply[3:])
