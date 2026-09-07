# SPDX-License-Identifier: GPL-3.0-or-later
"""Testes das partes puras: não precisam do hardware."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import constants as C  # noqa: E402
from awelc.cli import parse_color, parse_zones  # noqa: E402
from awelc.device import AwelcDevice, AwelcError, is_unsupported  # noqa: E402
from awelc.protocol import Action, _animation_major, _zones_hex  # noqa: E402


class TestParseColor:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("FF0000", (255, 0, 0)),
            ("#00FF00", (0, 255, 0)),
            ("0x0000FF", (0, 0, 255)),
            ("7733ff", (0x77, 0x33, 0xFF)),
            ("f00", (255, 0, 0)),
            ("#abc", (0xAA, 0xBB, 0xCC)),
        ],
    )
    def test_aceita_formatos(self, text, expected):
        assert parse_color(text) == expected

    @pytest.mark.parametrize("text", ["", "12345", "GGGGGG", "#12345678"])
    def test_rejeita_invalidos(self, text):
        with pytest.raises(Exception):
            parse_color(text)


class TestParseZones:
    def test_lista(self):
        assert parse_zones("0,1,2") == (0, 1, 2)

    def test_uma_zona(self):
        assert parse_zones("3") == (3,)

    def test_all(self):
        assert parse_zones("all") == C.DEFAULT_ZONES

    @pytest.mark.parametrize("text", ["", "a,b", "0,99", "-1"])
    def test_invalidos(self, text):
        with pytest.raises(Exception):
            parse_zones(text)


class TestAction:
    def test_serializacao(self):
        a = Action(C.EFFECT_COLOR, 0xFFFF, 0x01, 0x11, 0x22, 0x33)
        # efeito(1) + duracao(2) + tempo(2) + rgb(3) = 8 bytes = 16 hex
        assert a.to_hex() == "00ffff0001112233"
        assert len(a.to_hex()) == 16

    def test_morph(self):
        a = Action(C.EFFECT_MORPH, 0x00FF, 0x01, 255, 0, 0)
        assert a.to_hex().startswith("0200ff")


class TestZonesHex:
    def test_serializa_indices(self):
        assert _zones_hex([0, 1, 2, 3]) == "00010203"

    def test_zona_unica(self):
        assert _zones_hex([3]) == "03"


class TestAnimationMajor:
    @pytest.mark.parametrize("aid", [0x5B, 0x5C, 0x5D, 0x5E, 0x5F])
    def test_faixa_de_energia(self, aid):
        assert _animation_major(aid) == C.POWER_ANIMATION

    @pytest.mark.parametrize("aid", [0x61, 0x00, 0x99])
    def test_fora_da_faixa(self, aid):
        assert _animation_major(aid) == C.USER_ANIMATION


class TestBuildPacket:
    def test_preenche_ate_33_bytes(self):
        pkt = AwelcDevice._build("2000")
        assert len(pkt) == C.REPORT_LEN
        assert pkt[0] == C.REPORT_ID
        assert pkt[1] == C.ELC_QUERY
        assert pkt[2] == C.GET_VERSION
        assert all(b == 0 for b in pkt[3:])

    def test_recusa_pacote_grande(self):
        with pytest.raises(AwelcError):
            AwelcDevice._build("ff" * 40)


class TestIsUnsupported:
    def test_detecta_resposta_ff(self):
        r = bytearray([0x03, 0x20, 0x04] + [0xFF] * 30)
        assert is_unsupported(r)

    def test_resposta_valida_nao_e_ff(self):
        r = bytearray([0x03, 0x20, 0x00, 0x01, 0x01, 0x07] + [0x00] * 27)
        assert not is_unsupported(r)


class TestReadOnlyGuard:
    """A garantia de somente-leitura deve valer sem hardware presente."""

    def test_recusa_escrita(self):
        dev = AwelcDevice(read_only=True)
        dev._dev = object()  # basta não ser None
        with pytest.raises(AwelcError, match="read_only"):
            dev.command(f"{C.SET_COLOR:02x}ff0000000100")

    def test_permite_consulta(self):
        dev = AwelcDevice(read_only=True)
        assert C.ELC_QUERY in C.READ_COMMANDS
