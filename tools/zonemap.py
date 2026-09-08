#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Descobre o mapa fisico de zonas do teclado.

Acende uma zona por vez e pausa, para o operador anotar qual pedaco do
teclado acendeu. Usa play_zone_map (FINISH_PLAY), que substitui a animacao em execucao sem
gravar nada. SET_COLOR sozinho seria repintado pela animacao do controlador.
Ao final apaga tudo.

ATENCAO: sao comandos de ESCRITA. Nao persistem animacoes (a contagem
gravada nao muda), mas alteram o estado atual da iluminacao.

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
    print("comando        : FINISH_PLAY - transitorio, nao grava animacao\n")

    with AwelcDevice() as dev:
        elc = Elc(dev)

        print("apagando tudo antes de comecar...")
        elc.dim(zones, C.BRIGHTNESS_MIN)  # 0 = brilho total
        elc.play_zone_map({z: (0, 0, 0) for z in zones})
        time.sleep(1.5)

        for zone in zones:
            label, (r, g, b) = PROBES[zone % len(PROBES)]
            print(f"  zona {zone}  ->  {label:9}  (demais apagadas)")
            mapping = {z: (0, 0, 0) for z in zones}
            mapping[zone] = (r, g, b)
            elc.play_zone_map(mapping)
            time.sleep(args.hold)

        print("\ntodas as zonas juntas em branco...")
        elc.play_zone_map({z: (255, 255, 255) for z in zones})
        time.sleep(args.hold)

        print("apagando.")
        elc.play_zone_map({z: (0, 0, 0) for z in zones})

    print("\nAnote qual regiao do teclado acendeu em cada passo e registre")
    print("em docs/g5590.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
