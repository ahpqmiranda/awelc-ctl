# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from awelc import profiles as P


def make(name="Noite"):
    return P.Profile(name=name, colors={0: "#ff0000", 1: "#00ff00", 2: "#0000ff", 3: "#ffffff"},
                     enabled={0: True, 1: False, 2: True, 3: True},
                     effect=1, duration=300, brightness=40)


def test_roundtrip(tmp_path):
    store = P.ProfileStore(tmp_path)
    store.save(make())
    assert store.names() == ["Noite"]
    assert store.load("Noite") == make()


def test_names_sorted_and_delete(tmp_path):
    store = P.ProfileStore(tmp_path)
    for n in ("b", "A", "c"):
        store.save(make(n))
    assert store.names() == ["A", "b", "c"]
    store.delete("b")
    store.delete("inexistente")
    assert store.names() == ["A", "c"]


def test_ignores_garbage_files(tmp_path):
    (tmp_path / "lixo.json").write_text("{nao e json")
    store = P.ProfileStore(tmp_path)
    store.save(make())
    assert store.names() == ["Noite"]


def test_invalid_data_rejected(tmp_path):
    with pytest.raises(P.ProfileError):
        P.Profile.from_json({"name": "x", "zones": {"0": {"color": "vermelho"}}})
    with pytest.raises(P.ProfileError):
        P.Profile.from_json({"name": "x"})
    with pytest.raises(P.ProfileError):
        P.ProfileStore(tmp_path).load("nao existe")


def test_values_clamped():
    d = make().to_json()
    d["brightness"], d["duration"] = 500, 0
    p = P.Profile.from_json(d)
    assert p.brightness == 100 and p.duration == 1


def test_import_export(tmp_path):
    a, b = P.ProfileStore(tmp_path / "a"), P.ProfileStore(tmp_path / "b")
    a.save(make())
    out = tmp_path / "x.json"
    a.export_file("Noite", out)
    assert json.loads(out.read_text())["schema"] == P.SCHEMA
    assert b.import_file(out).name == "Noite"
    assert b.load("Noite") == make()


def test_slug_blocks_path_traversal(tmp_path):
    store = P.ProfileStore(tmp_path)
    store.save(make("../../etc/passwd"))
    assert all(p.parent == tmp_path for p in tmp_path.rglob("*.json"))
    with pytest.raises(P.ProfileError):
        store.save(make("   "))


def test_normalize_hex():
    assert P.normalize_hex("F00") == "#ff0000"
    assert P.normalize_hex(" #AbCdEf ") == "#abcdef"
    with pytest.raises(P.ProfileError):
        P.normalize_hex("#12345")


def test_presets():
    assert P.solid("#123456", 3) == ["#123456"] * 3
    assert P.gradient("#000000", "#ffffff", 3) == ["#000000", "#808080", "#ffffff"]
    assert P.gradient("#ff0000", "#0000ff", 1) == ["#ff0000"]
    r = P.rainbow(4)
    assert len(set(r)) == 4 and r[0] == "#ff0000"
    assert P.mirror(["a", "b", "c"]) == ["c", "b", "a"]
