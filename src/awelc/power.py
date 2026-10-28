# SPDX-License-Identifier: GPL-3.0-or-later
"""Gerenciamento de energia e térmico do Dell G5 5590 (e afins).

Este módulo roda sem privilégios. Leitura é feita direto do sysfs; escrita vai
por dois caminhos:

- **Perfil do sistema** (governor, EPP, limites de frequência): DBus do
  ``system76-power``, que o usuário da sessão já pode chamar.
- **Tudo que exige root** (modo térmico, turbo, SMT, carga da bateria):
  ``pkexec`` no helper de :mod:`awelc.power_helper`, instalado em
  :data:`HELPER_PATH`.

As ventoinhas são só lidas. No G5 5590 não há controle direto que funcione
(ver o projeto awcc-fanctl e :mod:`awelc.power_helper`): elas respondem ao
modo térmico, que governa o limite de potência da CPU.

Nada aqui depende do Qt, para a CLI funcionar sem ele.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .power_helper import CHARGE_TYPES, Sysfs

HELPER_PATH = Path("/usr/libexec/awelc-ctl/awelc-power-helper")
POLKIT_ACTION = "io.github.ahpqmiranda.awelc-ctl.power"
POLKIT_POLICY_PATH = Path(f"/usr/share/polkit-1/actions/{POLKIT_ACTION}.policy")

S76_BUS = "com.system76.PowerDaemon"
S76_PATH = "/com/system76/PowerDaemon"
S76_PROFILES = ("Battery", "Balanced", "Performance")

AW_HANDLER = "alienware-wmi"
DELL_HANDLER = "dell-pc"

THERMAL_LABELS = {
    "cool": "Frio",
    "quiet": "Silencioso",
    "balanced": "Equilibrado",
    "balanced-performance": "Equilibrado+desempenho",
    "performance": "Desempenho",
    "custom": "Misto",
    "low-power": "Economia",
}

# Nomes proprios de cada handler que significam o mesmo que um perfil da
# interface agregada. Medido em 2026-10-28 no G5 5590: gravar "performance" na
# agregada deixa alienware-wmi em "balanced-performance" e dell-pc em
# "performance", e a agregada passa a reportar "custom" so porque os nomes
# diferem. Nao e discordancia real.
HANDLER_ALIASES = {
    AW_HANDLER: {"balanced-performance": "performance"},
}

# Medido no G5 5590 (awcc-fanctl, docs/interfaces.md): 3 núcleos em carga
# constante, regime permanente. O perfil limita a potência da CPU; a rotação é
# consequência do calor, não um ajuste direto.
THERMAL_MEASURED = {
    "cool": "≈4120 RPM · 85 °C sob carga",
    "quiet": "≈4120 RPM · 86 °C sob carga",
    "balanced": "≈4360 RPM · 90 °C sob carga",
    "performance": "≈4930 RPM · 92 °C sob carga",
}

CHARGE_LABELS = {
    "Trickle": "Lenta (uso na tomada)",
    "Fast": "Rápida",
    "Standard": "Padrão",
    "Adaptive": "Adaptativa",
    "Custom": "Personalizada (limites)",
}


class PowerError(Exception):
    pass


class HelperMissing(PowerError):
    """O helper privilegiado ou a política polkit não estão instalados."""


class AuthCancelled(PowerError):
    pass


# ------------------------------------------------------------- estado

@dataclass
class ThermalHandler:
    name: str
    profile: str
    choices: list[str]


@dataclass
class Fan:
    index: int
    label: str
    rpm: int
    rpm_min: int | None
    rpm_max: int | None


@dataclass
class Battery:
    capacity: int | None = None
    status: str = "?"
    charge_type: str | None = None
    charge_types: list[str] = field(default_factory=list)
    start: int | None = None
    end: int | None = None
    cycles: int | None = None
    health_pct: float | None = None
    power_w: float | None = None


@dataclass
class Gpu:
    name: str
    temp_c: float | None
    power_w: float | None
    clock_mhz: float | None
    util_pct: float | None
    pstate: str | None
    mem_total_mib: float | None = None
    mem_used_mib: float | None = None
    mem_clock_mhz: float | None = None
    max_clock_mhz: float | None = None
    max_mem_clock_mhz: float | None = None
    throttle_mask: int | None = None        # None: o driver não informou

    @property
    def throttle(self) -> list[str]:
        """Razões ativas, das mais graves às informativas."""
        return throttle_reasons(self.throttle_mask)

    @property
    def limiting(self) -> list[str]:
        """Só as razões que de fato seguram o clock (não ocioso, etc.)."""
        return [k for k in self.throttle if k in THROTTLE_LIMITS]

    @property
    def mem_pct(self) -> float | None:
        if self.mem_total_mib and self.mem_used_mib is not None:
            return 100 * self.mem_used_mib / self.mem_total_mib
        return None


@dataclass
class CpuCore:
    """Núcleo físico: temperatura do coretemp e clock de cada thread."""

    core_id: int
    temp_c: float | None = None
    temp_max_c: float | None = None
    threads: dict[int, float] = field(default_factory=dict)   # cpu lógica -> MHz

    @property
    def mhz(self) -> float | None:
        """Maior clock entre as threads: é o que o núcleo físico está rodando."""
        return max(self.threads.values()) if self.threads else None


SECURITY_LABELS = {
    "none": "Nenhuma (todos os dispositivos autorizados)",
    "user": "Usuário (cada dispositivo precisa de autorização)",
    "secure": "Segura (autorização com chave)",
    "dponly": "Só DisplayPort e USB",
    "usbonly": "Só USB",
    "nopcie": "Sem túnel PCIe",
}


@dataclass
class TbDevice:
    id: str
    name: str
    vendor: str | None = None
    authorized: bool | None = None
    generation: int | None = None


@dataclass
class TypeCPort:
    name: str
    data_role: str | None = None        # host | device
    power_role: str | None = None       # source | sink
    power_mode: str | None = None       # default, 1.5A, 3.0A, usb_power_delivery


@dataclass
class Thunderbolt:
    """Controlador Thunderbolt/USB4 e a porta USB-C. Só leitura."""

    security: str | None = None
    controller: str | None = None
    generation: int | None = None
    nvm_version: str | None = None
    devices: list[TbDevice] = field(default_factory=list)
    ports: list[TypeCPort] = field(default_factory=list)
    bolt_installed: bool = False


_TB_PERIPHERAL = re.compile(r"^\d+-[1-9][\d.]*$")


def _bracketed(text: str | None) -> str | None:
    """Em ``[host] device`` devolve o valor entre colchetes."""
    if text is None:
        return None
    m = re.search(r"\[([^\]]+)\]", text)
    return m.group(1) if m else text


def read_thunderbolt(root: Path = Path("/")) -> Thunderbolt | None:
    """Estado do Thunderbolt, ou ``None`` se o kernel não vê controlador."""
    bus = root / "sys/bus/thunderbolt/devices"
    if not bus.is_dir():
        return None
    entries = sorted(bus.iterdir())
    domains = [d for d in entries if d.name.startswith("domain")]
    if not domains:
        return None
    tb = Thunderbolt(security=_read(domains[0] / "security"),
                     bolt_installed=shutil.which("boltctl") is not None)
    for d in entries:
        if re.fullmatch(r"\d+-0", d.name):
            gen = _read_int(d / "generation")
            tb.controller = _read(d / "device_name")
            tb.generation = gen
            tb.nvm_version = _read(d / "nvm_version")
        elif _TB_PERIPHERAL.match(d.name):
            auth = _read_int(d / "authorized")
            tb.devices.append(TbDevice(
                d.name, _read(d / "device_name") or d.name, _read(d / "vendor_name"),
                None if auth is None else auth != 0, _read_int(d / "generation"),
            ))
    typec = root / "sys/class/typec"
    if typec.is_dir():
        for d in sorted(typec.iterdir()):
            if re.fullmatch(r"port\d+", d.name):
                tb.ports.append(TypeCPort(
                    d.name, _bracketed(_read(d / "data_role")),
                    _bracketed(_read(d / "power_role")), _read(d / "power_operation_mode"),
                ))
    return tb


@dataclass
class PowerStatus:
    system_profile: str | None = None
    graphics: str | None = None
    thermal_profile: str | None = None      # interface agregada do ACPI
    thermal_choices: list[str] = field(default_factory=list)
    thermal: dict[str, ThermalHandler] = field(default_factory=dict)
    turbo: bool | None = None
    smt: bool | None = None                 # None: sem controle (forceoff etc.)
    smt_control: str | None = None          # valor cru de smt/control
    governor: str | None = None
    epp: str | None = None
    cpu_model: str | None = None
    cpus_present: list[int] = field(default_factory=list)   # threads que o hardware tem
    cpus_online: list[int] = field(default_factory=list)    # threads em uso
    cpu_times: dict[int, tuple[int, int]] = field(default_factory=dict)  # cpu -> (ocupado, total); -1 = soma
    cpu_mhz: float | None = None
    cpu_max_mhz: float | None = None
    cores: list[CpuCore] = field(default_factory=list)
    fans: list[Fan] = field(default_factory=list)
    temps: dict[str, float] = field(default_factory=dict)
    ac_online: bool | None = None
    battery: Battery | None = None
    gpu: Gpu | None = None
    thunderbolt: Thunderbolt | None = None
    helper_installed: bool = False

    def _handler_profiles(self) -> set[str]:
        return {
            HANDLER_ALIASES.get(h.name, {}).get(h.profile, h.profile)
            for h in self.thermal.values()
        }

    @property
    def thermal_mixed(self) -> bool:
        """Os handlers discordam de verdade (alias de nome nao conta)."""
        return len(self._handler_profiles()) > 1

    @property
    def thermal_effective(self) -> str | None:
        """Modo térmico em vigor: resolve o ``custom`` causado só por nomes."""
        if self.thermal_profile == "custom" and self.thermal and not self.thermal_mixed:
            (only,) = self._handler_profiles()
            if only in self.thermal_choices:
                return only
        return self.thermal_profile


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ------------------------------------------------------------- system76

def s76_available() -> bool:
    return shutil.which("busctl") is not None and Path(
        "/usr/share/dbus-1/system.d/com.system76.PowerDaemon.conf"
    ).exists()


def _s76_call(method: str, *args: str) -> str | None:
    p = _run(["busctl", "--system", "call", S76_BUS, S76_PATH, S76_BUS, method, *args])
    if p is None or p.returncode != 0:
        return None
    # Resposta do busctl: 's "Performance"'
    out = p.stdout.strip()
    if out.startswith("s "):
        return out[2:].strip().strip('"')
    return out


def get_system_profile() -> str | None:
    return _s76_call("GetProfile")


def get_graphics() -> str | None:
    return _s76_call("GetGraphics")


def set_system_profile(profile: str) -> None:
    if profile not in S76_PROFILES:
        raise PowerError(f"perfil desconhecido: {profile!r} (use {', '.join(S76_PROFILES)})")
    p = _run(["busctl", "--system", "call", S76_BUS, S76_PATH, S76_BUS, profile], timeout=10)
    if p is None:
        raise PowerError("busctl não encontrado")
    if p.returncode != 0:
        raise PowerError(f"system76-power recusou o perfil {profile}: {p.stderr.strip()}")


# -------------------------------------------------------------- leitura

def read_gpu() -> Gpu | None:
    if shutil.which("nvidia-smi") is None:
        return None
    fmt = "--format=csv,noheader,nounits"
    p = _run(["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS + GPU_OPTIONAL_FIELDS)}", fmt])
    if p is None or p.returncode != 0 or not p.stdout.strip():
        p = _run(["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", fmt])
    if p is None or p.returncode != 0 or not p.stdout.strip():
        return None
    return parse_gpu_csv(p.stdout.strip().splitlines()[0])


# Máscara de clocks_throttle_reasons.active (nvml.h, nvmlClocksEventReason*).
THROTTLE_BITS = {
    0x80: "hw_power_brake",
    0x40: "hw_thermal",
    0x08: "hw_slowdown",
    0x20: "sw_thermal",
    0x04: "sw_power_cap",
    0x10: "sync_boost",
    0x02: "app_clocks",
    0x100: "display_clock",
    0x01: "idle",
}

THROTTLE_LABELS = {
    "hw_power_brake": "Freio de potência (hardware)",
    "hw_thermal": "Térmico (hardware)",
    "hw_slowdown": "Redução por hardware",
    "sw_thermal": "Térmico (software)",
    "sw_power_cap": "Limite de potência",
    "sync_boost": "Sync boost",
    "app_clocks": "Clock fixado",
    "display_clock": "Clock do display",
    "idle": "Ocioso",
}

THROTTLE_HELP = {
    "hw_power_brake": "O sinal de freio de potência foi acionado: fonte ou EC pediram para reduzir.",
    "hw_thermal": "A GPU passou do limite térmico e o hardware reduziu o clock.",
    "hw_slowdown": "Redução imposta pelo hardware (térmica, freio de potência ou outra).",
    "sw_thermal": "O driver reduziu o clock para ficar abaixo da temperatura-alvo.",
    "sw_power_cap": "O driver reduziu o clock para ficar dentro do limite de potência (80 W).",
    "sync_boost": "Clock sincronizado com outras GPUs do grupo.",
    "app_clocks": "Há clocks de aplicação fixados (nvidia-smi -ac).",
    "display_clock": "O clock está preso a um requisito do display.",
    "idle": "Sem carga: a GPU baixou o clock por estar ociosa. Não é limitação.",
}

# Razões que seguram o desempenho de verdade. As demais são informativas.
THROTTLE_LIMITS = frozenset({
    "hw_power_brake", "hw_thermal", "hw_slowdown", "sw_thermal", "sw_power_cap",
})


def throttle_reasons(mask: int | None) -> list[str]:
    if not mask:
        return []
    return [name for bit, name in THROTTLE_BITS.items() if mask & bit]


def _parse_mask(text: str) -> int | None:
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


GPU_FIELDS = (
    "name", "temperature.gpu", "power.draw", "clocks.gr", "utilization.gpu",
    "pstate", "memory.total", "memory.used", "clocks.mem", "clocks.max.gr",
    "clocks.max.mem",
)
# Pedido à parte e opcional: drivers antigos rejeitam o campo e derrubariam
# a consulta inteira.
GPU_OPTIONAL_FIELDS = ("clocks_throttle_reasons.active",)


def parse_gpu_csv(line: str) -> Gpu | None:
    parts = [s.strip() for s in line.split(",")]
    if len(parts) < len(GPU_FIELDS):
        return None

    def num(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:
            return None

    return Gpu(
        name=parts[0], temp_c=num(parts[1]), power_w=num(parts[2]),
        clock_mhz=num(parts[3]), util_pct=num(parts[4]), pstate=parts[5],
        mem_total_mib=num(parts[6]), mem_used_mib=num(parts[7]),
        mem_clock_mhz=num(parts[8]), max_clock_mhz=num(parts[9]),
        max_mem_clock_mhz=num(parts[10]),
        throttle_mask=_parse_mask(parts[11]) if len(parts) > 11 else None,
    )


def read_cores(root: Path, coretemp: Path | None) -> list[CpuCore]:
    """Agrupa as CPUs lógicas por núcleo físico e casa com o coretemp.

    No i7-9750H, cpu0 e cpu6 são o núcleo 0, cpu1 e cpu7 o núcleo 1, e assim
    por diante. O coretemp rotula as temperaturas como "Core N", com N igual ao
    core_id da topologia.
    """
    cores: dict[int, CpuCore] = {}
    base = root / "sys/devices/system/cpu"
    for cpu in base.glob("cpu[0-9]*"):
        try:
            n = int(cpu.name[3:])
        except ValueError:
            continue
        core_id = _read_int(cpu / "topology/core_id")
        if core_id is None:
            continue
        c = cores.setdefault(core_id, CpuCore(core_id))
        f = _read_int(cpu / "cpufreq/scaling_cur_freq")
        if f is not None:
            c.threads[n] = f / 1000
    if coretemp is not None:
        for label in coretemp.glob("temp*_label"):
            text = _read(label) or ""
            if not text.startswith("Core "):
                continue
            try:
                core_id = int(text.split()[1])
            except (IndexError, ValueError):
                continue
            prefix = label.name.removesuffix("_label")
            t = _read_int(coretemp / f"{prefix}_input")
            mx = _read_int(coretemp / f"{prefix}_max")
            c = cores.setdefault(core_id, CpuCore(core_id))
            c.temp_c = t / 1000 if t is not None else None
            c.temp_max_c = mx / 1000 if mx else None
    for c in cores.values():
        c.threads = dict(sorted(c.threads.items()))
    return [cores[k] for k in sorted(cores)]


def clean_cpu_model(raw: str) -> str:
    """``Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz`` -> ``Intel Core i7-9750H``."""
    s = re.sub(r"\((?:R|TM|tm)\)", "", raw)
    s = re.sub(r"\s+@\s*[\d.]+\s*[GM]Hz\s*$", "", s)
    s = re.sub(r"\s+CPU\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_cpu_list(text: str | None) -> list[int]:
    """``0-3,6`` -> ``[0, 1, 2, 3, 6]`` (formato de sys/devices/system/cpu/online)."""
    out: list[int] = []
    for part in (text or "").split(","):
        part = part.strip()
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                out.extend(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
        except ValueError:
            return []
    return out


def read_cpu_times(root: Path) -> dict[int, tuple[int, int]]:
    """Tempo ocupado e total de cada thread, acumulados desde o boot.

    Chave ``-1`` é a soma de todas. Só threads online aparecem em /proc/stat.
    """
    text = _read(root / "proc/stat")
    out: dict[int, tuple[int, int]] = {}
    for line in (text or "").splitlines():
        name, _, rest = line.partition(" ")
        if not name.startswith("cpu"):
            continue
        try:
            f = [int(x) for x in rest.split()[:8]]
            cpu = -1 if name == "cpu" else int(name[3:])
        except ValueError:
            continue
        if len(f) < 4:
            continue
        total = sum(f)
        idle = f[3] + (f[4] if len(f) > 4 else 0)     # idle + iowait
        out[cpu] = (total - idle, total)
    return out


def cpu_usage(prev: dict[int, tuple[int, int]],
              cur: dict[int, tuple[int, int]]) -> dict[int, float]:
    """Uso em % de cada thread entre duas leituras de :func:`read_cpu_times`."""
    out: dict[int, float] = {}
    for cpu, (busy, total) in cur.items():
        if cpu not in prev:
            continue
        db, dt = busy - prev[cpu][0], total - prev[cpu][1]
        if dt > 0:
            out[cpu] = max(0.0, min(100.0, 100 * db / dt))
    return out


def read_status(root: Path = Path("/"), *, with_dbus: bool = True,
                with_gpu: bool = True) -> PowerStatus:
    """Fotografia do estado atual. Só lê; não exige privilégio."""
    fs = Sysfs(root)
    st = PowerStatus()

    st.thermal_profile = _read(fs.legacy_profile())
    st.thermal_choices = (_read(fs.legacy_choices()) or "").split()
    for name, d in fs.profile_handlers().items():
        st.thermal[name] = ThermalHandler(
            name, _read(d / "profile") or "?", (_read(d / "choices") or "").split()
        )

    pstate = fs.pstate()
    nt = _read_int(pstate / "no_turbo")
    st.turbo = None if nt is None else nt == 0

    cpu = root / "sys/devices/system/cpu"
    for line in (_read(root / "proc/cpuinfo") or "").splitlines():
        if line.startswith("model name"):
            st.cpu_model = clean_cpu_model(line.partition(":")[2])
            break
    st.cpus_present = parse_cpu_list(_read(cpu / "present"))
    st.cpus_online = parse_cpu_list(_read(cpu / "online"))
    st.cpu_times = read_cpu_times(root)
    st.smt_control = _read(fs.smt_control())
    st.smt = {"on": True, "off": False}.get(st.smt_control or "")
    st.governor = _read(cpu / "cpu0/cpufreq/scaling_governor")
    st.epp = _read(cpu / "cpu0/cpufreq/energy_performance_preference")
    freqs = [
        _read_int(p) for p in sorted(cpu.glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
    ]
    freqs = [f for f in freqs if f]
    if freqs:
        st.cpu_mhz = sum(freqs) / len(freqs) / 1000
    mx = _read_int(cpu / "cpu0/cpufreq/cpuinfo_max_freq")
    st.cpu_max_mhz = mx / 1000 if mx else None

    aw = fs.hwmon("alienware_wmi")
    if aw is not None:
        for i in range(1, 5):
            rpm = _read_int(aw / f"fan{i}_input")
            if rpm is None:
                continue
            st.fans.append(Fan(
                i, _read(aw / f"fan{i}_label") or f"Ventoinha {i}", rpm,
                _read_int(aw / f"fan{i}_min"), _read_int(aw / f"fan{i}_max"),
            ))
        for i in range(1, 5):
            t = _read_int(aw / f"temp{i}_input")
            if t is not None:
                label = _read(aw / f"temp{i}_label") or f"temp{i}"
                st.temps[f"{label} (EC)"] = t / 1000

    core = fs.hwmon("coretemp")
    st.cores = read_cores(root, core)
    if core is not None:
        t = _read_int(core / "temp1_input")
        if t is not None:
            st.temps["CPU (pacote)"] = t / 1000
    nvme = fs.hwmon("nvme")
    if nvme is not None:
        t = _read_int(nvme / "temp1_input")
        if t is not None:
            st.temps["SSD"] = t / 1000

    ac = root / "sys/class/power_supply/AC/online"
    v = _read_int(ac)
    st.ac_online = None if v is None else v == 1

    bat = fs.battery()
    if bat is not None:
        b = Battery()
        b.capacity = _read_int(bat / "capacity")
        b.status = _read(bat / "status") or "?"
        raw = _read(bat / "charge_types") or ""
        b.charge_types = [t.strip("[]") for t in raw.split()]
        b.charge_type = next((t.strip("[]") for t in raw.split() if t.startswith("[")), None)
        b.start = _read_int(bat / "charge_control_start_threshold")
        b.end = _read_int(bat / "charge_control_end_threshold")
        b.cycles = _read_int(bat / "cycle_count")
        full = _read_int(bat / "charge_full")
        design = _read_int(bat / "charge_full_design")
        if full and design:
            b.health_pct = 100 * full / design
        volt = _read_int(bat / "voltage_now")
        curr = _read_int(bat / "current_now")
        if volt is not None and curr is not None:
            b.power_w = volt * curr / 1e12
        st.battery = b

    if with_dbus and s76_available():
        st.system_profile = get_system_profile()
        st.graphics = get_graphics()
    if with_gpu:
        st.gpu = read_gpu()
    st.thunderbolt = read_thunderbolt(root)
    st.helper_installed = helper_installed()
    return st


# --------------------------------------------------------------- escrita

def helper_installed() -> bool:
    return HELPER_PATH.is_file() and POLKIT_POLICY_PATH.is_file()


def build_helper_args(ops: list[list[str]]) -> list[str]:
    args: list[str] = []
    for i, op in enumerate(ops):
        if i:
            args.append("--")
        args.extend(op)
    return args


def run_helper(ops: list[list[str]]) -> str:
    """Executa uma ou mais operações do helper numa só chamada ao pkexec."""
    if not ops:
        return ""
    if not helper_installed():
        raise HelperMissing(
            "o componente de energia não está instalado "
            f"({HELPER_PATH} e a política polkit)"
        )
    # Já root (ex.: sudo awelc power ...): dispensa o pkexec.
    cmd = [str(HELPER_PATH), *build_helper_args(ops)]
    if os.geteuid() != 0:
        cmd.insert(0, "pkexec")
    p = _run(cmd, timeout=120)
    if p is None:
        raise PowerError("pkexec não encontrado")
    if p.returncode in (126, 127) and "erro:" not in p.stderr:
        raise AuthCancelled("autorização negada ou cancelada")
    if p.returncode != 0:
        msg = p.stderr.strip().removeprefix("erro: ") or f"helper retornou {p.returncode}"
        raise PowerError(msg)
    return p.stdout.strip()


def set_thermal(profile: str) -> str:
    return run_helper([["thermal", profile]])


def set_turbo(on: bool) -> str:
    return run_helper([["turbo", "on" if on else "off"]])


def set_smt(on: bool) -> str:
    return run_helper([["smt", "on" if on else "off"]])


def set_charge_type(kind: str) -> str:
    if kind not in CHARGE_TYPES:
        raise PowerError(f"modo de carga desconhecido: {kind!r}")
    return run_helper([["charge-type", kind]])


def set_charge_thresholds(start: int, end: int) -> str:
    return run_helper([["charge-thresholds", str(start), str(end)]])


# --------------------------------------------------------------- presets

@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    system: str            # perfil do system76-power
    thermal: str           # platform_profile
    turbo: bool

    def helper_ops(self) -> list[list[str]]:
        return [["thermal", self.thermal], ["turbo", "on" if self.turbo else "off"]]


PRESETS: dict[str, Preset] = {p.key: p for p in (
    Preset("silencioso", "Silencioso",
           "Potência contida e sem turbo: menos calor, ventoinha baixa ou parada. "
           "Para leitura, vídeo e bateria.",
           "Battery", "quiet", False),
    Preset("equilibrado", "Equilibrado",
           "O padrão do dia a dia.",
           "Balanced", "balanced", True),
    Preset("desempenho", "Desempenho máximo",
           "Governor performance e limite de potência do firmware no máximo. "
           "Esquenta mais e a ventoinha acompanha — use na tomada.",
           "Performance", "performance", True),
)}


def apply_preset(key: str, status: PowerStatus | None = None) -> list[str]:
    """Aplica um preset.

    O perfil do system76-power vai antes: ao trocar, ele também grava o
    platform_profile, e o modo térmico do preset precisa ficar por cima.
    """
    preset = PRESETS[key]
    status = status or read_status(with_gpu=False)
    log: list[str] = []
    if s76_available():
        set_system_profile(preset.system)
        log.append(f"system76-power: {preset.system}")
    ops = preset.helper_ops()
    if preset.thermal not in status.thermal_choices:
        ops = [op for op in ops if op[0] != "thermal"]
    out = run_helper(ops)
    log.extend(line for line in out.splitlines() if line)
    return log


def matching_preset(st: PowerStatus) -> str | None:
    """Qual preset descreve o estado atual, se algum."""
    for p in PRESETS.values():
        if st.system_profile not in (None, p.system):
            continue
        if st.thermal_effective != p.thermal or st.turbo != p.turbo:
            continue
        return p.key
    return None
