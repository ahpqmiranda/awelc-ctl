# SPDX-License-Identifier: GPL-3.0-or-later
"""Instalação do helper de energia e da política polkit a partir do código.

O pacote .deb já instala os dois. Isto serve à instalação pelo repositório:
copia os arquivos para caminhos de root, porque o pkexec só pode liberar sem
senha um executável que o usuário não consiga alterar.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .power import HELPER_PATH, POLKIT_POLICY_PATH

PKG_DIR = Path(__file__).resolve().parent
HELPER_SRC = PKG_DIR / "power_helper.py"
POLICY_SRC = PKG_DIR.parent.parent / "packaging" / POLKIT_POLICY_PATH.name


class InstallError(Exception):
    pass


def install_command() -> str:
    if not HELPER_SRC.is_file():
        raise InstallError(f"helper não encontrado: {HELPER_SRC}")
    if not POLICY_SRC.is_file():
        raise InstallError(f"política polkit não encontrada: {POLICY_SRC}")
    q = shlex.quote
    return (
        f"install -D -o root -g root -m 0755 {q(str(HELPER_SRC))} {q(str(HELPER_PATH))} && "
        f"install -D -o root -g root -m 0644 {q(str(POLICY_SRC))} {q(str(POLKIT_POLICY_PATH))}"
    )


def install_with_pkexec(timeout: float = 180) -> None:
    cmd = install_command()
    try:
        proc = subprocess.run(["pkexec", "sh", "-c", cmd],
                              capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"não foi possível executar pkexec: {exc}") from exc
    if proc.returncode != 0:
        raise InstallError(proc.stderr.strip() or f"pkexec retornou {proc.returncode}")
