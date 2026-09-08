#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostica por que a cor aplicada volta ao padrao anterior.

Duas hipoteses:
  H1 - a animacao gravada no controlador repinta por cima do SET_COLOR.
  H2 - o ciclo detach/reattach do usbhid reseta o controlador, que volta
       a tocar a animacao padrao.

O experimento separa as duas segurando o dispositivo ABERTO depois de aplicar
a cor. Se reverter com o dispositivo ainda aberto -> H1. Se so reverter no
momento em que soltamos -> H2.

Usa apenas SET_COLOR (0x27), que e volatil: NAO grava nem apaga animacoes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import AwelcDevice, Elc  # noqa: E402
from awelc import constants as C  # noqa: E402

MAGENTA = (255, 0, 255)
ZONES = list(C.DEFAULT_ZONES)


def countdown(label: str, seconds: float, step: float = 1.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        left = end - time.monotonic()
        print(f"\r  {label} {left:4.1f}s ", end="", flush=True)
        time.sleep(min(step, max(left, 0.05)))
    print(f"\r  {label} concluida.        ")


def main() -> int:
    print("=" * 62)
    print("DIAGNOSTICO: por que a cor volta ao padrao")
    print("=" * 62)
    print("Observe o teclado durante TODAS as fases e anote o que acontece.\n")
    print("Cor de teste: MAGENTA (255,0,255) em todas as zonas\n")

    with AwelcDevice() as dev:
        elc = Elc(dev)

        print("FASE A - aplica a cor UMA vez e segura o dispositivo aberto")
        print("         (se reverter agora, a animacao gravada esta repintando)")
        elc.set_color(ZONES, *MAGENTA)
        countdown("observando...", 8)

        print("\nFASE B - reenvia a cor a cada 400 ms, dispositivo ainda aberto")
        print("         (se so assim ficar estavel, ha algo disputando o LED)")
        end = time.monotonic() + 8
        n = 0
        while time.monotonic() < end:
            elc.set_color(ZONES, *MAGENTA)
            n += 1
            time.sleep(0.4)
        print(f"  {n} reenvios feitos.")

        print("\nFASE C - ultima aplicacao, e vamos SOLTAR o dispositivo")
        elc.set_color(ZONES, *MAGENTA)
        print("  soltando agora (reanexando usbhid)...")

    # fora do with: usbhid ja foi reanexado
    countdown("observando apos soltar...", 8)

    print("\n" + "=" * 62)
    print("RESPONDA:")
    print("  1) Na FASE A, a cor magenta ficou os 8s ou voltou antes?")
    print("  2) Na FASE B (reenvio continuo), ficou estavel?")
    print("  3) Na FASE C, o que aconteceu no instante em que soltamos?")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
