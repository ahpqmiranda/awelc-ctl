# SPDX-License-Identifier: GPL-3.0-or-later
"""Abas de energia e monitoramento da central de controle."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import power as P
from .power_helper import (
    CHARGE_END_MAX, CHARGE_END_MIN, CHARGE_MIN_GAP, CHARGE_START_MAX,
    CHARGE_START_MIN,
)
from .power_install import InstallError, install_with_pkexec

POLL_MS = 2000


# ------------------------------------------------------------ segundo plano

class _Relay(QObject):
    done = Signal(object, object)   # (resultado, exceção)


def run_bg(parent: QObject, fn: Callable, on_done: Callable[[object, Exception | None], None]):
    """Roda ``fn`` numa thread e entrega o resultado na thread da GUI.

    Leitura do nvidia-smi e chamadas ao pkexec podem demorar (o pkexec pode
    até abrir um diálogo de senha); nada disso pode travar a janela.
    """
    relay = _Relay(parent)

    def finish(result, exc):
        relay.deleteLater()
        on_done(result, exc)

    relay.done.connect(finish)

    def work():
        try:
            relay.done.emit(fn(), None)
        except Exception as exc:  # noqa: BLE001 - devolvido à GUI
            relay.done.emit(None, exc)

    threading.Thread(target=work, daemon=True).start()


class StatusPoller(QObject):
    """Lê o estado a cada ``POLL_MS`` e avisa quem estiver ouvindo."""

    updated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.refresh()
        self.timer.start()

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True

        def done(st, exc):
            self._busy = False
            if exc is None:
                self.updated.emit(st)

        run_bg(self, P.read_status, done)


# ------------------------------------------------------------------ widgets

class HelperBanner(QFrame):
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
        label = QLabel(
            "O componente de energia não está instalado: modo térmico, turbo e "
            "carga da bateria ficam só para leitura. O perfil do sistema funciona."
        )
        label.setWordWrap(True)
        lay.addWidget(label, 1)
        btn = QPushButton("Instalar componente…")
        btn.clicked.connect(self.installRequested)
        lay.addWidget(btn)


class PresetButton(QPushButton):
    def __init__(self, preset: P.Preset, parent=None):
        super().__init__(preset.label, parent)
        self.preset = preset
        self.setCheckable(True)
        self.setMinimumHeight(56)
        self.setToolTip(preset.description)
        f = self.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        f.setBold(True)
        self.setFont(f)


class Meter(QWidget):
    """Rótulo, barra e valor numa linha."""

    def __init__(self, label: str, maximum: int, unit: str, parent=None,
                 warn: int | None = None, label_width: int = 130, value_width: int = 90):
        super().__init__(parent)
        self.unit = unit
        self.warn = warn
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setMinimumWidth(label_width)
        self.name = name
        if label:
            lay.addWidget(name)
        self.bar = QProgressBar()
        self.bar.setRange(0, maximum)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.bar.setMinimumWidth(30)
        lay.addWidget(self.bar, 1)
        self.value = QLabel("—")
        self.value.setMinimumWidth(value_width)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.value)
        self._normal = self.bar.palette()

    def set(self, v: float | None, text: str | None = None) -> None:
        if v is None:
            self.bar.setValue(0)
            self.value.setText("—")
            return
        self.bar.setValue(int(min(max(v, 0), self.bar.maximum())))
        self.value.setText(text or f"{v:.0f}{self.unit}")
        if self.warn is not None and v >= self.warn:
            pal = self.bar.palette()
            pal.setColor(QPalette.Highlight, QColor("#d9534f"))
            self.bar.setPalette(pal)
        else:
            self.bar.setPalette(self._normal)


# ------------------------------------------------------------------ Energia

class PowerTab(QWidget):
    def __init__(self, poller: StatusPoller, status_bar_msg: Callable[[str, int], None], parent=None):
        super().__init__(parent)
        self.poller = poller
        self.msg = status_bar_msg
        self.status: P.PowerStatus | None = None
        self._busy = False

        root = QVBoxLayout(self)

        self.banner = HelperBanner()
        self.banner.installRequested.connect(self.install_helper)
        self.banner.hide()
        root.addWidget(self.banner)

        # --- presets
        box = QGroupBox("Modo")
        bl = QVBoxLayout(box)
        row = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)  # permite nenhum marcado (ajuste manual)
        self.preset_buttons: dict[str, PresetButton] = {}
        for p in P.PRESETS.values():
            b = PresetButton(p)
            b.clicked.connect(lambda _=False, k=p.key: self.apply_preset(k))
            self.group.addButton(b)
            row.addWidget(b)
            self.preset_buttons[p.key] = b
        bl.addLayout(row)
        self.preset_desc = QLabel("—")
        self.preset_desc.setWordWrap(True)
        bl.addWidget(self.preset_desc)
        root.addWidget(box)

        # --- ajuste fino
        box = QGroupBox("Ajuste fino")
        fl = QFormLayout(box)
        self.system = QComboBox()
        for name in P.S76_PROFILES:
            self.system.addItem(name, name)
        self.system.setToolTip("system76-power: governor da CPU, EPP e limites de frequência")
        self.system.activated.connect(self._on_system)
        fl.addRow("Perfil do sistema:", self.system)

        self.thermal = QComboBox()
        self.thermal.setToolTip(
            "Modo térmico do firmware Dell. Governa o limite de potência da CPU; "
            "a ventoinha responde ao calor resultante."
        )
        self.thermal.activated.connect(self._on_thermal)
        fl.addRow("Modo térmico:", self.thermal)
        self.thermal_hint = QLabel("")
        self.thermal_hint.setEnabled(False)
        self.thermal_hint.setWordWrap(True)
        fl.addRow("", self.thermal_hint)

        self.turbo = QCheckBox("Turbo Boost")
        self.turbo.clicked.connect(self._on_turbo)
        fl.addRow("CPU:", self.turbo)

        self.smt = QCheckBox("Multithread (Hyper-Threading)")
        self.smt.setToolTip(
            "Equivale à opção da BIOS, sem reiniciar. Desligado, cada núcleo "
            "roda uma thread só: menos calor e latência mais previsível, mas "
            "menos desempenho em cargas paralelas."
        )
        self.smt.clicked.connect(self._on_smt)
        fl.addRow("", self.smt)

        fans = QLabel(
            "As ventoinhas são comandadas pela BIOS e seguem o modo térmico. "
            "Controle direto não funciona neste modelo (o firmware recusa o boost "
            "e o PWM manual rende +1 RPM) — medido no awcc-fanctl."
        )
        fans.setWordWrap(True)
        fans.setEnabled(False)
        fl.addRow("Ventoinhas:", fans)
        root.addWidget(box)

        # --- bateria
        box = QGroupBox("Carga da bateria")
        bf = QFormLayout(box)
        self.charge = QComboBox()
        self.charge.activated.connect(self._on_charge)
        bf.addRow("Modo:", self.charge)

        lim = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(CHARGE_START_MIN, CHARGE_START_MAX)
        self.start.setSuffix("%")
        self.end = QSpinBox()
        self.end.setRange(CHARGE_END_MIN, CHARGE_END_MAX)
        self.end.setSuffix("%")
        lim.addWidget(QLabel("começa em"))
        lim.addWidget(self.start)
        lim.addWidget(QLabel("para em"))
        lim.addWidget(self.end)
        self.limits_btn = QPushButton("Aplicar limites")
        self.limits_btn.clicked.connect(self._on_limits)
        lim.addWidget(self.limits_btn)
        lim.addStretch(1)
        bf.addRow("Personalizada:", lim)

        note = QLabel(
            "Gravado na BIOS: persiste a reboot e vale também no Windows. "
            "Para uso quase sempre na tomada, <i>Lenta</i> ou <i>Personalizada</i> "
            "(ex.: 50–80%) preservam a bateria."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        bf.addRow("", note)
        root.addWidget(box)

        # --- thunderbolt (só leitura)
        self.tb_box = QGroupBox("Thunderbolt")
        tf = QFormLayout(self.tb_box)
        self.tb_ctrl = QLabel("—")
        tf.addRow("Controlador:", self.tb_ctrl)
        self.tb_sec = QLabel("—")
        self.tb_sec.setWordWrap(True)
        tf.addRow("Segurança:", self.tb_sec)
        self.tb_devs = QLabel("—")
        self.tb_devs.setWordWrap(True)
        tf.addRow("Dispositivos:", self.tb_devs)
        self.tb_port = QLabel("—")
        self.tb_port.setWordWrap(True)
        tf.addRow("Porta USB-C:", self.tb_port)
        tb_note = QLabel(
            "Somente leitura. Ligar/desligar o Thunderbolt e o nível de segurança "
            "ficam na BIOS; autorizar dispositivos: <code>boltctl</code>."
        )
        tb_note.setWordWrap(True)
        tb_note.setEnabled(False)
        tf.addRow("", tb_note)
        root.addWidget(self.tb_box)

        root.addStretch(1)
        self.footer = QLabel("")
        self.footer.setEnabled(False)
        root.addWidget(self.footer)

        poller.updated.connect(self.update_status)

    # -- estado -> widgets ----------------------------------------------

    def update_status(self, st: P.PowerStatus) -> None:
        self.status = st
        self.banner.setVisible(not st.helper_installed)
        writable = st.helper_installed and not self._busy

        current = P.matching_preset(st)
        for key, b in self.preset_buttons.items():
            b.setChecked(key == current)
            b.setEnabled(not self._busy)
        if current:
            self.preset_desc.setText(P.PRESETS[current].description)
        else:
            self.preset_desc.setText("Ajuste manual — nenhum modo pronto corresponde ao estado atual.")

        self._set_combo(self.system, st.system_profile)
        self.system.setEnabled(st.system_profile is not None and not self._busy)

        if [self.thermal.itemData(i) for i in range(self.thermal.count())] != st.thermal_choices:
            self.thermal.clear()
            for c in st.thermal_choices:
                self.thermal.addItem(P.THERMAL_LABELS.get(c, c), c)
        if st.thermal_effective in st.thermal_choices:
            self._set_combo(self.thermal, st.thermal_effective)
        else:
            self.thermal.setCurrentIndex(-1)
        self.thermal.setEnabled(writable and bool(st.thermal_choices))
        if st.thermal_mixed:
            self.thermal_hint.setText(
                "⚠ Os handlers do firmware discordam ("
                + ", ".join(f"{h.name}: {h.profile}" for h in st.thermal.values())
                + "). Escolher um modo alinha os dois."
            )
        else:
            self.thermal_hint.setText(P.THERMAL_MEASURED.get(st.thermal_effective or "", ""))

        self.turbo.setChecked(bool(st.turbo))
        self.turbo.setEnabled(writable and st.turbo is not None)
        self.smt.setChecked(bool(st.smt))
        self.smt.setEnabled(writable and st.smt is not None)

        b = st.battery
        if b:
            if [self.charge.itemData(i) for i in range(self.charge.count())] != b.charge_types:
                self.charge.clear()
                for t in b.charge_types:
                    self.charge.addItem(P.CHARGE_LABELS.get(t, t), t)
            self._set_combo(self.charge, b.charge_type)
            if not self.start.hasFocus() and not self.end.hasFocus():
                if b.start is not None:
                    self.start.setValue(b.start)
                if b.end is not None:
                    self.end.setValue(b.end)
        has_limits = bool(b and b.start is not None)
        for w in (self.charge, self.start, self.end, self.limits_btn):
            w.setEnabled(writable and b is not None)
        self.start.setEnabled(writable and has_limits)
        self.end.setEnabled(writable and has_limits)
        self.limits_btn.setEnabled(writable and has_limits)

        tb = st.thunderbolt
        self.tb_box.setVisible(tb is not None)
        if tb:
            gen = f"Thunderbolt {tb.generation}" if tb.generation else "Thunderbolt"
            nvm = f" · firmware {tb.nvm_version}" if tb.nvm_version else ""
            self.tb_ctrl.setText(gen + nvm)
            self.tb_sec.setText(P.SECURITY_LABELS.get(tb.security or "", tb.security or "—"))
            if tb.devices:
                self.tb_devs.setText("\n".join(
                    f"{d.vendor + ' ' if d.vendor else ''}{d.name} — "
                    + {True: "autorizado", False: "não autorizado"}.get(d.authorized, "?")
                    for d in tb.devices))
            else:
                self.tb_devs.setText("nenhum conectado")
            self.tb_port.setText("\n".join(
                f"dados: {pt.data_role or '—'} · energia: {pt.power_role or '—'}"
                f" ({pt.power_mode or '—'})" for pt in tb.ports) or "—")

        parts = [f"governor {st.governor or '—'}", f"EPP {st.epp or '—'}"]
        if st.graphics:
            parts.append(f"gráficos {st.graphics}")
        parts.append("na tomada" if st.ac_online else "na bateria")
        self.footer.setText(" · ".join(parts))

    @staticmethod
    def _set_combo(combo: QComboBox, value) -> None:
        i = combo.findData(value)
        if i >= 0 and combo.currentIndex() != i:
            combo.setCurrentIndex(i)

    # -- ações -----------------------------------------------------------

    def _do(self, what: str, fn: Callable) -> None:
        if self._busy:
            return
        self._busy = True
        self.msg(f"{what}…", 0)

        def done(result, exc):
            self._busy = False
            if exc is None:
                self.msg(f"{what}: feito", 4000)
            elif isinstance(exc, P.AuthCancelled):
                self.msg(f"{what}: autorização cancelada", 5000)
            elif isinstance(exc, P.HelperMissing):
                self.banner.show()
                self.msg(str(exc), 6000)
            else:
                QMessageBox.critical(self, what, str(exc))
                self.msg("", 1)
            self.poller.refresh()

        run_bg(self, fn, done)

    def apply_preset(self, key: str) -> None:
        p = P.PRESETS[key]
        if key == "desempenho" and self.status and self.status.ac_online is False:
            if QMessageBox.question(
                self, p.label,
                "O notebook está na bateria. Neste modo o consumo e o calor sobem "
                "bastante, e a bateria limita a potência de qualquer jeito.\n\n"
                "Aplicar mesmo assim?",
            ) != QMessageBox.Yes:
                self.poller.refresh()
                return
        st = self.status
        self._do(f"Modo {p.label}", lambda: P.apply_preset(key, st))

    def _on_system(self, _i: int) -> None:
        prof = self.system.currentData()
        self._do(f"Perfil do sistema {prof}", lambda: P.set_system_profile(prof))

    def _on_thermal(self, _i: int) -> None:
        prof = self.thermal.currentData()
        self._do(f"Modo térmico {P.THERMAL_LABELS.get(prof, prof)}", lambda: P.set_thermal(prof))

    def _on_turbo(self, on: bool) -> None:
        self._do("Turbo " + ("ligado" if on else "desligado"), lambda: P.set_turbo(on))

    def _on_smt(self, on: bool) -> None:
        self._do("Multithread " + ("ligado" if on else "desligado"), lambda: P.set_smt(on))

    def _on_charge(self, _i: int) -> None:
        kind = self.charge.currentData()
        self._do(f"Carga {P.CHARGE_LABELS.get(kind, kind)}", lambda: P.set_charge_type(kind))

    def _on_limits(self) -> None:
        s, e = self.start.value(), self.end.value()
        if e - s < CHARGE_MIN_GAP:
            QMessageBox.warning(
                self, "Limites inválidos",
                f"O fim precisa ficar ao menos {CHARGE_MIN_GAP} pontos acima do início.",
            )
            return
        self._do(f"Limites {s}–{e}%", lambda: P.run_helper([
            ["charge-thresholds", str(s), str(e)], ["charge-type", "Custom"],
        ]))

    def install_helper(self) -> None:
        try:
            install_with_pkexec()
        except InstallError as exc:
            QMessageBox.critical(self, "Falha ao instalar", str(exc))
            return
        QMessageBox.information(
            self, "Componente instalado",
            "Helper e política polkit instalados. A partir de agora as mudanças "
            "de energia não pedem senha na sessão local.",
        )
        self.poller.refresh()


# ------------------------------------------------------------------ Monitor

def ghz(mhz: float | None) -> str:
    return "—" if mhz is None else f"{mhz / 1000:.2f} GHz"


class CoreRow:
    """Uma linha da grade de núcleos: nome, temperatura e clock."""

    def __init__(self, grid: QGridLayout, row: int, core_id: int):
        self.name = QLabel(f"Núcleo {core_id}")
        grid.addWidget(self.name, row, 0)
        self.temp = Meter("", 100, " °C", warn=90, value_width=50)
        grid.addWidget(self.temp, row, 1)
        self.clock = Meter("", 5000, "", value_width=70)
        grid.addWidget(self.clock, row, 2)

    def set(self, c: P.CpuCore, max_mhz: float | None) -> None:
        if max_mhz:
            self.clock.bar.setMaximum(int(max_mhz))
        self.temp.set(c.temp_c)
        self.clock.set(c.mhz, ghz(c.mhz))
        threads = ", ".join(f"cpu{n}: {ghz(f)}" for n, f in c.threads.items())
        self.name.setToolTip(f"Threads — {threads}" if threads else "")


class MonitorTab(QWidget):
    def __init__(self, poller: StatusPoller, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        root = QVBoxLayout(inner)

        cols = QHBoxLayout()

        # --- CPU, por núcleo
        cpu = QGroupBox("CPU")
        cl = QVBoxLayout(cpu)
        self.cpu_name = QLabel("")
        self.cpu_name.setEnabled(False)
        self.cpu_name.setWordWrap(True)
        cl.addWidget(self.cpu_name)
        self.cpu_util = Meter("Uso", 100, "%", label_width=100)
        cl.addWidget(self.cpu_util)
        self.core_grid = QGridLayout()
        self.core_grid.setHorizontalSpacing(10)
        hdr_t = QLabel("Temperatura")
        hdr_c = QLabel("Clock")
        for h in (hdr_t, hdr_c):
            h.setEnabled(False)
        self.core_grid.addWidget(hdr_t, 0, 1)
        self.core_grid.addWidget(hdr_c, 0, 2)
        self.core_grid.setColumnStretch(1, 1)
        self.core_grid.setColumnStretch(2, 1)
        cl.addLayout(self.core_grid)
        self.core_rows: dict[int, CoreRow] = {}

        hdr = QLabel("Uso por thread")
        hdr.setEnabled(False)
        cl.addWidget(hdr)
        self.thread_grid = QGridLayout()
        self.thread_grid.setHorizontalSpacing(10)
        self.thread_grid.setColumnStretch(1, 1)
        self.thread_grid.setColumnStretch(2, 1)
        cl.addLayout(self.thread_grid)
        self.thread_meters: dict[int, Meter] = {}
        self.thread_rows: dict[int, int] = {}
        self._prev_times: dict[int, tuple[int, int]] = {}

        cl.addStretch(1)
        self.cpu_summary = QLabel("—")
        self.cpu_summary.setWordWrap(True)
        cl.addWidget(self.cpu_summary)
        cols.addWidget(cpu, 1)

        # --- GPU
        gpu = QGroupBox("GPU")
        gl = QVBoxLayout(gpu)
        self.gpu_name = QLabel("")
        self.gpu_name.setEnabled(False)
        gl.addWidget(self.gpu_name)
        lw = 100
        self.gpu_util = Meter("Uso", 100, "%", label_width=lw)
        self.gpu_vram = Meter("VRAM", 6144, " MiB", label_width=lw, value_width=100)
        self.gpu_clock = Meter("Clock núcleo", 2100, " MHz", label_width=lw)
        self.gpu_mclock = Meter("Clock memória", 6000, " MHz", label_width=lw)
        self.gpu_power = Meter("Potência", 80, " W", label_width=lw)
        self.gpu_temp = Meter("Temperatura", 100, " °C", warn=87, label_width=lw)
        for m in (self.gpu_util, self.gpu_vram, self.gpu_clock, self.gpu_mclock,
                  self.gpu_power, self.gpu_temp):
            gl.addWidget(m)
        gl.addStretch(1)
        self.gpu_throttle = QLabel("")
        self.gpu_throttle.setWordWrap(True)
        self.gpu_throttle.setTextFormat(Qt.RichText)
        gl.addWidget(self.gpu_throttle)
        cols.addWidget(gpu, 1)
        root.addLayout(cols)

        # --- ventoinhas e demais sensores
        row2 = QHBoxLayout()
        fans = QGroupBox("Ventoinhas (controladas pela BIOS)")
        self.fans_lay = QVBoxLayout(fans)
        self.fan_meters: dict[int, Meter] = {}
        row2.addWidget(fans, 1)
        temps = QGroupBox("Outros sensores")
        self.temps_lay = QVBoxLayout(temps)
        self.temp_meters: dict[str, Meter] = {}
        row2.addWidget(temps, 1)
        root.addLayout(row2)

        bat = QGroupBox("Bateria")
        g = QGridLayout(bat)
        self.bat_labels = {}
        for i, key in enumerate(("Carga", "Estado", "Potência", "Saúde")):
            g.addWidget(QLabel(f"{key}:"), 0, i * 2)
            v = QLabel("—")
            g.addWidget(v, 0, i * 2 + 1)
            self.bat_labels[key] = v
        root.addWidget(bat)
        root.addStretch(1)

        poller.updated.connect(self.update_status)

    def _show_throttle(self, g: P.Gpu | None) -> None:
        if g is None or g.throttle_mask is None:
            self.gpu_throttle.setText("")
            self.gpu_throttle.setToolTip("")
            return
        thermal = {"hw_thermal", "sw_thermal", "hw_slowdown", "hw_power_brake"}
        lim = g.limiting
        if lim:
            color = "#d9534f" if thermal & set(lim) else "#e0a030"
            head = f'<span style="color:{color}"><b>Limitada:</b> ' + \
                " · ".join(P.THROTTLE_LABELS[k] for k in lim) + "</span>"
        else:
            head = "<b>Limitação:</b> nenhuma"
        info = [k for k in g.throttle if k not in lim]
        if info:
            head += " <i>(" + ", ".join(P.THROTTLE_LABELS[k].lower() for k in info) + ")</i>"
        self.gpu_throttle.setText(head)
        self.gpu_throttle.setToolTip("\n".join(
            f"{P.THROTTLE_LABELS[k]}: {P.THROTTLE_HELP[k]}" for k in g.throttle
        ) or "Nenhuma razão de redução de clock ativa.")

    def update_status(self, st: P.PowerStatus) -> None:
        # CPU
        pkg = st.temps.get("CPU (pacote)")
        hottest = max((c.temp_c for c in st.cores if c.temp_c is not None), default=None)
        parts = []
        if pkg is not None:
            parts.append(f"pacote <b>{pkg:.0f} °C</b>")
        if hottest is not None:
            parts.append(f"núcleo mais quente {hottest:.0f} °C")
        parts.append(f"média {ghz(st.cpu_mhz)}")
        if st.cpu_max_mhz:
            parts.append(f"máx {ghz(st.cpu_max_mhz)}")
        self.cpu_summary.setText(" · ".join(parts))

        online = len(st.cpus_online) or sum(len(c.threads) for c in st.cores)
        present = len(st.cpus_present) or online
        name = [st.cpu_model or "CPU", f"{len(st.cores)} núcleos",
                f"{online}/{present} threads online"]
        if st.smt is not None:
            name.append("SMT " + ("ligado" if st.smt else "desligado"))
        self.cpu_name.setText(" · ".join(name))

        usage = P.cpu_usage(self._prev_times, st.cpu_times) if self._prev_times else {}
        self._prev_times = st.cpu_times
        self.cpu_util.set(usage.get(-1))

        for i, c in enumerate(st.cores):
            r = self.core_rows.get(c.core_id)
            if r is None:
                r = CoreRow(self.core_grid, i + 1, c.core_id)
                self.core_rows[c.core_id] = r
            r.set(c, st.cpu_max_mhz)
            if c.core_id not in self.thread_rows:
                row = len(self.thread_rows)
                self.thread_rows[c.core_id] = row
                self.thread_grid.addWidget(QLabel(f"Núcleo {c.core_id}"), row, 0)
            for slot, n in enumerate(c.threads):
                m = self.thread_meters.get(n)
                if m is None:
                    m = Meter(f"cpu{n}", 100, "%", label_width=36, value_width=40)
                    self.thread_meters[n] = m
                    self.thread_grid.addWidget(m, self.thread_rows[c.core_id], 1 + slot)
        # Com o SMT desligado, as threads irmãs saem do ar: esconde, não apaga.
        live = {n for c in st.cores for n in c.threads}
        for n, m in self.thread_meters.items():
            m.setVisible(n in live)
            m.set(usage.get(n))

        # GPU
        g = st.gpu
        self.gpu_name.setText(f"{g.name} · estado {g.pstate}" if g else "GPU NVIDIA indisponível")
        self.gpu_util.set(g.util_pct if g else None)
        if g and g.mem_total_mib:
            self.gpu_vram.bar.setMaximum(int(g.mem_total_mib))
        self.gpu_vram.set(
            g.mem_used_mib if g else None,
            f"{g.mem_used_mib:.0f} / {g.mem_total_mib:.0f} MiB"
            if g and g.mem_used_mib is not None and g.mem_total_mib else None,
        )
        if g and g.max_clock_mhz:
            self.gpu_clock.bar.setMaximum(int(g.max_clock_mhz))
        if g and g.max_mem_clock_mhz:
            self.gpu_mclock.bar.setMaximum(int(g.max_mem_clock_mhz))
        self.gpu_clock.set(g.clock_mhz if g else None)
        self.gpu_mclock.set(g.mem_clock_mhz if g else None)
        self.gpu_power.set(g.power_w if g else None,
                           f"{g.power_w:.1f} W" if g and g.power_w is not None else None)
        self.gpu_temp.set(g.temp_c if g else None)
        self._show_throttle(g)

        # demais sensores (o pacote da CPU já aparece no grupo CPU)
        for name, t in st.temps.items():
            if name == "CPU (pacote)":
                continue
            m = self.temp_meters.get(name)
            if m is None:
                m = Meter(name, 100, " °C", warn=90, label_width=90, value_width=50)
                self.temp_meters[name] = m
                self.temps_lay.addWidget(m)
            m.set(t)

        for f in st.fans:
            m = self.fan_meters.get(f.index)
            if m is None:
                m = Meter(f.label, f.rpm_max or 5000, " RPM", label_width=70)
                self.fan_meters[f.index] = m
                self.fans_lay.addWidget(m)
            m.set(f.rpm, "parada" if f.rpm == 0 else f"{f.rpm} RPM")

        b = st.battery
        self.bat_labels["Carga"].setText(f"{b.capacity}%" if b and b.capacity is not None else "—")
        self.bat_labels["Estado"].setText(b.status if b else "—")
        self.bat_labels["Potência"].setText(
            f"{b.power_w:.1f} W" if b and b.power_w is not None else "—")
        self.bat_labels["Saúde"].setText(
            f"{b.health_pct:.0f}%" if b and b.health_pct is not None else "—")
