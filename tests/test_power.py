# SPDX-License-Identifier: GPL-3.0-or-later
"""Gerenciamento de energia contra um sysfs falso, sem hardware nem root."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import power as P  # noqa: E402
from awelc import power_helper as H  # noqa: E402


def w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def sysroot(tmp_path: Path) -> Path:
    """Reproduz o G5 5590 como estava em 2026-10-26: handlers discordando."""
    r = tmp_path
    w(r / "sys/firmware/acpi/platform_profile", "custom\n")
    w(r / "sys/firmware/acpi/platform_profile_choices", "cool quiet balanced performance\n")
    pp = r / "sys/class/platform-profile"
    w(pp / "platform-profile-0/name", "alienware-wmi\n")
    w(pp / "platform-profile-0/profile", "balanced-performance\n")
    w(pp / "platform-profile-0/choices",
      "cool quiet balanced balanced-performance performance custom\n")
    w(pp / "platform-profile-1/name", "dell-pc\n")
    w(pp / "platform-profile-1/profile", "performance\n")
    w(pp / "platform-profile-1/choices", "cool quiet balanced performance\n")

    hw = r / "sys/class/hwmon"
    w(hw / "hwmon5/name", "coretemp\n")
    w(hw / "hwmon5/temp1_input", "62000\n")
    w(hw / "hwmon5/temp1_label", "Package id 0\n")
    w(hw / "hwmon5/temp2_label", "Core 0\n")
    w(hw / "hwmon5/temp2_input", "57000\n")
    w(hw / "hwmon5/temp2_max", "100000\n")
    w(hw / "hwmon5/temp3_label", "Core 1\n")
    w(hw / "hwmon5/temp3_input", "71000\n")
    w(hw / "hwmon7/name", "alienware_wmi\n")
    w(hw / "hwmon7/fan1_label", "CPU Fan\n")
    w(hw / "hwmon7/fan1_input", "4930\n")
    w(hw / "hwmon7/fan1_min", "2400\n")
    w(hw / "hwmon7/fan1_max", "5000\n")
    w(hw / "hwmon7/fan1_boost", "0\n")
    w(hw / "hwmon7/temp1_label", "CPU\n")
    w(hw / "hwmon7/temp1_input", "65000\n")

    ps = r / "sys/devices/system/cpu/intel_pstate"
    w(ps / "no_turbo", "0\n")
    w(r / "sys/devices/system/cpu/smt/control", "on\n")
    cpu = r / "sys/devices/system/cpu/cpu0/cpufreq"
    w(cpu / "scaling_governor", "performance\n")
    w(cpu / "energy_performance_preference", "performance\n")
    w(cpu / "scaling_cur_freq", "2500000\n")
    w(cpu / "cpuinfo_max_freq", "4500000\n")
    # i7-9750H: cpu0/cpu2 dividem o núcleo 0; cpu1 é o núcleo 1
    base = r / "sys/devices/system/cpu"
    w(base / "cpu0/topology/core_id", "0\n")
    w(base / "cpu1/topology/core_id", "1\n")
    w(base / "cpu1/cpufreq/scaling_cur_freq", "3200000\n")
    w(base / "cpu2/topology/core_id", "0\n")
    w(base / "cpu2/cpufreq/scaling_cur_freq", "4100000\n")

    w(base / "present", "0-2\n")
    w(base / "online", "0-2\n")
    w(r / "proc/cpuinfo", "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz\n")
    w(r / "proc/stat", "cpu  300 0 100 600 0 0 0 0\ncpu0 100 0 50 250 0 0 0 0\n"
                       "cpu1 100 0 25 275 0 0 0 0\ncpu2 100 0 25 75 0 0 0 0\nintr 1 2 3\n")
    tb = r / "sys/bus/thunderbolt/devices"
    w(tb / "domain0/security", "user\n")
    w(tb / "0-0/device_name", "Dell G5 5590\n")
    w(tb / "0-0/generation", "3\n")
    w(tb / "0-0/nvm_version", "40.0\n")
    tc = r / "sys/class/typec/port0"
    w(tc / "data_role", "[host] device\n")
    w(tc / "power_role", "[source] sink\n")
    w(tc / "power_operation_mode", "default\n")

    ps_ = r / "sys/class/power_supply"
    w(ps_ / "AC/type", "Mains\n")
    w(ps_ / "AC/online", "1\n")
    bat = ps_ / "BAT0"
    w(bat / "type", "Battery\n")
    w(bat / "capacity", "100\n")
    w(bat / "status", "Full\n")
    w(bat / "charge_types", "Trickle Fast Standard [Adaptive] Custom\n")
    w(bat / "charge_control_start_threshold", "50\n")
    w(bat / "charge_control_end_threshold", "90\n")
    w(bat / "charge_full", "3068000\n")
    w(bat / "charge_full_design", "3100000\n")
    return r


def read(root: Path, rel: str) -> str:
    return (root / rel).read_text().strip()


# ------------------------------------------------------------- leitura

def test_status_reads_everything(sysroot):
    st = P.read_status(sysroot, with_dbus=False, with_gpu=False)
    assert st.thermal_profile == "custom"
    assert st.thermal_choices == ["cool", "quiet", "balanced", "performance"]
    assert st.thermal["alienware-wmi"].profile == "balanced-performance"
    # "custom" aqui e so diferenca de nome entre os handlers: e "performance"
    assert not st.thermal_mixed
    assert st.thermal_effective == "performance"
    assert st.turbo is True
    assert st.cpu_mhz == (2500 + 3200 + 4100) / 3
    assert [(f.label, f.rpm) for f in st.fans] == [("CPU Fan", 4930)]
    assert st.temps == {"CPU (EC)": 65.0, "CPU (pacote)": 62.0}
    assert st.ac_online is True
    b = st.battery
    assert b.charge_type == "Adaptive"
    assert b.charge_types == ["Trickle", "Fast", "Standard", "Adaptive", "Custom"]
    assert (b.start, b.end) == (50, 90)
    assert round(b.health_pct) == 99


def test_status_survives_missing_hardware(tmp_path):
    st = P.read_status(tmp_path, with_dbus=False, with_gpu=False)
    assert st.thermal == {} and st.fans == [] and st.battery is None
    assert st.turbo is None and st.thermal_profile is None


def test_matching_preset(sysroot):
    st = P.read_status(sysroot, with_dbus=False, with_gpu=False)
    st.system_profile = "Performance"
    assert P.matching_preset(st) == "desempenho"  # "custom" por nomes = performance
    st.thermal["dell-pc"].profile = "quiet"        # discordancia de verdade
    assert st.thermal_mixed and st.thermal_effective == "custom"
    assert P.matching_preset(st) is None
    st.thermal["dell-pc"].profile = "performance"
    st.thermal_profile = "performance"
    st.system_profile = "Balanced"
    assert P.matching_preset(st) is None          # sistema discorda do preset


def test_effective_thermal_when_aggregate_is_plain(sysroot):
    st = P.read_status(sysroot, with_dbus=False, with_gpu=False)
    st.thermal_profile = "balanced"
    for h in st.thermal.values():
        h.profile = "balanced"
    assert not st.thermal_mixed and st.thermal_effective == "balanced"


def test_presets_never_touch_fans():
    for p in P.PRESETS.values():
        assert {op[0] for op in p.helper_ops()} <= set(H.OPS)
    assert "fan-boost" not in H.OPS


# -------------------------------------------------------------- helper

def test_thermal_uses_aggregate_interface(sysroot):
    assert H.main(["thermal", "performance"], root=sysroot) == 0
    assert read(sysroot, "sys/firmware/acpi/platform_profile") == "performance"
    # A agregada é quem propaga; os handlers não são tocados diretamente.
    assert read(sysroot, "sys/class/platform-profile/platform-profile-0/profile") \
        == "balanced-performance"


def test_thermal_rejects_handler_only_profile(sysroot):
    # balanced-performance só existe no alienware-wmi: gravá-lo desalinharia
    # os handlers, que é justamente o estado "custom" a evitar.
    assert H.main(["thermal", "balanced-performance"], root=sysroot) == 2
    assert read(sysroot, "sys/firmware/acpi/platform_profile") == "custom"


def test_thermal_falls_back_to_handlers(sysroot):
    (sysroot / "sys/firmware/acpi/platform_profile").unlink()
    assert H.main(["thermal", "quiet"], root=sysroot) == 0
    for n in (0, 1):
        assert read(sysroot, f"sys/class/platform-profile/platform-profile-{n}/profile") == "quiet"


def test_turbo_is_inverted(sysroot):
    assert H.main(["turbo", "off"], root=sysroot) == 0
    assert read(sysroot, "sys/devices/system/cpu/intel_pstate/no_turbo") == "1"
    assert H.main(["turbo", "on"], root=sysroot) == 0
    assert read(sysroot, "sys/devices/system/cpu/intel_pstate/no_turbo") == "0"


def test_cpu_identity_and_threads(sysroot):
    st = P.read_status(sysroot, with_dbus=False, with_gpu=False)
    assert st.cpu_model == "Intel Core i7-9750H"
    assert st.cpus_present == st.cpus_online == [0, 1, 2]


def test_parse_cpu_list():
    assert P.parse_cpu_list("0-3,6,8-9") == [0, 1, 2, 3, 6, 8, 9]
    assert P.parse_cpu_list("0") == [0]
    assert P.parse_cpu_list("") == [] and P.parse_cpu_list(None) == []
    assert P.parse_cpu_list("a-b") == []


def test_cpu_usage_from_deltas(sysroot):
    t0 = P.read_status(sysroot, with_dbus=False, with_gpu=False).cpu_times
    assert t0[-1] == (400, 1000)           # ocupado = total - idle - iowait
    assert t0[2] == (125, 200)
    # +100 ticks em cada thread: cpu0 100% ocupada, cpu1 50%, cpu2 ociosa
    t1 = {-1: (550, 1300), 0: (250, 500), 1: (175, 500), 2: (125, 300)}
    u = P.cpu_usage(t0, t1)
    assert u[0] == 100.0 and u[1] == 50.0 and u[2] == 0.0
    assert round(u[-1]) == 50


def test_cpu_usage_ignores_threads_without_baseline_or_time():
    assert P.cpu_usage({0: (1, 2)}, {0: (1, 2), 5: (3, 4)}) == {}


def test_thunderbolt_idle(sysroot):
    tb = P.read_status(sysroot, with_dbus=False, with_gpu=False).thunderbolt
    assert tb.security == "user" and tb.generation == 3
    assert tb.controller == "Dell G5 5590" and tb.nvm_version == "40.0"
    assert tb.devices == []
    assert [(p.data_role, p.power_role, p.power_mode) for p in tb.ports] == \
        [("host", "source", "default")]


def test_thunderbolt_lists_peripherals_not_host(sysroot):
    d = sysroot / "sys/bus/thunderbolt/devices"
    for rel, val in (("0-1/device_name", "Dock\n"), ("0-1/vendor_name", "Acme\n"),
                     ("0-1/authorized", "0\n"), ("0-1.1/device_name", "Disk\n"),
                     ("0-1.1/authorized", "1\n")):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(val)
    tb = P.read_status(sysroot, with_dbus=False, with_gpu=False).thunderbolt
    assert [(x.id, x.name, x.authorized) for x in tb.devices] == \
        [("0-1", "Dock", False), ("0-1.1", "Disk", True)]


def test_thunderbolt_absent(tmp_path):
    assert P.read_status(tmp_path, with_dbus=False, with_gpu=False).thunderbolt is None


def test_smt_toggle(sysroot):
    ctl = "sys/devices/system/cpu/smt/control"
    assert H.main(["smt", "off"], root=sysroot) == 0
    assert read(sysroot, ctl) == "off"
    assert P.read_status(sysroot, with_dbus=False, with_gpu=False).smt is False
    assert H.main(["smt", "on"], root=sysroot) == 0
    assert read(sysroot, ctl) == "on"
    assert P.read_status(sysroot, with_dbus=False, with_gpu=False).smt is True


@pytest.mark.parametrize("state", ["forceoff", "notsupported"])
def test_smt_locked_is_refused(sysroot, state):
    (sysroot / "sys/devices/system/cpu/smt/control").write_text(state + "\n")
    assert H.main(["smt", "off"], root=sysroot) == 2
    assert P.read_status(sysroot, with_dbus=False, with_gpu=False).smt is None


def test_smt_missing_is_refused(sysroot):
    (sysroot / "sys/devices/system/cpu/smt/control").unlink()
    assert H.main(["smt", "off"], root=sysroot) == 2


@pytest.mark.parametrize("args", [
    ["charge-thresholds", "40", "80"],     # início abaixo do mínimo
    ["charge-thresholds", "50", "101"],    # fim acima do máximo
    ["charge-thresholds", "78", "80"],     # janela menor que 5
    ["charge-thresholds", "50", "x"],
    ["charge-type", "Turbo"],
    ["turbo", "maybe"],
    ["smt", "maybe"],
    ["fan-boost", "1", "255"],             # removido de propósito
    ["rm", "-rf", "/"],
])
def test_rejects_invalid(sysroot, args):
    before = read(sysroot, "sys/class/power_supply/BAT0/charge_control_end_threshold")
    assert H.main(args, root=sysroot) == 2
    assert read(sysroot, "sys/class/power_supply/BAT0/charge_control_end_threshold") == before


def test_batch_is_all_or_nothing_on_validation(sysroot):
    # A segunda operação é inválida: a primeira não pode ter sido aplicada.
    assert H.main(["turbo", "off", "--", "charge-type", "Turbo"], root=sysroot) == 2
    assert read(sysroot, "sys/devices/system/cpu/intel_pstate/no_turbo") == "0"


def test_charge_thresholds_order(sysroot):
    bat = "sys/class/power_supply/BAT0/"
    assert H.main(["charge-thresholds", "60", "80"], root=sysroot) == 0
    assert (read(sysroot, bat + "charge_control_start_threshold"),
            read(sysroot, bat + "charge_control_end_threshold")) == ("60", "80")
    # Janela inteira acima do fim atual: o fim precisa subir primeiro.
    assert H.main(["charge-thresholds", "85", "95"], root=sysroot) == 0
    assert (read(sysroot, bat + "charge_control_start_threshold"),
            read(sysroot, bat + "charge_control_end_threshold")) == ("85", "95")


def test_charge_type(sysroot):
    assert H.main(["charge-type", "Custom"], root=sysroot) == 0
    assert read(sysroot, "sys/class/power_supply/BAT0/charge_types") == "Custom"


def test_helper_is_stdlib_only():
    """O helper roda como root com /usr/bin/python3 -I: nada do pacote."""
    src = Path(H.__file__).read_text()
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert all(
        ln.split()[1].split(".")[0] in {"__future__", "sys", "pathlib"} for ln in imports
    ), imports


def test_build_helper_args():
    assert P.build_helper_args([["thermal", "quiet"], ["turbo", "off"]]) == \
        ["thermal", "quiet", "--", "turbo", "off"]


# ------------------------------------------------------- CPU e GPU detalhadas

def test_cores_group_threads_and_match_coretemp(sysroot):
    st = P.read_status(sysroot, with_dbus=False, with_gpu=False)
    assert [c.core_id for c in st.cores] == [0, 1]
    c0, c1 = st.cores
    assert c0.threads == {0: 2500, 2: 4100}
    assert c0.mhz == 4100            # o núcleo físico roda no clock da thread mais rápida
    assert (c0.temp_c, c0.temp_max_c) == (57, 100)
    assert (c1.temp_c, c1.mhz) == (71, 3200)


def test_parse_gpu_csv():
    g = P.parse_gpu_csv(
        "NVIDIA GeForce GTX 1660 Ti, 55, 32.30, 1560, 37, P0, 6144, 782, 6000, 2100, 6001"
    )
    assert g.name == "NVIDIA GeForce GTX 1660 Ti"
    assert (g.mem_used_mib, g.mem_total_mib) == (782, 6144)
    assert round(g.mem_pct, 1) == 12.7
    assert (g.clock_mhz, g.max_clock_mhz) == (1560, 2100)
    assert (g.mem_clock_mhz, g.max_mem_clock_mhz) == (6000, 6001)


def test_parse_gpu_csv_throttle_mask():
    base = "GPU, 70, 79.5, 1500, 99, P0, 6144, 900, 6000, 2100, 6001"
    g = P.parse_gpu_csv(base + ", 0x0000000000000004")
    assert g.throttle == ["sw_power_cap"] and g.limiting == ["sw_power_cap"]
    g = P.parse_gpu_csv(base + ", 0x0000000000000061")      # idle + sw_thermal + hw_thermal
    assert g.throttle == ["hw_thermal", "sw_thermal", "idle"]
    assert g.limiting == ["hw_thermal", "sw_thermal"]       # ocioso não é limitação
    g = P.parse_gpu_csv(base + ", 0x0000000000000001")
    assert g.throttle == ["idle"] and g.limiting == []
    assert P.parse_gpu_csv(base + ", 0x0").throttle == []


def test_parse_gpu_csv_without_throttle_field():
    g = P.parse_gpu_csv("GPU, 70, 79.5, 1500, 99, P0, 6144, 900, 6000, 2100, 6001")
    assert g.throttle_mask is None and g.limiting == []
    assert P.parse_gpu_csv(
        "GPU, 70, 79.5, 1500, 99, P0, 6144, 900, 6000, 2100, 6001, [N/A]").throttle_mask is None


def test_parse_gpu_csv_tolerates_na():
    # nvidia-smi devolve "[N/A]" para campos sem suporte (ex.: power.limit no mobile)
    g = P.parse_gpu_csv("GPU, [N/A], [N/A], 300, 0, P8, 6144, [N/A], 405, 2100, 6001")
    assert g.temp_c is None and g.mem_used_mib is None and g.mem_pct is None
    assert P.parse_gpu_csv("GPU, 50") is None
