# SPDX-License-Identifier: GPL-3.0-or-later
"""Perfis de iluminacao (zonas, efeito, brilho) salvos em JSON, e presets de cor.

Sem dependencia de Qt: so hex "#rrggbb" e dicts, para poder testar sem GUI.
Os perfis ficam em ``$XDG_CONFIG_HOME/awelc-ctl/profiles/<nome>.json``.
"""
from __future__ import annotations

import colorsys
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ProfileError(ValueError):
    pass


def profiles_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "awelc-ctl" / "profiles"


def normalize_hex(value: str) -> str:
    v = value.strip()
    if not v.startswith("#"):
        v = "#" + v
    if len(v) == 4:  # #rgb
        v = "#" + "".join(ch * 2 for ch in v[1:])
    if not _HEX.match(v):
        raise ProfileError(f"cor invalida: {value!r}")
    return v.lower()


@dataclass
class Profile:
    name: str
    colors: dict[int, str]            # indice da zona -> "#rrggbb"
    enabled: dict[int, bool] = field(default_factory=dict)
    effect: int = 0
    duration: int = 0xFF
    brightness: int = 100

    def to_json(self) -> dict:
        return {
            "schema": SCHEMA,
            "name": self.name,
            "zones": {
                str(i): {"color": c, "enabled": self.enabled.get(i, True)}
                for i, c in sorted(self.colors.items())
            },
            "effect": self.effect,
            "duration": self.duration,
            "brightness": self.brightness,
        }

    @classmethod
    def from_json(cls, data: dict) -> "Profile":
        try:
            zones = data["zones"]
            colors = {int(i): normalize_hex(z["color"]) for i, z in zones.items()}
            enabled = {int(i): bool(z.get("enabled", True)) for i, z in zones.items()}
            return cls(
                name=str(data["name"]),
                colors=colors,
                enabled=enabled,
                effect=int(data.get("effect", 0)),
                duration=max(1, min(0xFFFF, int(data.get("duration", 0xFF)))),
                brightness=max(0, min(100, int(data.get("brightness", 100)))),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, ProfileError):
                raise
            raise ProfileError(f"perfil invalido: {exc}") from exc


def slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    if not s:
        raise ProfileError("nome de perfil vazio")
    return s[:64]


class ProfileStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory) if directory else profiles_dir()

    def _path(self, name: str) -> Path:
        return self.dir / f"{slug(name)}.json"

    def names(self) -> list[str]:
        out = []
        for p in sorted(self.dir.glob("*.json")) if self.dir.exists() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8"))["name"])
            except (OSError, ValueError, KeyError):
                continue  # arquivo estranho na pasta: ignora
        return sorted(out, key=str.lower)

    def save(self, profile: Profile) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(profile.name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile.to_json(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, name: str) -> Profile:
        try:
            data = json.loads(self._path(name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProfileError(f"perfil nao encontrado: {name}") from None
        except (OSError, ValueError) as exc:
            raise ProfileError(f"perfil ilegivel: {exc}") from exc
        return Profile.from_json(data)

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass

    def import_file(self, path: Path) -> Profile:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfileError(f"arquivo ilegivel: {exc}") from exc
        profile = Profile.from_json(data)
        self.save(profile)
        return profile

    def export_file(self, name: str, path: Path) -> None:
        profile = self.load(name)
        Path(path).write_text(json.dumps(profile.to_json(), indent=2, ensure_ascii=False),
                              encoding="utf-8")


# ---------------------------------------------------------------- presets

def _hsv_hex(h: float, s: float = 1.0, v: float = 1.0) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _lerp_hex(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def rainbow(n: int) -> list[str]:
    return [_hsv_hex(i / max(n, 1) * 0.8) for i in range(n)]  # vermelho -> violeta


def gradient(a: str, b: str, n: int) -> list[str]:
    a, b = normalize_hex(a), normalize_hex(b)
    if n == 1:
        return [a]
    return [_lerp_hex(a, b, i / (n - 1)) for i in range(n)]


def solid(color: str, n: int) -> list[str]:
    return [normalize_hex(color)] * n


def mirror(colors: list[str]) -> list[str]:
    """Espelha: a ultima zona recebe a cor da primeira, e assim por diante."""
    return list(reversed(colors))
