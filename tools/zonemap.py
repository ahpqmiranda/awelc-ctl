#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Descobre o mapa fisico de zonas do teclado.

Acende uma zona por vez e pausa, para o operador anotar qual pedaco do
teclado acendeu. Usa SET_COLOR (0x27), que e volatil: nao grava animacao
no controlador. Ao final apaga tudo.

ATENCAO: SET_COLOR e um comando de ESCRITA. Ele nao persiste animacoes,
mas altera o estado atual da iluminacao.

Uso:
    pkexec .venv/bin/python tools/zonemap.py [--zones 0,1,2,3] [--hold 4]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import AwelcDevice, Elc  # noqa: E402
from awelc import constants as C  # noqa: E402

PROBES = [
    ("VERMELHO", (255, 0, 0)),
    ("VERDE", (0, 255, 0)),
    ("AZUL", (0, 0, 255)),
    ("BRANCO", (255, 255, 255)),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zones", default="0,1,2,3", help="zonas a testar")
    ap.add_argument("--hold", type=float, default=4.0, help="segundos por zona")
    args = ap.parse_args()

    zones = [int(z) for z in args.zones.split(",") if z.strip()]

    print("=" * 58)
    print("MAPEAMENTO DE ZONAS - observe o teclado e anote")
    print("=" * 58)
    print(f"zonas a testar : {zones}")
    print(f"tempo por zona : {args.hold}s")
    print("comando        : SET_COLOR (0x27) - volatil, nao grava animacao\n")

    with AwelcDevice() as dev:
        elc = Elc(dev)

        print("apagando tudo antes de comecar...")
        elc.set_color(zones, 0, 0, 0)
        elc.dim(zones, C.BRIGHTNESS_MIN)  # 0 = brilho total
        time.sleep(1.5)

        for zone in zones:
            label, (r, g, b) = PROBES[zone % len(PROBES)]
            others = [z for z in zones if z != zone]
            print(f"  zona {zone}  ->  {label:9}  (demais apagadas)")
            if others:
                elc.set_color(others, 0, 0, 0)
            elc.set_color([zone], r, g, b)
            time.sleep(args.hold)

        print("\ntodas as zonas juntas em branco...")
        elc.set_color(zones, 255, 255, 255)
        time.sleep(args.hold)

        print("apagando.")
        elc.set_color(zones, 0, 0, 0)

    print("\nAnote qual regiao do teclado acendeu em cada passo e registre")
    print("em docs/g5590.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
