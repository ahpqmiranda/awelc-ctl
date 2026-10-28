#!/usr/bin/python3 -I
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper privilegiado do gerenciamento de energia do awelc-ctl.

Roda como root via pkexec, autorizado pela ação polkit
``io.github.ahpqmiranda.awelc-ctl.power``. Instalado em
``/usr/libexec/awelc-ctl/awelc-power-helper``.

Regras deste arquivo:

- Só biblioteca padrão e compatível com Python 3.10 (o ``/usr/bin/python3`` do
  Ubuntu/Pop!_OS 22.04). Não importa nada do pacote ``awelc``: o código que
  roda como root é só este arquivo, instalado num caminho de root.
- Lista fechada de operações, cada argumento validado antes de qualquer
  escrita. Nenhum caminho vem da linha de comando; os arquivos de sysfs são
  descobertos pelo nome do driver.

Uso (várias operações numa só chamada, separadas por ``--``)::

    awelc-power-helper thermal performance -- turbo on

Não há operação de ventoinha. No G5 5590 o ``fanN_boost`` do
``alienware_wmi`` é recusado pelo firmware (``EBADRQC``) e o PWM manual do
``dell_smm`` rende +1 RPM sobre a BIOS — medido no projeto awcc-fanctl. A
ventoinha responde ao modo térmico, que muda o limite de potência da CPU.
"""
from __future__ import annotations

import sys
from pathlib import Path

CHARGE_TYPES = ("Trickle", "Fast", "Standard", "Adaptive", "Custom")

# Faixas aceitas pelo dell-laptop para o modo Custom. O kernel também valida;
# aqui recusamos antes para devolver uma mensagem clara.
CHARGE_START_MIN, CHARGE_START_MAX = 50, 95
CHARGE_END_MIN, CHARGE_END_MAX = 55, 100
CHARGE_MIN_GAP = 5


class HelperError(Exception):
    pass


class Sysfs:
    """Localiza os atributos de sysfs a partir de uma raiz (``/`` em produção)."""

    def __init__(self, root: Path = Path("/")):
        self.root = root

    def legacy_profile(self) -> Path:
        """Interface agregada: grava o mesmo perfil em todos os handlers."""
        return self.root / "sys/firmware/acpi/platform_profile"

    def legacy_choices(self) -> Path:
        return self.root / "sys/firmware/acpi/platform_profile_choices"

    def profile_handlers(self) -> dict[str, Path]:
        base = self.root / "sys/class/platform-profile"
        out: dict[str, Path] = {}
        if base.is_dir():
            for d in sorted(base.iterdir()):
                name = d / "name"
                if name.is_file():
                    out[name.read_text().strip()] = d
        return out

    def hwmon(self, driver: str) -> Path | None:
        base = self.root / "sys/class/hwmon"
        if base.is_dir():
            for d in sorted(base.iterdir()):
                name = d / "name"
                if name.is_file() and name.read_text().strip() == driver:
                    return d
        return None

    def battery(self) -> Path | None:
        base = self.root / "sys/class/power_supply"
        if base.is_dir():
            for d in sorted(base.iterdir()):
                t = d / "type"
                if t.is_file() and t.read_text().strip() == "Battery":
                    return d
        return None

    def pstate(self) -> Path:
        return self.root / "sys/devices/system/cpu/intel_pstate"

    def smt_control(self) -> Path:
        return self.root / "sys/devices/system/cpu/smt/control"


def _write(path: Path, value: str) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(value)
    except FileNotFoundError:
        raise HelperError(f"atributo inexistente: {path}") from None
    except OSError as exc:
        raise HelperError(f"o kernel recusou {value!r} em {path}: {exc.strerror}") from None


def _int(text: str, what: str) -> int:
    try:
        return int(text, 10)
    except ValueError:
        raise HelperError(f"{what} inválido: {text!r}") from None


# ------------------------------------------------------------ operações
#
# Cada operação tem duas fases: ``check`` valida os argumentos e devolve uma
# função sem argumentos que faz a escrita. ``main`` roda todas as checagens
# antes da primeira escrita, para um lote com erro não ficar aplicado pela
# metade.

def op_thermal(fs: Sysfs, args: list[str]):
    """Grava o perfil pela interface agregada do ACPI.

    O G5 5590 tem dois handlers (``alienware-wmi`` e ``dell-pc``). Gravar em
    só um deixa os dois discordando, e a agregada passa a reportar ``custom``.
    A agregada aceita apenas os perfis comuns a todos e os aplica em todos.
    """
    if len(args) != 1:
        raise HelperError("uso: thermal <perfil>")
    want = args[0]
    legacy = fs.legacy_profile()
    if legacy.is_file():
        choices = fs.legacy_choices().read_text().split()
        if want not in choices:
            raise HelperError(f"perfil {want!r} fora das opções: {' '.join(choices)}")

        def do() -> str:
            _write(legacy, want)
            return f"thermal {want}"
        return do

    # Kernel sem a agregada: grava em cada handler que aceita o perfil.
    targets = [
        d for d in fs.profile_handlers().values()
        if want in (d / "choices").read_text().split()
    ]
    if not targets:
        raise HelperError(f"perfil {want!r} não é aceito por nenhum handler")

    def do_each() -> str:
        for d in targets:
            _write(d / "profile", want)
        return f"thermal {want}"
    return do_each


def op_turbo(fs: Sysfs, args: list[str]):
    if len(args) != 1 or args[0] not in ("on", "off"):
        raise HelperError("uso: turbo on|off")
    on = args[0] == "on"

    def do() -> str:
        # no_turbo é negativo: 1 desliga o turbo.
        _write(fs.pstate() / "no_turbo", "0" if on else "1")
        return f"turbo {args[0]}"
    return do


def op_smt(fs: Sysfs, args: list[str]):
    """Liga ou desliga o SMT (Hyper-Threading) em tempo de execução.

    Equivale à opção da BIOS, sem reiniciar: ``off`` coloca offline as threads
    irmãs de cada núcleo; ``on`` as traz de volta. Se o kernel está em
    ``forceoff`` (parâmetro ``nosmt=force``) ou sem suporte, não há o que fazer.
    """
    if len(args) != 1 or args[0] not in ("on", "off"):
        raise HelperError("uso: smt on|off")
    ctl = fs.smt_control()
    if not ctl.is_file():
        raise HelperError("este kernel não expõe o controle de SMT")
    state = ctl.read_text().strip()
    if state in ("forceoff", "notsupported", "notimplemented"):
        raise HelperError(f"o SMT não pode ser alterado em tempo de execução ({state})")

    def do() -> str:
        _write(ctl, args[0])
        return f"smt {args[0]}"
    return do


def op_charge_type(fs: Sysfs, args: list[str]):
    if len(args) != 1 or args[0] not in CHARGE_TYPES:
        raise HelperError(f"uso: charge-type {'|'.join(CHARGE_TYPES)}")
    bat = fs.battery()
    if bat is None:
        raise HelperError("bateria não encontrada")

    def do() -> str:
        _write(bat / "charge_types", args[0])
        return f"charge_types={args[0]}"
    return do


def op_charge_thresholds(fs: Sysfs, args: list[str]):
    if len(args) != 2:
        raise HelperError("uso: charge-thresholds <início> <fim>")
    start = _int(args[0], "início")
    end = _int(args[1], "fim")
    if not CHARGE_START_MIN <= start <= CHARGE_START_MAX:
        raise HelperError(f"início fora da faixa {CHARGE_START_MIN}-{CHARGE_START_MAX}: {start}")
    if not CHARGE_END_MIN <= end <= CHARGE_END_MAX:
        raise HelperError(f"fim fora da faixa {CHARGE_END_MIN}-{CHARGE_END_MAX}: {end}")
    if end - start < CHARGE_MIN_GAP:
        raise HelperError(f"o fim precisa ficar ao menos {CHARGE_MIN_GAP} pontos acima do início")
    bat = fs.battery()
    if bat is None:
        raise HelperError("bateria não encontrada")
    start_f = bat / "charge_control_start_threshold"
    end_f = bat / "charge_control_end_threshold"

    def do() -> str:
        # O kernel recusa um início acima do fim atual, então a ordem das
        # escritas depende de para onde a janela está andando.
        try:
            cur_end = int(end_f.read_text())
        except (OSError, ValueError):
            cur_end = CHARGE_END_MAX
        if start >= cur_end:
            _write(end_f, str(end))
            _write(start_f, str(start))
        else:
            _write(start_f, str(start))
            _write(end_f, str(end))
        return f"charge thresholds {start}-{end}"
    return do


OPS = {
    "thermal": op_thermal,
    "turbo": op_turbo,
    "smt": op_smt,
    "charge-type": op_charge_type,
    "charge-thresholds": op_charge_thresholds,
}


def split_ops(argv: list[str]) -> list[list[str]]:
    groups: list[list[str]] = [[]]
    for a in argv:
        if a == "--":
            groups.append([])
        else:
            groups[-1].append(a)
    return [g for g in groups if g]


def main(argv: list[str] | None = None, root: Path = Path("/")) -> int:
    argv = sys.argv[1:] if argv is None else argv
    groups = split_ops(argv)
    if not groups:
        print(f"uso: awelc-power-helper <op> [args] [-- <op> ...]\nops: {', '.join(OPS)}",
              file=sys.stderr)
        return 2
    fs = Sysfs(root)
    actions = []
    for g in groups:
        if g[0] not in OPS:
            print(f"erro: operação desconhecida: {g[0]!r}", file=sys.stderr)
            return 2
        try:
            actions.append(OPS[g[0]](fs, g[1:]))
        except (HelperError, OSError) as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 2
    for do in actions:
        try:
            print(do())
        except HelperError as exc:
            print(f"erro: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
