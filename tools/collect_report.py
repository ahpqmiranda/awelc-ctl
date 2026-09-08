#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Coleta um relatorio de compatibilidade para colar numa issue.

SOMENTE LEITURA. Envia apenas comandos ELC_QUERY (0x20) ao controlador e le
identificadores do sistema. Nao altera a iluminacao, nao grava animacoes, nao
toca em nada persistente.

Uso:
    .venv/bin/python tools/collect_report.py            # imprime na tela
    .venv/bin/python tools/collect_report.py -o rel.md  # salva num arquivo
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import __version__  # noqa: E402
from awelc import constants as C  # noqa: E402
from awelc.device import (  # noqa: E402
    AwelcDevice, DeviceNotFound, PermissionDenied, is_unsupported,
)
from awelc.protocol import Elc  # noqa: E402

QUERIES = [
    ("GET_VERSION", C.GET_VERSION),
    ("GET_STATUS", C.GET_STATUS),
    ("GET_PLATFORM", C.GET_PLATFORM),
    ("GET_ANIMATION_COUNT", C.GET_ANIMATION_COUNT),
    ("GET_ANIMATION_BY_ID", C.GET_ANIMATION_BY_ID),
    ("READ_SERIES", C.READ_SERIES),
]


def run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return out.stdout.strip() or "(vazio)"
    except Exception as exc:
        return f"(falhou: {exc})"


def read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return "(indisponivel)"


def collect() -> str:
    L: list[str] = []
    add = L.append

    add("## Relatorio de compatibilidade awelc-ctl")
    add("")
    add("### Maquina")
    add("")
    add("| Campo | Valor |")
    add("|---|---|")
    add(f"| Modelo | `{read_file('/sys/class/dmi/id/product_name')}` |")
    add(f"| Fabricante | `{read_file('/sys/class/dmi/id/sys_vendor')}` |")
    add(f"| BIOS | `{read_file('/sys/class/dmi/id/bios_version')}` |")
    add(f"| Kernel | `{platform.release()}` |")
    add(f"| Python | `{platform.python_version()}` |")
    add(f"| awelc-ctl | `{__version__}` |")

    distro = "(desconhecida)"
    for line in read_file("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            distro = line.split("=", 1)[1].strip('"')
    add(f"| Distribuicao | `{distro}` |")
    add("")

    add("### USB")
    add("")
    add("```")
    lsusb = run(["lsusb"])
    hits = [ln for ln in lsusb.splitlines() if "187c" in ln.lower()]
    add("\n".join(hits) if hits else "nenhum dispositivo 187c: encontrado")
    add("```")
    add("")

    add("### LEDs e modulos de kernel")
    add("")
    add("```")
    leds = run(["ls", "/sys/class/leds/"])
    kbd = [x for x in leds.split() if "kbd" in x or "backlight" in x]
    add(f"leds com kbd/backlight: {kbd or 'nenhum'}")
    lsmod = run(["lsmod"])
    mods = [ln.split()[0] for ln in lsmod.splitlines()
            if ln.split() and ("alienware" in ln or "dell" in ln)]
    add(f"modulos dell/alienware: {sorted(mods) or 'nenhum'}")
    add("```")
    add("")

    add("### Controlador")
    add("")
    try:
        with AwelcDevice(read_only=True) as dev:
            elc = Elc(dev)
            info = elc.info()
            add("| Campo | Valor |")
            add("|---|---|")
            add(f"| Firmware | `{info.firmware_str}` |")
            add(f"| Plataforma | `{info.platform_id.hex(' ')}` variante `0x{info.platform_variant:02x}` |")
            add(f"| Animacoes gravadas | {info.animation_count} |")
            if info.animation_count:
                add(f"| Primeiro id | `0x{info.first_animation_id:02x}` |")
            add("")
            add("Respostas cruas das consultas:")
            add("")
            add("```")
            for name, sub in QUERIES:
                frag = f"{C.ELC_QUERY:02x}{sub:02x}"
                if sub in (C.GET_ANIMATION_BY_ID, C.READ_SERIES):
                    frag += "0061"
                r = dev.command(frag)
                marca = "  <- NAO SUPORTADO" if is_unsupported(r) else ""
                add(f"{name:22} {r[:12].hex(' ')}{marca}")
            add("```")
    except PermissionDenied:
        add("> **Sem permissao para acessar o dispositivo.**")
        add("> Instale a regra udev (`udev/60-awelc.rules`) ou rode com `pkexec`.")
    except DeviceNotFound:
        add("> **Controlador nao encontrado.** Confira `lsusb | grep 187c`.")
    except Exception as exc:  # noqa: BLE001
        add(f"> **Erro ao consultar:** `{exc}`")
    add("")

    add("### A preencher por voce")
    add("")
    add("- [ ] Quantas zonas o teclado tem, e qual regiao cada indice acende")
    add("      (rode `tools/zonemap.py` e anote)")
    add("- [ ] A cor fica firme ou pisca? (rode `tools/diag_revert.py`)")
    add("- [ ] O que cada efeito produz? (rode `tools/diag_effects.py`)")
    add("- [ ] A tecla de backlight do notebook funciona no Linux?")
    add("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", help="salva num arquivo em vez de imprimir")
    args = ap.parse_args()

    report = collect()
    if args.output:
        Path(args.output).write_text(report)
        print(f"relatorio salvo em {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
