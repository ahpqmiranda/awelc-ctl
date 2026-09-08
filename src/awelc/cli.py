# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface de linha de comando do awelc-ctl."""
from __future__ import annotations

import argparse
import sys

from . import constants as C
from .device import AwelcDevice, AwelcError, is_unsupported
from .protocol import Elc


def parse_color(text: str) -> tuple[int, int, int]:
    s = text.strip().lstrip("#").removeprefix("0x").removeprefix("0X")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise argparse.ArgumentTypeError(
            f"cor invalida: {text!r} (use RRGGBB, #RRGGBB ou RGB)"
        )
    try:
        v = int(s, 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"cor invalida: {text!r}") from exc
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def parse_zones(text: str) -> tuple[int, ...]:
    if text in ("all", "todas"):
        return C.DEFAULT_ZONES
    try:
        zones = tuple(int(p) for p in text.split(",") if p.strip() != "")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"zonas invalidas: {text!r} (use 0,1,2,3 ou 'all')"
        ) from exc
    if not zones:
        raise argparse.ArgumentTypeError("lista de zonas vazia")
    for z in zones:
        if not 0 <= z <= 15:
            raise argparse.ArgumentTypeError(f"zona fora da faixa: {z}")
    return zones


def cmd_info(args) -> int:
    with AwelcDevice(read_only=True) as dev:
        elc = Elc(dev)
        i = elc.info()
        print(f"firmware        : {i.firmware_str}")
        print(f"plataforma      : id={i.platform_id.hex(' ')} variante=0x{i.platform_variant:02x}")
        print(f"animacoes salvas: {i.animation_count}", end="")
        if i.animation_count:
            name = C.ANIMATION_NAMES.get(i.first_animation_id, "desconhecida")
            print(f" (primeira: 0x{i.first_animation_id:02x} {name})")
        else:
            print()
        r = dev.command(f"{C.ELC_QUERY:02x}{C.GET_ANIMATION_BY_ID:02x}0061")
        print(
            "leitura de animacao: "
            + ("NAO suportada por este firmware" if is_unsupported(r) else "suportada")
        )
    return 0


def cmd_color(args) -> int:
    r, g, b = args.color
    with AwelcDevice() as dev:
        elc = Elc(dev)
        if args.persist:
            for aid in (C.AC_CHARGED, C.AC_CHARGING, C.DEFAULT_POST_BOOT):
                elc.store_static(aid, args.zones, r, g, b)
            half = (r // 2, g // 2, b // 2)
            elc.store_static(C.DC_ON, args.zones, *half)
            for aid in (C.AC_SLEEP, C.DC_SLEEP):
                elc.store_static(aid, args.zones, 0, 0, 0)
            print(f"cor #{r:02x}{g:02x}{b:02x} gravada nas zonas {args.zones}")
        else:
            # Animacao transitoria (FINISH_PLAY): substitui a animacao em
            # execucao. SET_COLOR sozinho seria repintado por ela.
            elc.play_transient(args.zones, r, g, b)
            print(
                f"cor #{r:02x}{g:02x}{b:02x} aplicada nas zonas {args.zones} "
                "(volatil: some ao desligar)"
            )
    return 0


def cmd_morph(args) -> int:
    r, g, b = args.color
    with AwelcDevice() as dev:
        elc = Elc(dev)
        for aid in (C.AC_CHARGED, C.AC_CHARGING, C.DEFAULT_POST_BOOT):
            elc.store_morph(aid, args.zones, r, g, b, duration=args.duration)
        print(f"morph a partir de #{r:02x}{g:02x}{b:02x} gravado nas zonas {args.zones}")
    return 0


def cmd_brightness(args) -> int:
    # O usuario pensa em brilho (100 = claro); o protocolo usa dimming (0 = claro).
    dimming = C.BRIGHTNESS_MAX - args.level
    with AwelcDevice() as dev:
        Elc(dev).dim(args.zones, dimming)
    print(f"brilho {args.level}% (dimming={dimming}) nas zonas {args.zones}")
    return 0


def cmd_off(args) -> int:
    with AwelcDevice() as dev:
        Elc(dev).dim(args.zones, C.BRIGHTNESS_MAX)
    print(f"zonas {args.zones} apagadas")
    return 0


def cmd_clear(args) -> int:
    if not args.yes:
        print(
            "Isto remove TODAS as animacoes gravadas no controlador, incluindo\n"
            "as que vieram do Windows. Este firmware nao permite le-las de volta,\n"
            "entao a remocao e irreversivel. Repita com --yes para confirmar.",
            file=sys.stderr,
        )
        return 1
    with AwelcDevice() as dev:
        n = Elc(dev).clear_all()
    print(f"{n} animacao(oes) removida(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="awelc",
        description="Controle do backlight RGB Alienware AW-ELC (187c:0550).",
        epilog="Testado no Dell G5 5590, firmware 1.1.7. Ver docs/g5590.md.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    zones_kw = dict(
        type=parse_zones,
        default=C.DEFAULT_ZONES,
        help="zonas separadas por virgula, ou 'all' (padrao: 0,1,2,3)",
    )

    sp = sub.add_parser("info", help="identifica o controlador (somente leitura)")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("color", help="define uma cor solida")
    sp.add_argument("color", type=parse_color, help="RRGGBB, #RRGGBB ou RGB")
    sp.add_argument("--zones", **zones_kw)
    sp.add_argument(
        "--persist",
        action="store_true",
        help="grava no controlador (sobrevive a reboot); sem isso a cor e volatil",
    )
    sp.set_defaults(func=cmd_color)

    sp = sub.add_parser(
        "rainbow", aliases=["morph"],
        help="grava um ciclo de cores (rotação dos canais RGB)",
    )
    sp.add_argument("color", type=parse_color)
    sp.add_argument("--zones", **zones_kw)
    sp.add_argument("--duration", type=int, default=0x00FF,
                    help="duracao do ciclo, unidade desconhecida (padrao 255)")
    sp.set_defaults(func=cmd_morph)

    sp = sub.add_parser("brightness", help="ajusta o brilho (0-100)")
    sp.add_argument("level", type=int, choices=range(0, 101), metavar="0-100")
    sp.add_argument("--zones", **zones_kw)
    sp.set_defaults(func=cmd_brightness)

    sp = sub.add_parser("off", help="apaga o backlight")
    sp.add_argument("--zones", **zones_kw)
    sp.set_defaults(func=cmd_off)

    sp = sub.add_parser("clear", help="remove todas as animacoes gravadas")
    sp.add_argument("--yes", action="store_true", help="confirma a operacao irreversivel")
    sp.set_defaults(func=cmd_clear)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AwelcError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
