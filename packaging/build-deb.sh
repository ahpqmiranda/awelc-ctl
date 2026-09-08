#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Constrói o pacote .deb do awelc-ctl.
#
#   ./packaging/build-deb.sh          -> dist/awelc-ctl_<versao>_all.deb
#
# Só precisa de dpkg-deb e fakeroot, sem debhelper.
#
# O pacote traz a biblioteca, a CLI, a regra udev e o atalho do menu. O
# PySide6 entra como Recommends porque não existe no Ubuntu 22.04 (só a
# partir do 24.04); nesses casos a GUI vem do pip e a CLI funciona igual.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml")"
[ -n "$VERSION" ] || { echo "não consegui ler a versão do pyproject.toml" >&2; exit 1; }

PKG="awelc-ctl"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

STAGE="$BUILD/$PKG"
install -d "$STAGE/DEBIAN"
install -d "$STAGE/usr/lib/python3/dist-packages/awelc"
install -d "$STAGE/usr/bin"
install -d "$STAGE/usr/lib/udev/rules.d"
install -d "$STAGE/usr/share/applications"
install -d "$STAGE/usr/share/doc/$PKG"

# --- conteúdo -----------------------------------------------------------
install -m 0644 "$ROOT"/src/awelc/*.py "$STAGE/usr/lib/python3/dist-packages/awelc/"
install -m 0644 "$ROOT/udev/60-awelc.rules" "$STAGE/usr/lib/udev/rules.d/"
install -m 0644 "$ROOT/packaging/awelc-gui.desktop" "$STAGE/usr/share/applications/"
install -m 0644 "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$ROOT/CONTRIBUTING.md" \
    "$STAGE/usr/share/doc/$PKG/"

cat > "$STAGE/usr/bin/awelc" <<'EOF'
#!/usr/bin/python3
import sys
from awelc.cli import main
sys.exit(main())
EOF

cat > "$STAGE/usr/bin/awelc-gui" <<'EOF'
#!/usr/bin/python3
import sys
try:
    from awelc.gui import main
except ImportError:
    sys.exit(
        "awelc-gui precisa do PySide6.\n"
        "  Ubuntu 24.04+/Debian 13+:  sudo apt install python3-pyside6\n"
        "  Ubuntu 22.04 e derivados:  pip install --user PySide6"
    )
sys.exit(main())
EOF
chmod 0755 "$STAGE/usr/bin/awelc" "$STAGE/usr/bin/awelc-gui"

# --- metadados ----------------------------------------------------------
INSTALLED_SIZE="$(du -ks "$STAGE" | cut -f1)"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-usb
Recommends: python3-pyside6
Suggests: policykit-1
Installed-Size: $INSTALLED_SIZE
Maintainer: Alan Miranda <ahpqmiranda@users.noreply.github.com>
Homepage: https://github.com/ahpqmiranda/awelc-ctl
Description: Controle do backlight RGB Alienware AW-ELC no Linux
 Controla cor, zonas e brilho do backlight de teclados que usam o
 controlador USB Alienware AW-ELC (187c:0550), presente em vários Dell
 G3, G5, G7, G15, G16 e Alienware.
 .
 Esses teclados não são expostos como LED do kernel: o controlador é um
 dispositivo USB HID separado, sem driver. Este pacote traz uma
 biblioteca Python, a ferramenta de linha de comando awelc, uma regra
 udev que dispensa privilégios e um atalho para a interface gráfica.
 .
 A interface gráfica precisa do PySide6, que no Ubuntu 22.04 ainda não
 está nos repositórios; nesse caso instale via pip. A linha de comando
 funciona sem ele.
 .
 Validado no Dell G5 5590. Relatos de outros modelos são bem-vindos.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger --subsystem-match=usb || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    echo "awelc-ctl: se o controlador já estava conectado, desconecte e"
    echo "reconecte (ou reinicie) para a regra udev passar a valer."
fi
exit 0
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

cat > "$STAGE/usr/share/doc/$PKG/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: awelc-ctl
Source: https://github.com/ahpqmiranda/awelc-ctl

Files: *
Copyright: 2026 Alan Miranda
License: GPL-3+
 Deriva de G15kblight-cli e Dell-G-Series-Controller, ambos GPL-3.0.
 .
 This program is free software: you can redistribute it and/or modify it
 under the terms of the GNU General Public License as published by the
 Free Software Foundation, either version 3 of the License, or (at your
 option) any later version.
 .
 On Debian systems, the complete text of the GNU General Public License
 version 3 can be found in /usr/share/common-licenses/GPL-3.
EOF

chmod 0644 "$STAGE/usr/share/doc/$PKG/copyright"

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/${PKG}_${VERSION}_all.deb"
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT" >/dev/null
echo "$OUT"
