#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gera as capturas de tela usadas no README.

Renderiza cada aba com QWidget.grab(), o que produz imagens limpas, sem
decoracao de janela e independentes do gerenciador de janelas. Roda contra o
dispositivo real, entao a aba Dispositivo mostra os dados verdadeiros.

Uso:
    .venv/bin/python tools/screenshots.py [destino]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc.gui import MainWindow  # noqa: E402

# Cores de demonstracao, para o print nao ficar monocromatico.
DEMO_COLORS = ["#ff2d55", "#ff9500", "#30d158", "#0a84ff"]

SHOTS = [
    (0, "gui-zonas.png"),
    (1, "gui-animacoes.png"),
    (2, "gui-dispositivo.png"),
]


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/img")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    win = MainWindow()

    # Nao queremos que o print dispare escrita no controlador.
    win.live_check.setChecked(False)

    for card, hexcolor in zip(win.zone_cards, DEMO_COLORS):
        card.color_btn.setColor(QColor(hexcolor))
        card.state.color = QColor(hexcolor)

    win.resize(820, 600)
    win.show()

    tabs = win.centralWidget().layout().itemAt(1).widget()

    def capture() -> None:
        for index, filename in SHOTS:
            tabs.setCurrentIndex(index)
            app.processEvents()
            path = out_dir / filename
            win.grab().save(str(path))
            size = path.stat().st_size
            print(f"  {path}  ({size // 1024} KB)  aba '{tabs.tabText(index)}'")
        app.quit()

    # Deixa a janela assentar antes de capturar.
    QTimer.singleShot(900, capture)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
