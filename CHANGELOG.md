# Changelog

## 0.3.0 — 2026-09-07

Primeira versão utilizável. Antes disso, o backlight RGB do Dell G5 5590
simplesmente não era controlável no Linux: não há driver de kernel, e os
projetos existentes miravam o Dell G15.

### Adicionado

- Biblioteca `awelc` sobre o protocolo Alienware ELC (`187c:0550`)
- CLI `awelc`: `info`, `color`, `morph`, `brightness`, `off`, `clear`
- GUI PySide6 `awelc-gui` em três abas: zonas e cor, animações, dispositivo
- Regra udev, dispensando `sudo`/`pkexec` no uso diário
- Ferramentas de diagnóstico em `tools/`
- 48 testes, sem dependência de hardware
- Pacote `.deb` (`packaging/build-deb.sh`), com regra udev e atalho do menu

### Descobertas de protocolo

Documentadas em `docs/protocol.md`, todas medidas contra o firmware 1.1.7:

- **`SET_COLOR` é repintado pelo motor de animação.** O comando pinta os LEDs
  mas não substitui a animação em execução, então a cor pisca e volta ao
  padrão. A solução é encerrar a montagem com `FINISH_PLAY` (`0x03`) em vez de
  `FINISH_SAVE` (`0x02`), o que torna a animação montada a corrente sem gravar
  nada. Nenhum projeto upstream faz isso — os dois têm
  `finish_play_animation()` no código e nunca o chamam.
- **`GET_ANIMATION_BY_ID` e `READ_SERIES` não existem neste firmware.** Ambos
  constam das constantes dos dois projetos upstream, nenhum jamais os chamou, e
  o controlador responde `0xff` a todas as codificações testadas. Consequência:
  as animações gravadas não podem ser lidas de volta, e a primeira escrita
  persistente destrói irreversivelmente o que o Alienware Command Center
  deixou.
- **Várias séries cabem numa animação**, encadeando `START_SERIES` +
  `ADD_ACTION` por grupo de cor. É assim que se obtêm cores distintas por zona
  — o README do `G15kblight-cli` afirma que zonas não são suportadas, o que
  descreve a CLI dele, não o protocolo.

### Corrigido em relação ao upstream

- `AwelcDevice` reanexa o `usbhid` num `finally`. O upstream desanexa o driver
  do kernel e nunca religa, deixando o dispositivo órfão até o próximo replug.

### Não resolvido

- Estabilidade da cor ao longo do tempo: blink intermitente não explicado.
  Suspeito principal é o `loop` do `START_SERIES`, fixado em `1` desde o
  upstream e nunca investigado.
- Semântica de `tempo` e `duration`.
- Se `PULSE` (`0x01`) é o *breath* do ACC.
- Mapa físico das zonas: os índices funcionam, mas qual região cada um acende
  não foi registrado.
- Caminho persistente (`--persist`) nunca exercitado — é a única operação
  irreversível do projeto.

### Ambiente de validação

Dell G5 5590 · Pop!_OS 22.04 · kernel 7.1.1-76070101 · KDE Plasma 5.24.7 (X11)
· AW-ELC firmware 1.1.7 · plataforma `06 01` variante `0x04`
