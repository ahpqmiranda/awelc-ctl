# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface de linha de comando do awelc-ctl."""
from __future__ import annotations

import argparse
import shlex
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


# ------------------------------------------------------------------ energia

def _fmt(v, unit: str = "", nd: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def cmd_power_status(args) -> int:
    from . import power as P

    st = P.read_status()
    preset = P.matching_preset(st)
    print(f"preset            : {P.PRESETS[preset].label if preset else '— (ajuste manual)'}")
    print(f"perfil do sistema : {_fmt(st.system_profile)}"
          f"  (governor {_fmt(st.governor)}, EPP {_fmt(st.epp)})")
    mixed = "  ⚠ handlers discordam: " + ", ".join(
        f"{h.name}={h.profile}" for h in st.thermal.values()
    ) if st.thermal_mixed else ""
    alias = (
        f"  (agregada: {st.thermal_profile}; só os nomes dos handlers diferem)"
        if st.thermal_effective != st.thermal_profile else ""
    )
    print(f"modo térmico      : {_fmt(st.thermal_effective)}{alias}{mixed}")
    print(f"turbo             : {'ligado' if st.turbo else 'desligado' if st.turbo is not None else '—'}")
    smt = {True: "ligado", False: "desligado"}.get(st.smt, f"fixo ({st.smt_control or '—'})")
    print(f"multithread (SMT) : {smt}")
    if st.cpu_model:
        print(f"processador       : {st.cpu_model} · {len(st.cores)} núcleos · "
              f"{len(st.cpus_online)}/{len(st.cpus_present)} threads online")
    print(f"CPU               : {_fmt(st.cpu_mhz, ' MHz')} (máx {_fmt(st.cpu_max_mhz, ' MHz')})")
    for c in st.cores:
        threads = " ".join(f"cpu{n}={f:.0f}" for n, f in c.threads.items())
        print(f"  núcleo {c.core_id:<10}: {_fmt(c.temp_c, ' °C')}  {_fmt(c.mhz, ' MHz')}  ({threads})")
    for f in st.fans:
        print(f"{f.label:<18}: {f.rpm} RPM")
    for name, t in st.temps.items():
        print(f"{name:<18}: {t:.0f} °C")
    if st.gpu:
        g = st.gpu
        print(f"GPU               : {g.name} · {_fmt(g.temp_c, ' °C')} · {_fmt(g.power_w, ' W', 1)}"
              f" · {_fmt(g.clock_mhz, ' MHz')} · {_fmt(g.util_pct, '%')} · {g.pstate}")
        print(f"  VRAM            : {_fmt(g.mem_used_mib, ' MiB')} / {_fmt(g.mem_total_mib, ' MiB')}"
              f" ({_fmt(g.mem_pct, '%')})")
        if g.throttle_mask is not None:
            lim = [P.THROTTLE_LABELS[k] for k in g.limiting]
            info = [P.THROTTLE_LABELS[k] for k in g.throttle if k not in g.limiting]
            txt = ", ".join(lim) if lim else "nenhuma"
            if info:
                txt += f" ({', '.join(info).lower()})"
            print(f"  limitação       : {txt}")
        print(f"  clocks          : núcleo {_fmt(g.clock_mhz)}/{_fmt(g.max_clock_mhz)} MHz"
              f" · memória {_fmt(g.mem_clock_mhz)}/{_fmt(g.max_mem_clock_mhz)} MHz")
    print(f"modo gráfico      : {_fmt(st.graphics)}")
    print(f"na tomada         : {'sim' if st.ac_online else 'não' if st.ac_online is not None else '—'}")
    if st.battery:
        b = st.battery
        lim = f" ({b.start}–{b.end}%)" if b.charge_type == "Custom" else ""
        print(f"bateria           : {_fmt(b.capacity, '%')} {b.status} · carga {_fmt(b.charge_type)}{lim}"
              f" · saúde {_fmt(b.health_pct, '%')}")
    if st.thunderbolt:
        tb = st.thunderbolt
        sec = P.SECURITY_LABELS.get(tb.security or "", _fmt(tb.security))
        print(f"thunderbolt       : TB{_fmt(tb.generation)} · segurança {sec}")
        if not tb.devices:
            print("  dispositivos    : nenhum conectado")
        for d in tb.devices:
            auth = {True: "autorizado", False: "NÃO autorizado"}.get(d.authorized, "?")
            print(f"  {d.id:<15} : {d.vendor + ' ' if d.vendor else ''}{d.name} · {auth}")
        for pt in tb.ports:
            print(f"  USB-C {pt.name:<9} : dados {_fmt(pt.data_role)} · energia {_fmt(pt.power_role)}"
                  f" ({_fmt(pt.power_mode)})")
    if not st.helper_installed:
        print("\ncomponente de energia não instalado: só leitura. Ver `awelc power install`.")
    return 0


def _power_call(fn) -> int:
    from . import power as P

    try:
        out = fn()
    except P.PowerError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    if isinstance(out, list):
        out = "\n".join(out)
    if out:
        print(out)
    return 0


def cmd_power_preset(args) -> int:
    from . import power as P
    return _power_call(lambda: P.apply_preset(args.preset))


def cmd_power_system(args) -> int:
    from . import power as P
    return _power_call(lambda: P.set_system_profile(args.profile))


def cmd_power_thermal(args) -> int:
    from . import power as P
    return _power_call(lambda: P.set_thermal(args.profile))


def cmd_power_turbo(args) -> int:
    from . import power as P
    return _power_call(lambda: P.set_turbo(args.state == "on"))


def cmd_power_smt(args) -> int:
    from . import power as P
    return _power_call(lambda: P.set_smt(args.state == "on"))


def cmd_power_charge(args) -> int:
    from . import power as P
    if args.start is not None or args.end is not None:
        if args.start is None or args.end is None:
            print("erro: informe --start e --end juntos", file=sys.stderr)
            return 2
        if args.mode not in (None, "Custom"):
            print("erro: limites só valem no modo Custom", file=sys.stderr)
            return 2
        return _power_call(lambda: P.run_helper([
            ["charge-thresholds", str(args.start), str(args.end)],
            ["charge-type", "Custom"],
        ]))
    if args.mode is None:
        print("erro: informe o modo ou --start/--end", file=sys.stderr)
        return 2
    return _power_call(lambda: P.set_charge_type(args.mode))


def cmd_power_install(args) -> int:
    from .power_install import InstallError, install_command
    try:
        cmd = install_command()
    except InstallError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print("Rode como root (ou use o botão na aba Energia da GUI):\n")
    print(f"sudo sh -c {shlex.quote(cmd)}")
    return 0


def add_power_parser(sub) -> None:
    from .power import PRESETS, S76_PROFILES
    from .power_helper import CHARGE_TYPES

    pp = sub.add_parser("power", help="energia e modo térmico (system76-power + firmware Dell)")
    ps = pp.add_subparsers(dest="power_cmd", required=True)

    sp = ps.add_parser("status", help="mostra o estado atual (somente leitura)")
    sp.set_defaults(func=cmd_power_status)

    sp = ps.add_parser("preset", help="aplica um modo pronto")
    sp.add_argument("preset", choices=list(PRESETS))
    sp.set_defaults(func=cmd_power_preset)

    sp = ps.add_parser("system", help="perfil do system76-power (governor, EPP)")
    sp.add_argument("profile", choices=S76_PROFILES)
    sp.set_defaults(func=cmd_power_system)

    sp = ps.add_parser("thermal", help="modo térmico do firmware (limite de potência)")
    sp.add_argument("profile", help="cool, quiet, balanced ou performance")
    sp.set_defaults(func=cmd_power_thermal)

    sp = ps.add_parser("turbo", help="liga ou desliga o Turbo Boost")
    sp.add_argument("state", choices=("on", "off"))
    sp.set_defaults(func=cmd_power_turbo)

    sp = ps.add_parser("smt", help="liga ou desliga o multithread (Hyper-Threading)")
    sp.add_argument("state", choices=("on", "off"))
    sp.set_defaults(func=cmd_power_smt)

    sp = ps.add_parser("charge", help="modo de carga da bateria (gravado na BIOS)")
    sp.add_argument("mode", nargs="?", choices=CHARGE_TYPES)
    sp.add_argument("--start", type=int, help="início da carga no modo Custom (50-95)")
    sp.add_argument("--end", type=int, help="fim da carga no modo Custom (55-100)")
    sp.set_defaults(func=cmd_power_charge)

    sp = ps.add_parser("install", help="mostra o comando que instala o helper e a política polkit")
    sp.set_defaults(func=cmd_power_install)


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

    add_power_parser(sub)
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
