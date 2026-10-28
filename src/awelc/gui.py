# SPDX-License-Identifier: GPL-3.0-or-later
"""Central de controle PySide6: energia, monitor e backlight AW-ELC."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QSpinBox, QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)

from . import constants as C
from .device import AwelcDevice, AwelcError, DeviceNotFound, PermissionDenied
from . import profiles as P
from .power_gui import MonitorTab, PowerTab, StatusPoller
from .protocol import Elc

UDEV_RULE = Path(__file__).resolve().parent.parent.parent / "udev" / "60-awelc.rules"

# ATENCAO aos nomes: o upstream chama MORPH de "fade", mas o que ele monta e
# uma rotacao dos canais RGB, que produz um ciclo de cores - o "rainbow" do
# Alienware Command Center. PULSE nunca foi exercitado por ninguem e e
# candidato a ser o "breath" do ACC, sem confirmacao. Ver docs/protocol.md.
EFFECTS = [
    ("Estático", C.EFFECT_COLOR),
    ("Rainbow (ciclo de cores)", C.EFFECT_MORPH),
    ("Pulse (não confirmado)", C.EFFECT_PULSE),
]

ANIMATION_SLOTS = [
    (C.AC_CHARGED, "Na tomada, carregado"),
    (C.AC_CHARGING, "Na tomada, carregando"),
    (C.DC_ON, "Na bateria"),
    (C.DC_LOW, "Bateria fraca"),
    (C.AC_SLEEP, "Suspenso (tomada)"),
    (C.DC_SLEEP, "Suspenso (bateria)"),
    (C.DEFAULT_POST_BOOT, "Após o boot"),
]

DEFAULT_ZONE_COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffffff"]


def swatch(color: QColor, size: int = 24) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(color)
    return QIcon(pm)


MAX_RECENT = 16


def recent_colors() -> list[str]:
    raw = QSettings("awelc-ctl", "gui").value("recent_colors", [])
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for c in raw or []:
        try:
            out.append(P.normalize_hex(str(c)))
        except P.ProfileError:
            continue
    return out


def push_recent(color: QColor) -> None:
    hexc = color.name().lower()
    cur = [c for c in recent_colors() if c != hexc]
    QSettings("awelc-ctl", "gui").setValue("recent_colors", [hexc] + cur[:MAX_RECENT - 1])


# ---------------------------------------------------------------- estado

@dataclass
class ZoneState:
    index: int
    enabled: bool = True
    color: QColor = field(default_factory=lambda: QColor("#ffffff"))


# ------------------------------------------------------------- widgets

class ColorButton(QPushButton):
    """Botao que abre o seletor de cor e mostra a cor atual."""

    colorChanged = Signal(QColor)

    def __init__(self, color: QColor, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setMinimumWidth(120)
        self.setToolTip("Clique para escolher; botão direito: copiar/colar hex")
        self.clicked.connect(self.pick)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._refresh()

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, color: QColor) -> None:
        self._color = QColor(color)
        self._refresh()

    def _refresh(self) -> None:
        self.setIcon(swatch(self._color))
        self.setText(self._color.name().upper())

    def _set_and_emit(self, c: QColor) -> None:
        self.setColor(c)
        push_recent(c)
        self.colorChanged.emit(c)

    def pick(self) -> None:
        # Cores recentes entram nos slots "personalizados" do seletor; o campo
        # HTML do proprio dialogo ja aceita hex.
        for i, hexc in enumerate(recent_colors()[:MAX_RECENT]):
            QColorDialog.setCustomColor(i, QColor(hexc))
        c = QColorDialog.getColor(self._color, self, "Escolher cor da zona")
        if c.isValid():
            self._set_and_emit(c)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        copy = menu.addAction(f"Copiar {self._color.name().upper()}")
        paste = menu.addAction("Colar hex")
        act = menu.exec(self.mapToGlobal(pos))
        if act is copy:
            QApplication.clipboard().setText(self._color.name().upper())
        elif act is paste:
            try:
                hexc = P.normalize_hex(QApplication.clipboard().text())
            except P.ProfileError:
                return
            self._set_and_emit(QColor(hexc))


class ZoneCard(QGroupBox):
    changed = Signal()
    copyPrevious = Signal(int)

    def __init__(self, state: ZoneState, parent=None):
        super().__init__(f"Zona {state.index}", parent)
        self.state = state
        self.setCheckable(True)
        self.setChecked(state.enabled)

        lay = QVBoxLayout(self)
        self.color_btn = ColorButton(state.color)
        lay.addWidget(self.color_btn)

        if state.index > 0:
            prev = QPushButton("◄ cor da anterior")
            prev.setToolTip(f"Copia a cor da zona {state.index - 1}")
            prev.clicked.connect(lambda: self.copyPrevious.emit(self.state.index))
            lay.addWidget(prev)

        self.hint = QLabel("região não mapeada")
        self.hint.setEnabled(False)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        self.color_btn.colorChanged.connect(self._on_color)
        self.toggled.connect(self._on_toggle)

    def _on_color(self, c: QColor) -> None:
        self.state.color = c
        self.changed.emit()

    def _on_toggle(self, on: bool) -> None:
        self.state.enabled = on
        self.changed.emit()

    def set_region(self, text: str) -> None:
        self.hint.setText(text)

    def refresh(self) -> None:
        """Relê o estado (sem emitir sinais) depois de mudança externa."""
        self.blockSignals(True)
        self.setChecked(self.state.enabled)
        self.blockSignals(False)
        self.color_btn.setColor(self.state.color)


class KeyboardView(QWidget):
    """Desenho ilustrativo do teclado com as zonas coloridas.

    A posicao real das zonas neste modelo NAO foi confirmada (ver
    docs/g5590.md); as faixas verticais sao so uma representacao. Clicar numa
    faixa abre o seletor de cor daquela zona.
    """

    zoneClicked = Signal(int)
    ROWS, COLS = 6, 20

    def __init__(self, zones: list[ZoneState], parent=None):
        super().__init__(parent)
        self.zones = zones
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Clique numa faixa para escolher a cor da zona")

    def _board(self) -> QRectF:
        return QRectF(6, 6, self.width() - 12, self.height() - 30)

    def _zone_at_x(self, x: float) -> int:
        b = self._board()
        n = len(self.zones)
        frac = min(max((x - b.left()) / max(b.width(), 1), 0.0), 0.9999)
        return int(frac * n)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.zoneClicked.emit(self.zones[self._zone_at_x(ev.position().x())].index)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = self._board()
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(QColor("#1b1b1f"))
        p.drawRoundedRect(b, 8, 8)

        n = len(self.zones)
        pad, gap = 8.0, 3.0
        kw = (b.width() - 2 * pad - gap * (self.COLS - 1)) / self.COLS
        kh = (b.height() - 2 * pad - gap * (self.ROWS - 1)) / self.ROWS
        p.setPen(Qt.NoPen)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                st = self.zones[min(int(c / self.COLS * n), n - 1)]
                col = QColor(st.color) if st.enabled else QColor("#2c2c32")
                p.setBrush(col)
                p.drawRoundedRect(QRectF(b.left() + pad + c * (kw + gap),
                                         b.top() + pad + r * (kh + gap), kw, kh), 3, 3)

        p.setPen(self.palette().text().color())
        zw = b.width() / n
        for i, st in enumerate(self.zones):
            p.drawText(QRectF(b.left() + i * zw, b.bottom() + 3, zw, 18),
                       Qt.AlignCenter, f"Zona {st.index}" + ("" if st.enabled else " (off)"))


class PermissionBanner(QFrame):
    """Aviso de falta de permissao, com botao para instalar a regra udev."""

    installRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#5a3d00"))
        pal.setColor(QPalette.WindowText, QColor("#ffe9b0"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        lay = QHBoxLayout(self)
        self.label = QLabel(
            "Sem permissão para acessar o controlador USB. "
            "Instale a regra udev para usar sem privilégios."
        )
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        btn = QPushButton("Instalar regra udev…")
        btn.clicked.connect(self.installRequested)
        lay.addWidget(btn)


# ------------------------------------------------------------ animacoes

class AnimationRow(QWidget):
    def __init__(self, anim_id: int, description: str, parent=None):
        super().__init__(parent)
        self.anim_id = anim_id
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.enabled = QCheckBox()
        self.enabled.setChecked(True)
        lay.addWidget(self.enabled)

        name = QLabel(f"<b>{C.ANIMATION_NAMES[anim_id]}</b><br><small>{description}</small>")
        name.setMinimumWidth(220)
        lay.addWidget(name, 1)

        self.color_btn = ColorButton(QColor("#7733ff"))
        lay.addWidget(self.color_btn)

        self.effect = QComboBox()
        for label, value in EFFECTS:
            self.effect.addItem(label, value)
        lay.addWidget(self.effect)

    def config(self):
        c = self.color_btn.color()
        return {
            "id": self.anim_id,
            "enabled": self.enabled.isChecked(),
            "rgb": (c.red(), c.green(), c.blue()),
            "effect": self.effect.currentData(),
        }


# ------------------------------------------------------------ principal

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("awelc — central de controle")
        self.resize(780, 680)
        self.settings = QSettings("awelc-ctl", "gui")

        self.zones = [
            ZoneState(i, True, QColor(DEFAULT_ZONE_COLORS[i]))
            for i in C.DEFAULT_ZONES
        ]
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(220)
        self._live_timer.timeout.connect(self._apply_live)

        central = QWidget()
        root = QVBoxLayout(central)

        self.banner = PermissionBanner()
        self.banner.installRequested.connect(self.install_udev)
        self.banner.hide()
        root.addWidget(self.banner)

        self.setStatusBar(QStatusBar())
        self.poller = StatusPoller(self)

        tabs = QTabWidget()
        tabs.addTab(PowerTab(self.poller, self.statusBar().showMessage), "Energia")
        tabs.addTab(MonitorTab(self.poller), "Monitor")
        tabs.addTab(self._build_zones_tab(), "Zonas e cor")
        tabs.addTab(self._build_animations_tab(), "Animações")
        tabs.addTab(self._build_device_tab(), "Dispositivo")
        root.addWidget(tabs, 1)

        self.setCentralWidget(central)
        self.refresh_device()
        self.poller.start()

    # -- construcao das abas ---------------------------------------------

    def _build_zones_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # perfis
        pbox = QGroupBox("Perfis")
        pl = QHBoxLayout(pbox)
        self.profile_store = P.ProfileStore()
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        pl.addWidget(self.profile_combo, 1)
        for text, slot, tip in (
            ("Carregar", self.load_profile, "Aplica o perfil escolhido à tela"),
            ("Salvar como…", self.save_profile, "Guarda as zonas, efeito e brilho atuais"),
            ("Excluir", self.delete_profile, None),
            ("Importar…", self.import_profile, "Lê um perfil .json"),
            ("Exportar…", self.export_profile, "Salva o perfil escolhido em um .json"),
        ):
            b = QPushButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(slot)
            pl.addWidget(b)
        lay.addWidget(pbox)
        self._reload_profiles()

        # teclado ilustrativo + cartoes
        self.kb_view = KeyboardView(self.zones)
        self.kb_view.zoneClicked.connect(lambda i: self.zone_cards[i].color_btn.pick())
        lay.addWidget(self.kb_view)

        grid = QGridLayout()
        self.zone_cards = []
        for i, st in enumerate(self.zones):
            card = ZoneCard(st)
            card.changed.connect(self.schedule_live)
            card.changed.connect(self.kb_view.update)
            card.copyPrevious.connect(self.copy_previous_color)
            grid.addWidget(card, 0, i)
            self.zone_cards.append(card)
        lay.addLayout(grid)

        # atalhos de cor
        qbox = QGroupBox("Atalhos de cor")
        ql = QHBoxLayout(qbox)
        self.preset_combo = QComboBox()
        for label, key in (
            ("Todas iguais (cor da zona 0)", "solid"),
            ("Arco-íris", "rainbow"),
            ("Degradê (zona 0 → última)", "gradient"),
            ("Espelhar", "mirror"),
        ):
            self.preset_combo.addItem(label, key)
        ql.addWidget(self.preset_combo, 1)
        go = QPushButton("Aplicar às zonas")
        go.clicked.connect(self.apply_preset)
        ql.addWidget(go)
        lay.addWidget(qbox)

        note = QLabel(
            "O mapa físico das zonas ainda não foi determinado neste modelo. "
            "Use <tt>tools/zonemap.py</tt> para descobrir qual região cada índice acende."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        lay.addWidget(note)

        # brilho
        box = QGroupBox("Brilho")
        bl = QHBoxLayout(box)
        self.power = QCheckBox("Ligado")
        self.power.setChecked(True)
        self.power.toggled.connect(self._on_power)
        bl.addWidget(self.power)

        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(100)
        self.brightness.valueChanged.connect(self._on_brightness)
        bl.addWidget(self.brightness, 1)

        self.brightness_label = QLabel("100%")
        self.brightness_label.setMinimumWidth(45)
        bl.addWidget(self.brightness_label)
        lay.addWidget(box)

        # efeito
        fx = QGroupBox("Efeito")
        fl = QFormLayout(fx)
        self.effect = QComboBox()
        for label, value in EFFECTS:
            self.effect.addItem(label, value)
        fl.addRow("Tipo:", self.effect)

        self.duration = QSpinBox()
        self.duration.setRange(1, 0xFFFF)
        self.duration.setValue(0xFF)
        self.duration.setSuffix(" un.")
        self.duration.setToolTip(
            "Unidade desconhecida. O upstream fixa 255 sem explicar; "
            "ver as limitações conhecidas no README."
        )
        fl.addRow("Duração do ciclo:", self.duration)
        lay.addWidget(fx)

        lay.addStretch(1)

        actions = QHBoxLayout()
        self.live_check = QCheckBox("Pré-visualizar ao mudar")
        self.live_check.setChecked(True)
        actions.addWidget(self.live_check)
        actions.addStretch(1)

        apply_btn = QPushButton("Aplicar agora")
        apply_btn.setToolTip("Volátil: perde ao desligar o notebook")
        apply_btn.clicked.connect(self._apply_live)
        actions.addWidget(apply_btn)

        save_btn = QPushButton("Salvar no teclado…")
        save_btn.setToolTip("Grava no controlador; sobrevive a reboot")
        save_btn.clicked.connect(self.save_current)
        actions.addWidget(save_btn)
        lay.addLayout(actions)
        return w

    def _build_animations_tab(self) -> QWidget:
        outer = QWidget()
        lay = QVBoxLayout(outer)

        info = QLabel(
            "Cada situação abaixo é uma animação gravada <b>dentro do controlador</b>. "
            "Elas persistem a reboot e à troca de sistema operacional."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        self.anim_rows = []
        for aid, desc in ANIMATION_SLOTS:
            row = AnimationRow(aid, desc)
            self.anim_rows.append(row)
            il.addWidget(row)
        il.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        clear_btn = QPushButton("Remover todas…")
        clear_btn.clicked.connect(self.clear_animations)
        btns.addWidget(clear_btn)
        write_btn = QPushButton("Gravar animações…")
        write_btn.clicked.connect(self.save_animations)
        btns.addWidget(write_btn)
        lay.addLayout(btns)
        return outer

    def _build_device_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.info_form = QFormLayout()
        self.lbl_status = QLabel("—")
        self.lbl_fw = QLabel("—")
        self.lbl_platform = QLabel("—")
        self.lbl_anims = QLabel("—")
        self.lbl_readback = QLabel("—")
        self.info_form.addRow("Estado:", self.lbl_status)
        self.info_form.addRow("Firmware:", self.lbl_fw)
        self.info_form.addRow("Plataforma:", self.lbl_platform)
        self.info_form.addRow("Animações gravadas:", self.lbl_anims)
        self.info_form.addRow("Leitura de animação:", self.lbl_readback)
        lay.addLayout(self.info_form)

        warn = QLabel(
            "<b>Aviso.</b> Este firmware não permite ler de volta as animações "
            "gravadas. Qualquer gravação substitui a configuração anterior — "
            "inclusive a deixada pelo Alienware Command Center no Windows — "
            "sem caminho de volta pelo Linux."
        )
        warn.setWordWrap(True)
        lay.addWidget(warn)

        btn = QPushButton("Reler dispositivo")
        btn.clicked.connect(self.refresh_device)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    # -- cores e perfis ---------------------------------------------------

    def _set_zone_colors(self, hexes: list[str]) -> None:
        for st, h in zip(self.zones, hexes):
            st.color = QColor(h)
        for card in self.zone_cards:
            card.refresh()
        self.kb_view.update()
        self.schedule_live()

    def copy_previous_color(self, index: int) -> None:
        hexes = [z.color.name() for z in self.zones]
        hexes[index] = hexes[index - 1]
        self._set_zone_colors(hexes)

    def apply_preset(self) -> None:
        n = len(self.zones)
        cur = [z.color.name() for z in self.zones]
        key = self.preset_combo.currentData()
        if key == "solid":
            hexes = P.solid(cur[0], n)
        elif key == "rainbow":
            hexes = P.rainbow(n)
        elif key == "gradient":
            hexes = P.gradient(cur[0], cur[-1], n)
        else:
            hexes = P.mirror(cur)
        self._set_zone_colors(hexes)

    def _reload_profiles(self, select: str | None = None) -> None:
        self.profile_combo.clear()
        names = self.profile_store.names()
        self.profile_combo.addItems(names)
        if select in names:
            self.profile_combo.setCurrentText(select)

    def _current_profile(self, name: str) -> P.Profile:
        return P.Profile(
            name=name,
            colors={z.index: z.color.name() for z in self.zones},
            enabled={z.index: z.enabled for z in self.zones},
            effect=self.effect.currentData(),
            duration=self.duration.value(),
            brightness=self.brightness.value() if self.power.isChecked() else 0,
        )

    def save_profile(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Salvar perfil", "Nome do perfil:", text=self.profile_combo.currentText()
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            exists = name in self.profile_store.names()
            if exists and QMessageBox.question(
                self, "Sobrescrever", f"O perfil “{name}” já existe. Sobrescrever?"
            ) != QMessageBox.Yes:
                return
            self.profile_store.save(self._current_profile(name))
        except (P.ProfileError, OSError) as exc:
            QMessageBox.critical(self, "Não foi possível salvar", str(exc))
            return
        self._reload_profiles(name)
        self.statusBar().showMessage(f"Perfil “{name}” salvo", 3000)

    def _apply_profile(self, prof: P.Profile) -> None:
        for st in self.zones:
            if st.index in prof.colors:
                st.color = QColor(prof.colors[st.index])
                st.enabled = prof.enabled.get(st.index, True)
        for card in self.zone_cards:
            card.refresh()
        self.kb_view.update()

        i = self.effect.findData(prof.effect)
        if i >= 0:
            self.effect.setCurrentIndex(i)
        self.duration.setValue(prof.duration)

        self.brightness.blockSignals(True)
        self.power.blockSignals(True)
        on = prof.brightness > 0
        self.power.setChecked(on)
        if on:
            self.brightness.setValue(prof.brightness)
        self.brightness.setEnabled(on)
        self.brightness_label.setText(f"{self.brightness.value()}%")
        self.power.blockSignals(False)
        self.brightness.blockSignals(False)

        if self.live_check.isChecked():
            self._apply_live()
            self._set_brightness(prof.brightness)

    def load_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name:
            self.statusBar().showMessage("Nenhum perfil salvo", 3000)
            return
        try:
            prof = self.profile_store.load(name)
        except P.ProfileError as exc:
            QMessageBox.critical(self, "Não foi possível carregar", str(exc))
            return
        self._apply_profile(prof)
        self.statusBar().showMessage(f"Perfil “{name}” carregado", 3000)

    def delete_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name:
            return
        if QMessageBox.question(self, "Excluir perfil", f"Excluir o perfil “{name}”?") \
                != QMessageBox.Yes:
            return
        self.profile_store.delete(name)
        self._reload_profiles()

    def import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar perfil", "", "Perfil (*.json)")
        if not path:
            return
        try:
            prof = self.profile_store.import_file(Path(path))
        except (P.ProfileError, OSError) as exc:
            QMessageBox.critical(self, "Não foi possível importar", str(exc))
            return
        self._reload_profiles(prof.name)
        self.statusBar().showMessage(f"Perfil “{prof.name}” importado", 3000)

    def export_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar perfil", f"{P.slug(name)}.json", "Perfil (*.json)"
        )
        if not path:
            return
        try:
            self.profile_store.export_file(name, Path(path))
        except (P.ProfileError, OSError) as exc:
            QMessageBox.critical(self, "Não foi possível exportar", str(exc))

    # -- helpers ----------------------------------------------------------

    def active_zones(self) -> list[int]:
        return [z.index for z in self.zones if z.enabled]

    def _run(self, fn, *, quiet: bool = False):
        """Executa uma operacao no dispositivo tratando erros conhecidos."""
        try:
            with AwelcDevice() as dev:
                result = fn(Elc(dev))
            self.banner.hide()
            return result
        except PermissionDenied:
            self.banner.show()
            if not quiet:
                self.statusBar().showMessage("Sem permissão para acessar o dispositivo", 6000)
        except DeviceNotFound:
            self.banner.hide()
            if not quiet:
                QMessageBox.warning(
                    self, "Controlador não encontrado",
                    "Nenhum controlador 187c:0550/0551 foi encontrado.\n"
                    "Confira com: lsusb | grep 187c",
                )
        except AwelcError as exc:
            if not quiet:
                QMessageBox.critical(self, "Erro", str(exc))
        return None

    def confirm_write(self) -> bool:
        if self.settings.value("ack_irreversible", False, type=bool):
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Gravar no controlador")
        box.setText("Esta operação grava permanentemente no controlador.")
        box.setInformativeText(
            "As animações atuais serão substituídas. Este firmware não permite "
            "lê-las de volta, então a configuração anterior — inclusive a que o "
            "Alienware Command Center deixou gravada no Windows — será perdida "
            "sem possibilidade de restauração pelo Linux.\n\n"
            "Nada disso afeta BIOS ou firmware: no pior caso você reconfigura a "
            "iluminação do zero."
        )
        ack = QCheckBox("Não perguntar de novo")
        box.setCheckBox(ack)
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Ok:
            return False
        if ack.isChecked():
            self.settings.setValue("ack_irreversible", True)
        return True

    # -- acoes ------------------------------------------------------------

    def refresh_device(self) -> None:
        def read(elc: Elc):
            i = elc.info()
            r = elc.get_animation_by_id(C.DEFAULT_POST_BOOT)
            return i, r

        got = self._run(read, quiet=True)
        if got is None:
            self.lbl_status.setText("indisponível")
            self.lbl_fw.setText("—")
            self.lbl_platform.setText("—")
            self.lbl_anims.setText("—")
            self.lbl_readback.setText("—")
            return
        info, readback = got
        self.lbl_status.setText("conectado")
        self.lbl_fw.setText(info.firmware_str)
        self.lbl_platform.setText(
            f"id={info.platform_id.hex(' ')} variante=0x{info.platform_variant:02x}"
        )
        name = C.ANIMATION_NAMES.get(info.first_animation_id, "desconhecida")
        self.lbl_anims.setText(
            f"{info.animation_count}"
            + (f" (primeira: 0x{info.first_animation_id:02x} {name})" if info.animation_count else "")
        )
        self.lbl_readback.setText(
            "suportada" if readback is not None else "não suportada por este firmware"
        )
        self.statusBar().showMessage(f"Controlador conectado — firmware {info.firmware_str}", 4000)

    def schedule_live(self) -> None:
        if self.live_check.isChecked():
            self._live_timer.start()

    def _zone_map(self) -> dict[int, tuple[int, int, int]]:
        """Zonas desligadas entram apagadas, para substituir a animacao inteira."""
        mapping: dict[int, tuple[int, int, int]] = {}
        for z in self.zones:
            if z.enabled:
                c = z.color
                mapping[z.index] = (c.red(), c.green(), c.blue())
            else:
                mapping[z.index] = (0, 0, 0)
        return mapping

    def _effect_params(self) -> tuple[int, int]:
        effect = self.effect.currentData()
        duration = self.duration.value() if effect == C.EFFECT_MORPH else C.DURATION_MAX
        return effect, duration

    def _apply_live(self) -> None:
        zones = self.active_zones()
        if not zones:
            self.statusBar().showMessage("Nenhuma zona selecionada", 3000)
            return
        effect, duration = self._effect_params()
        mapping = self._zone_map()

        def do(elc: Elc):
            # play_zone_map termina em FINISH_PLAY: substitui a animacao em
            # execucao sem gravar. set_color sozinho seria repintado pela
            # animacao gravada. Ver docs/protocol.md.
            elc.play_zone_map(mapping, effect=effect, duration=duration)
            return True

        if self._run(do) is not None:
            self.statusBar().showMessage(f"Aplicado nas zonas {zones}", 3000)

    def _on_power(self, on: bool) -> None:
        self.brightness.setEnabled(on)
        level = self.brightness.value() if on else 0
        self._set_brightness(level)

    def _on_brightness(self, value: int) -> None:
        self.brightness_label.setText(f"{value}%")
        self._set_brightness(value)

    def _set_brightness(self, level: int) -> None:
        zones = self.active_zones() or list(C.DEFAULT_ZONES)
        dimming = C.BRIGHTNESS_MAX - level

        def do(elc: Elc):
            elc.dim(zones, dimming)
            return True

        self._run(do, quiet=True)

    def save_current(self) -> None:
        zones = self.active_zones()
        if not zones:
            QMessageBox.information(self, "Nada a gravar", "Selecione ao menos uma zona.")
            return
        if not self.confirm_write():
            return

        effect, duration = self._effect_params()
        mapping = self._zone_map()
        dimmed = {z: (r // 2, g // 2, b // 2) for z, (r, g, b) in mapping.items()}
        off = {z: (0, 0, 0) for z in mapping}

        def do(elc: Elc):
            for aid in (C.AC_CHARGED, C.AC_CHARGING, C.DEFAULT_POST_BOOT):
                elc.store_zone_map(aid, mapping, effect=effect, duration=duration)
            elc.store_zone_map(C.DC_ON, dimmed, effect=effect, duration=duration)
            for aid in (C.AC_SLEEP, C.DC_SLEEP):
                elc.store_zone_map(aid, off)
            return True

        if self._run(do) is not None:
            self.statusBar().showMessage("Configuração gravada no controlador", 5000)
            self.refresh_device()

    def save_animations(self) -> None:
        configs = [r.config() for r in self.anim_rows if r.config()["enabled"]]
        if not configs:
            QMessageBox.information(self, "Nada a gravar", "Nenhuma animação marcada.")
            return
        if not self.confirm_write():
            return
        zones = self.active_zones() or list(C.DEFAULT_ZONES)

        def do(elc: Elc):
            for cfg in configs:
                if cfg["effect"] == C.EFFECT_MORPH:
                    elc.store_morph(cfg["id"], zones, *cfg["rgb"], duration=self.duration.value())
                else:
                    elc.store_static(cfg["id"], zones, *cfg["rgb"])
            return True

        if self._run(do) is not None:
            self.statusBar().showMessage(f"{len(configs)} animação(ões) gravada(s)", 5000)
            self.refresh_device()

    def clear_animations(self) -> None:
        if QMessageBox.warning(
            self, "Remover animações",
            "Remove TODAS as animações gravadas no controlador.\n\n"
            "Como este firmware não permite lê-las de volta, isto é irreversível.",
            QMessageBox.Cancel | QMessageBox.Ok, QMessageBox.Cancel,
        ) != QMessageBox.Ok:
            return

        def do(elc: Elc):
            return elc.clear_all()

        n = self._run(do)
        if n is not None:
            self.statusBar().showMessage(f"{n} animação(ões) removida(s)", 5000)
            self.refresh_device()

    def install_udev(self) -> None:
        if not UDEV_RULE.exists():
            QMessageBox.critical(
                self, "Regra não encontrada",
                f"Arquivo não encontrado:\n{UDEV_RULE}",
            )
            return
        cmd = (
            f"install -m 0644 {UDEV_RULE} /etc/udev/rules.d/60-awelc.rules && "
            "udevadm control --reload-rules && udevadm trigger"
        )
        try:
            proc = subprocess.run(
                ["pkexec", "sh", "-c", cmd],
                capture_output=True, text=True, timeout=180,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            QMessageBox.critical(self, "Falha", f"Não foi possível executar pkexec: {exc}")
            return
        if proc.returncode == 0:
            QMessageBox.information(
                self, "Regra instalada",
                "Regra udev instalada. Desconecte e reconecte o controlador, ou "
                "reinicie, para que as permissões passem a valer.",
            )
            self.refresh_device()
        else:
            QMessageBox.critical(
                self, "Falha ao instalar",
                proc.stderr.strip() or f"pkexec retornou {proc.returncode}",
            )


def main(argv=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("awelc-gui")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
