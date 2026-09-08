#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Descobre o que cada efeito e cada parametro realmente produzem.

O upstream chama de "morph" uma rotacao dos canais RGB, que na pratica gera um
ciclo de cores (rainbow), nao um fade. E fixa tempo em TEMPO_MIN (0x01) sem
explicar o porque. Este script varre as combinacoes para descobrir:

  - o que muda com valores maiores de `tempo` (suavidade?)
  - o que muda com valores maiores de `duration` (velocidade?)
  - como e um fade/respiracao de verdade (cor <-> apagado)
  - o que o efeito PULSE (0x01) faz, nunca exercitado por ninguem

Tudo transitorio (FINISH_PLAY): nao grava nada no controlador.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import AwelcDevice, Elc  # noqa: E402
from awelc import constants as C  # noqa: E402
from awelc.protocol import Action  # noqa: E402

ZONES = list(C.DEFAULT_ZONES)
ROXO = (140, 0, 255)


def play(elc: Elc, actions: list[Action], zones=None) -> None:
    """Monta e toca uma animacao transitoria com as acoes dadas."""
    zones = zones or ZONES
    elc.remove_animation(C.RUNNING_START)
    elc.start_new_animation(C.RUNNING_START)
    elc.start_series(zones)
    elc.add_actions(actions)
    elc.finish_play_animation(C.RUNNING_START)


def watch(label: str, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        print(f"\r    {label}  {end - time.monotonic():4.1f}s ", end="", flush=True)
        time.sleep(0.5)
    print(f"\r    {label}  fim.                                   ")


def main() -> int:
    r, g, b = ROXO
    print("=" * 66)
    print("MAPEAMENTO DE EFEITOS - observe o teclado e anote cada passo")
    print("=" * 66)

    with AwelcDevice() as dev:
        elc = Elc(dev)
        before = elc.get_animation_count()
        print(f"animacoes gravadas antes: {before[0]}\n")

        print("[1] MORPH com rotacao de canal, tempo=1  (o 'rainbow' atual)")
        play(elc, [
            Action(C.EFFECT_MORPH, 0x00FF, 0x01, r, g, b),
            Action(C.EFFECT_MORPH, 0x00FF, 0x01, g, b, r),
            Action(C.EFFECT_MORPH, 0x00FF, 0x01, b, r, g),
        ])
        watch("rainbow tempo=1", 9)

        print("\n[2] MESMA coisa, tempo=64  (tempo controla suavidade?)")
        play(elc, [
            Action(C.EFFECT_MORPH, 0x00FF, 0x40, r, g, b),
            Action(C.EFFECT_MORPH, 0x00FF, 0x40, g, b, r),
            Action(C.EFFECT_MORPH, 0x00FF, 0x40, b, r, g),
        ])
        watch("rainbow tempo=64", 9)

        print("\n[3] MESMA coisa, tempo=255  (o maximo)")
        play(elc, [
            Action(C.EFFECT_MORPH, 0x00FF, 0xFF, r, g, b),
            Action(C.EFFECT_MORPH, 0x00FF, 0xFF, g, b, r),
            Action(C.EFFECT_MORPH, 0x00FF, 0xFF, b, r, g),
        ])
        watch("rainbow tempo=255", 9)

        print("\n[4] FADE de verdade: roxo <-> APAGADO (respiracao), tempo=64")
        play(elc, [
            Action(C.EFFECT_MORPH, 0x00FF, 0x40, r, g, b),
            Action(C.EFFECT_MORPH, 0x00FF, 0x40, 0, 0, 0),
        ])
        watch("respiracao", 10)

        print("\n[5] FADE lento: mesma coisa com duration=0x0800")
        play(elc, [
            Action(C.EFFECT_MORPH, 0x0800, 0x40, r, g, b),
            Action(C.EFFECT_MORPH, 0x0800, 0x40, 0, 0, 0),
        ])
        watch("respiracao lenta", 10)

        print("\n[6] Efeito PULSE (0x01), nunca exercitado por ninguem")
        play(elc, [Action(C.EFFECT_PULSE, 0x00FF, 0x40, r, g, b)])
        watch("pulse", 9)

        print("\n[7] volta ao roxo fixo")
        play(elc, [Action(C.EFFECT_COLOR, C.DURATION_MAX, 0x01, r, g, b)])
        watch("estatico", 4)

        after = elc.get_animation_count()
        print(f"\nanimacoes gravadas depois: {after[0]}", end="")
        print("  (inalterado)" if after[0] == before[0] else "  !! MUDOU")

    print("\n" + "=" * 66)
    print("Para cada passo, anote: piscou seco, degradê suave, ou nada mudou?")
    print("  [1] rainbow tempo=1     [2] tempo=64        [3] tempo=255")
    print("  [4] respiracao          [5] respiracao lenta")
    print("  [6] PULSE               [7] estatico")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
