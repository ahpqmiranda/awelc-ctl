# SPDX-License-Identifier: GPL-3.0-or-later
"""Camada de protocolo ELC sobre AwelcDevice."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from . import constants as C
from .device import AwelcDevice, AwelcError, is_unsupported


@dataclass(frozen=True)
class Action:
    """Uma acao dentro de uma animacao: efeito + duracao + tempo + cor."""

    effect: int
    duration: int
    tempo: int
    red: int
    green: int
    blue: int

    def to_hex(self) -> str:
        return (
            f"{self.effect:02x}{self.duration:04x}{self.tempo:04x}"
            f"{self.red:02x}{self.green:02x}{self.blue:02x}"
        )


@dataclass(frozen=True)
class DeviceInfo:
    firmware: tuple[int, int, int]
    platform_id: bytes
    platform_variant: int
    animation_count: int
    first_animation_id: int

    @property
    def firmware_str(self) -> str:
        return ".".join(str(x) for x in self.firmware)


def _zones_hex(zones: Sequence[int]) -> str:
    return "".join(f"{z:02x}" for z in zones)


def _animation_major(animation_id: int) -> int:
    return (
        C.POWER_ANIMATION
        if animation_id in C.POWER_ANIMATION_RANGE
        else C.USER_ANIMATION
    )


class Elc:
    """Comandos do protocolo. Recebe um AwelcDevice ja aberto."""

    def __init__(self, dev: AwelcDevice):
        self.dev = dev

    # -- leitura ----------------------------------------------------------

    def get_version(self) -> tuple[int, int, int]:
        r = self.dev.command(f"{C.ELC_QUERY:02x}{C.GET_VERSION:02x}")
        return (r[3], r[4], r[5])

    def get_platform(self) -> tuple[bytes, int]:
        r = self.dev.command(f"{C.ELC_QUERY:02x}{C.GET_PLATFORM:02x}")
        return (bytes(r[3:5]), r[5])

    def get_animation_count(self) -> tuple[int, int]:
        """(quantidade, id da primeira animacao)."""
        r = self.dev.command(f"{C.ELC_QUERY:02x}{C.GET_ANIMATION_COUNT:02x}")
        return (
            int.from_bytes(r[3:5], "big"),
            int.from_bytes(r[5:7], "big"),
        )

    def get_animation_by_id(self, animation_id: int) -> bytearray | None:
        """NAO suportado no firmware 1.1.7 - devolve None nesse caso."""
        r = self.dev.command(
            f"{C.ELC_QUERY:02x}{C.GET_ANIMATION_BY_ID:02x}{animation_id:04x}"
        )
        return None if is_unsupported(r) else r

    def info(self) -> DeviceInfo:
        fw = self.get_version()
        pid, variant = self.get_platform()
        count, first = self.get_animation_count()
        return DeviceInfo(fw, pid, variant, count, first)

    # -- escrita ----------------------------------------------------------

    def remove_animation(self, animation_id: int) -> bytearray:
        major = _animation_major(animation_id)
        return self.dev.command(
            f"{major:02x}{C.REMOVE:04x}{animation_id:04x}"
        )

    def start_new_animation(self, animation_id: int) -> bytearray:
        major = _animation_major(animation_id)
        return self.dev.command(
            f"{major:02x}{C.START_NEW:04x}{animation_id:04x}"
        )

    def finish_save_animation(self, animation_id: int) -> bytearray:
        major = _animation_major(animation_id)
        return self.dev.command(
            f"{major:02x}{C.FINISH_SAVE:04x}{animation_id:04x}"
        )

    def set_default_animation(self, animation_id: int) -> bytearray:
        major = _animation_major(animation_id)
        return self.dev.command(
            f"{major:02x}{C.SET_DEFAULT:04x}{animation_id:04x}"
        )

    def start_series(self, zones: Sequence[int], loop: int = 1) -> bytearray:
        return self.dev.command(
            f"{C.START_SERIES:02x}{loop:02x}{len(zones):04x}{_zones_hex(zones)}"
        )

    def add_actions(self, actions: Iterable[Action]) -> bytearray:
        actions = list(actions)
        if not 1 <= len(actions) <= 3:
            raise AwelcError("ADD_ACTION aceita de 1 a 3 acoes por comando")
        fragment = f"{C.ADD_ACTION:02x}" + "".join(a.to_hex() for a in actions)
        return self.dev.command(fragment)

    def set_color(self, zones: Sequence[int], r: int, g: int, b: int) -> bytearray:
        """Cor imediata, sem gravar animacao. Nao persiste."""
        return self.dev.command(
            f"{C.SET_COLOR:02x}{r:02x}{g:02x}{b:02x}"
            f"{len(zones):04x}{_zones_hex(zones)}"
        )

    def dim(self, zones: Sequence[int], level: int) -> bytearray:
        """level: 0 = brilho total, 100 = apagado."""
        if not C.BRIGHTNESS_MIN <= level <= C.BRIGHTNESS_MAX:
            raise AwelcError(f"nivel {level} fora de 0..100")
        return self.dev.command(
            f"{C.DIMMING:02x}{level:02x}{len(zones):04x}{_zones_hex(zones)}"
        )

    # -- composicoes ------------------------------------------------------

    def store_static(
        self,
        animation_id: int,
        zones: Sequence[int],
        r: int,
        g: int,
        b: int,
        duration: int = C.DURATION_MAX,
        tempo: int = C.TEMPO_MIN,
    ) -> None:
        """Grava uma cor estatica como animacao persistente do controlador."""
        self.remove_animation(animation_id)
        self.start_new_animation(animation_id)
        self.start_series(zones)
        self.add_actions([Action(C.EFFECT_COLOR, duration, tempo, r, g, b)])
        self.finish_save_animation(animation_id)
        self.set_default_animation(animation_id)

    def store_morph(
        self,
        animation_id: int,
        zones: Sequence[int],
        r: int,
        g: int,
        b: int,
        duration: int = 0x00FF,
        tempo: int = C.TEMPO_MIN,
    ) -> None:
        """Grava um ciclo morph rotacionando os canais RGB."""
        self.remove_animation(animation_id)
        self.start_new_animation(animation_id)
        self.start_series(zones)
        self.add_actions(
            [
                Action(C.EFFECT_MORPH, duration, tempo, r, g, b),
                Action(C.EFFECT_MORPH, duration, tempo, g, b, r),
                Action(C.EFFECT_MORPH, duration, tempo, b, r, g),
            ]
        )
        self.finish_save_animation(animation_id)
        self.set_default_animation(animation_id)

    def clear_all(self) -> int:
        """Remove todas as animacoes gravadas. Devolve quantas foram removidas."""
        removed = 0
        for aid in C.ANIMATION_NAMES:
            self.remove_animation(aid)
        count, first = self.get_animation_count()
        guard = 0
        while count and guard < 64:
            self.remove_animation(first)
            removed += 1
            guard += 1
            count, first = self.get_animation_count()
        return removed
