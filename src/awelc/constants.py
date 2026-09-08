# SPDX-License-Identifier: GPL-3.0-or-later
"""Constantes do protocolo Alienware ELC (Enhanced Lighting Controller).

Derivado de G15kblight-cli e Dell-G-Series-Controller (ambos GPL-3.0).
Ver docs/protocol.md para o que foi confirmado empiricamente no G5 5590.
"""

VENDOR_ID = 0x187C
PRODUCT_IDS = (0x0550, 0x0551)

REPORT_ID = 0x03
REPORT_LEN = 33

# --- comandos maiores ---------------------------------------------------
ELC_QUERY = 0x20        # leitura
USER_ANIMATION = 0x21   # escrita
POWER_ANIMATION = 0x22  # escrita
START_SERIES = 0x23     # escrita
ADD_ACTION = 0x24       # escrita
SET_EVENT = 0x25        # escrita
DIMMING = 0x26          # escrita
SET_COLOR = 0x27        # escrita
RESET = 0x28            # escrita
SPI_FLASH = 0xFF        # escrita (nao usado)

READ_COMMANDS = frozenset({ELC_QUERY})

# --- subcomandos de ELC_QUERY -------------------------------------------
GET_VERSION = 0x00
GET_STATUS = 0x01
GET_PLATFORM = 0x02
GET_ANIMATION_COUNT = 0x03
GET_ANIMATION_BY_ID = 0x04   # NAO suportado pelo firmware 1.1.7 - ver docs
READ_SERIES = 0x05           # NAO suportado pelo firmware 1.1.7 - ver docs

# --- subcomandos de animacao --------------------------------------------
START_NEW = 0x01
FINISH_SAVE = 0x02
FINISH_PLAY = 0x03
REMOVE = 0x04
PLAY = 0x05
SET_DEFAULT = 0x06
SET_STARTUP = 0x07

# --- efeitos ------------------------------------------------------------
EFFECT_COLOR = 0x00
EFFECT_PULSE = 0x01
EFFECT_MORPH = 0x02

EFFECT_NAMES = {
    "color": EFFECT_COLOR,
    "pulse": EFFECT_PULSE,
    "morph": EFFECT_MORPH,
}

# --- ids de animacao ----------------------------------------------------
# A faixa 0x5b-0x60 usa POWER_ANIMATION; fora dela, USER_ANIMATION.
AC_SLEEP = 0x5B
AC_CHARGED = 0x5C
AC_CHARGING = 0x5D
DC_SLEEP = 0x5E
DC_ON = 0x5F
DC_LOW = 0x60
DEFAULT_POST_BOOT = 0x61

# Ids especiais fora da faixa normal. Os projetos upstream os declaram mas
# nunca os usam (so aparecem em linhas comentadas). Ver docs/protocol.md.
RUNNING_START = 0xFFFF
RUNNING_FINISH = 0x00FF

POWER_ANIMATION_RANGE = range(0x5B, 0x61)

ANIMATION_NAMES = {
    AC_SLEEP: "AC_SLEEP",
    AC_CHARGED: "AC_CHARGED",
    AC_CHARGING: "AC_CHARGING",
    DC_SLEEP: "DC_SLEEP",
    DC_ON: "DC_ON",
    DC_LOW: "DC_LOW",
    DEFAULT_POST_BOOT: "DEFAULT_POST_BOOT",
}

# --- limites ------------------------------------------------------------
DURATION_MAX = 0xFFFF
DURATION_MIN = 0x0000
DURATION_BATTERY_LOW = 0x00FF
TEMPO_MAX = 0xFF
TEMPO_MIN = 0x01

BRIGHTNESS_MIN = 0    # 0 = brilho total (o campo e "dimming", nao "brightness")
BRIGHTNESS_MAX = 100  # 100 = apagado

# --- zonas --------------------------------------------------------------
# ATENCAO: o mapa fisico varia por modelo. Estes sao os indices que o
# controlador aceita; qual pedaco do teclado cada um acende deve ser
# descoberto com tools/zonemap.py. Ver docs/g5590.md.
DEFAULT_ZONES = (0, 1, 2, 3)
