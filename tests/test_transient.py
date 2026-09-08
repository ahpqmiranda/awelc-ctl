# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressao do bug 'a cor pisca e volta ao padrao'.

Causa: SET_COLOR pinta o LED mas nao substitui a animacao em execucao, entao o
motor de animacao do controlador repinta por cima. O caminho correto e montar
uma animacao e encerra-la com FINISH_PLAY (toca sem gravar) em vez de
FINISH_SAVE.

Estes testes fixam a sequencia de comandos para que a correcao nao regrida.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from awelc import constants as C  # noqa: E402
from awelc.device import AwelcError  # noqa: E402
from awelc.protocol import Elc  # noqa: E402


class FakeDevice:
    """Registra os fragmentos enviados, sem hardware."""

    def __init__(self):
        self.sent: list[str] = []

    def command(self, fragment_hex: str) -> bytearray:
        self.sent.append(fragment_hex.lower())
        return bytearray(33)

    def majors(self) -> list[int]:
        return [int(f[:2], 16) for f in self.sent]

    def has_fragment_starting(self, prefix: str) -> bool:
        return any(f.startswith(prefix.lower()) for f in self.sent)


@pytest.fixture
def elc():
    dev = FakeDevice()
    return Elc(dev), dev


class TestPlayZoneMap:
    def test_termina_em_finish_play_e_nunca_finish_save(self, elc):
        e, dev = elc
        e.play_zone_map({0: (255, 0, 0), 1: (0, 255, 0)})

        finish_play = f"{C.USER_ANIMATION:02x}{C.FINISH_PLAY:04x}"
        finish_save = f"{C.USER_ANIMATION:02x}{C.FINISH_SAVE:04x}"
        assert dev.has_fragment_starting(finish_play), "deve encerrar com FINISH_PLAY"
        assert not dev.has_fragment_starting(finish_save), "nao pode gravar"

    def test_nunca_marca_como_default(self, elc):
        """SET_DEFAULT gravaria a animacao como padrao - transitorio nao faz isso."""
        e, dev = elc
        e.play_zone_map({0: (1, 2, 3)})
        set_default = f"{C.USER_ANIMATION:02x}{C.SET_DEFAULT:04x}"
        assert not dev.has_fragment_starting(set_default)

    def test_nao_usa_set_color(self, elc):
        """SET_COLOR e justamente o comando que a animacao repinta por cima."""
        e, dev = elc
        e.play_zone_map({0: (255, 0, 255)})
        assert C.SET_COLOR not in dev.majors()

    def test_agrupa_zonas_de_mesma_cor_numa_serie(self, elc):
        e, dev = elc
        e.play_zone_map({0: (255, 0, 0), 1: (255, 0, 0), 2: (0, 0, 255)})
        series = [f for f in dev.sent if f.startswith(f"{C.START_SERIES:02x}")]
        assert len(series) == 2, "duas cores distintas -> duas series"

    def test_cores_distintas_geram_series_distintas(self, elc):
        e, dev = elc
        e.play_zone_map({0: (1, 1, 1), 1: (2, 2, 2), 2: (3, 3, 3)})
        series = [f for f in dev.sent if f.startswith(f"{C.START_SERIES:02x}")]
        assert len(series) == 3

    def test_usa_id_transitorio(self, elc):
        e, dev = elc
        e.play_zone_map({0: (1, 2, 3)})
        assert any(f"{C.RUNNING_START:04x}" in f for f in dev.sent)

    def test_recusa_mapa_vazio(self, elc):
        e, _ = elc
        with pytest.raises(AwelcError):
            e.play_zone_map({})

    def test_morph_gera_tres_acoes(self, elc):
        e, dev = elc
        e.play_zone_map({0: (10, 20, 30)}, effect=C.EFFECT_MORPH)
        actions = [f for f in dev.sent if f.startswith(f"{C.ADD_ACTION:02x}")]
        assert len(actions) == 1
        # 2 hex do comando + 3 acoes de 16 hex cada
        assert len(actions[0]) == 2 + 3 * 16


class TestStoreZoneMap:
    def test_termina_em_finish_save_e_set_default(self, elc):
        e, dev = elc
        e.store_zone_map(C.AC_CHARGED, {0: (255, 0, 0)})
        major = C.POWER_ANIMATION  # 0x5c esta na faixa de energia
        assert dev.has_fragment_starting(f"{major:02x}{C.FINISH_SAVE:04x}")
        assert dev.has_fragment_starting(f"{major:02x}{C.SET_DEFAULT:04x}")

    def test_nao_usa_finish_play(self, elc):
        e, dev = elc
        e.store_zone_map(C.AC_CHARGED, {0: (255, 0, 0)})
        major = C.POWER_ANIMATION
        assert not dev.has_fragment_starting(f"{major:02x}{C.FINISH_PLAY:04x}")

    def test_recusa_mapa_vazio(self, elc):
        e, _ = elc
        with pytest.raises(AwelcError):
            e.store_zone_map(C.AC_CHARGED, {})


class TestPlayTransient:
    def test_delega_para_zone_map(self, elc):
        e, dev = elc
        e.play_transient([0, 1, 2, 3], 9, 8, 7)
        series = [f for f in dev.sent if f.startswith(f"{C.START_SERIES:02x}")]
        assert len(series) == 1, "cor unica -> uma serie so"
        assert dev.has_fragment_starting(f"{C.USER_ANIMATION:02x}{C.FINISH_PLAY:04x}")


class TestSomenteEscritaEsperada:
    """Nenhum comando fora do conjunto esperado deve escapar."""

    def test_conjunto_de_comandos(self, elc):
        e, dev = elc
        e.play_zone_map({0: (1, 2, 3), 1: (4, 5, 6)})
        esperados = {
            C.USER_ANIMATION,   # remove / start_new / finish_play
            C.START_SERIES,
            C.ADD_ACTION,
        }
        assert set(dev.majors()) <= esperados
