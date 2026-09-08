#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compara SET_COLOR com a sequencia FINISH_PLAY (animacao transitoria).

Hipotese: SET_COLOR pinta o LED mas nao substitui a animacao em execucao,
entao o motor de animacao repinta por cima (foi o que se observou: alternancia
entre a cor pedida e o branco padrao). Ja montar uma animacao e encerra-la com
FINISH_PLAY em vez de FINISH_SAVE deveria substituir a animacao corrente sem
gravar nada no armazenamento.

Este script mede a contagem de animacoes ANTES e DEPOIS. Se algo tiver sido
gravado por engano, ele remove e avisa.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import AwelcDevice, Elc  # noqa: E402
from awelc import constants as C  # noqa: E402

MAGENTA = (255, 0, 255)
CIANO = (0, 255, 255)
ZONES = list(C.DEFAULT_ZONES)


def hold(label: str, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        print(f"\r  {label} {end - time.monotonic():4.1f}s ", end="", flush=True)
        time.sleep(0.5)
    print(f"\r  {label} fim.                    ")


def main() -> int:
    print("=" * 64)
    print("TESTE: SET_COLOR  vs  animacao transitoria (FINISH_PLAY)")
    print("=" * 64)

    with AwelcDevice() as dev:
        elc = Elc(dev)
        before = elc.get_animation_count()
        print(f"animacoes gravadas ANTES: {before[0]} (primeira id=0x{before[1]:04x})\n")

        print("FASE 1 - SET_COLOR magenta (o metodo atual, que falha)")
        print("         esperado: alternar com o branco padrao")
        elc.set_color(ZONES, *MAGENTA)
        hold("observando", 7)

        print("\nFASE 2 - animacao transitoria CIANO via FINISH_PLAY")
        print("         esperado: ciano firme, sem alternancia")
        elc.play_transient(ZONES, *CIANO)
        hold("observando", 10)

        mid = elc.get_animation_count()
        print(f"\n  contagem durante o teste: {mid[0]}")

        print("\nFASE 3 - soltando o dispositivo (reanexa usbhid)")

    hold("observando apos soltar", 7)

    with AwelcDevice() as dev:
        elc = Elc(dev)
        after = elc.get_animation_count()
        print(f"\nanimacoes gravadas DEPOIS: {after[0]} (primeira id=0x{after[1]:04x})")

        if after[0] > before[0]:
            print("  !! a sequencia GRAVOU algo - removendo o id transitorio")
            elc.remove_animation(C.RUNNING_START)
            elc.remove_animation(C.RUNNING_FINISH)
            final = elc.get_animation_count()
            print(f"  contagem apos limpeza: {final[0]}")
        else:
            print("  OK: nada foi gravado no armazenamento")

    print("\n" + "=" * 64)
    print("RESPONDA:")
    print("  1) FASE 1 (magenta): alternou com o branco, como antes?")
    print("  2) FASE 2 (ciano): ficou FIRME, sem alternar?")
    print("  3) FASE 3: o que aconteceu ao soltar o dispositivo?")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
