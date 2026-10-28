# Changelog

## 0.4.0 — 2026-10-28

O backlight vira uma central de controle: energia, modo térmico e monitor. A aba
"Zonas e cor" ganha perfis salvos em disco e atalhos de cor.

### Adicionado

- Módulo `awelc.power`: leitura de modo térmico, turbo, SMT, governor/EPP,
  frequência, ventoinhas, temperaturas, bateria e GPU (via `nvidia-smi`)
- Três modos prontos — silencioso, equilibrado, desempenho máximo —
  combinando perfil do `system76-power`, modo térmico do firmware e turbo
- Monitor: a CPU é identificada pelo modelo (como a GPU), mostra o uso total e
  o uso de cada thread, e o resumo do pacote fica embaixo dos gráficos. Threads
  desligadas pelo SMT saem da lista
- Monitor: razões de limitação de clock da GPU (limite de potência, térmico por
  software ou hardware, freio de potência), em `awelc power status` e embaixo do
  grupo GPU, com explicação no tooltip. "Ocioso" aparece como informativo, não
  como limitação
- Thunderbolt somente leitura (`awelc power status` e aba Energia): geração,
  nível de segurança, dispositivos e autorização, e papéis da porta USB-C
- CLI `awelc power`: `status`, `preset`, `system`, `thermal`, `turbo`, `smt`,
  `charge`, `install`
- Abas **Energia** e **Monitor** na GUI, que passa a abrir nelas
- Monitor com temperatura e clock **por núcleo físico** (threads agrupadas
  pela topologia) e GPU com VRAM usada/total e clocks de núcleo e memória;
  os mesmos dados no `awelc power status`
- Helper privilegiado `awelc-power-helper` com política polkit; entra no
  `.deb` em `/usr/libexec/awelc-ctl/`
- 24 testes contra sysfs falso, incluindo validação do helper
- **Perfis**: salvar, carregar, excluir, importar e exportar a configuração da
  aba Zonas e cor (cor e liga/desliga de cada zona, efeito, duração e brilho).
  Ficam em `~/.config/awelc-ctl/profiles/<nome>.json`. Carregar atualiza a
  tela e, com a pré-visualização ligada, aplica no teclado; não grava no
  controlador. Lógica em `awelc.profiles`, sem dependência de Qt
- **Atalhos de cor**: "cor da anterior" em cada zona e, para todas de uma vez,
  cor única, arco-íris, degradê (da primeira à última zona) e espelhar
- **Cores recentes**: as últimas 16 cores usadas entram nos slots
  personalizados do seletor e persistem entre sessões. Botão direito no botão
  de cor copia o hex ou cola um hex da área de transferência
- **Teclado ilustrativo** no topo da aba, com as zonas coloridas; clicar numa
  faixa abre o seletor daquela zona
- 10 testes novos: perfis, presets de cor e leitura do modo térmico efetivo

### Corrigido

- O modo térmico aparecia como "Misto" (e a GUI avisava de handlers em
  discordância) quando a agregada reportava `custom` só porque `alienware-wmi`
  chama de `balanced-performance` o que `dell-pc` chama de `performance`.
  Agora esse par é lido como `performance`: o preset "Desempenho máximo" volta
  a ser reconhecido e a mensagem de discordância só aparece quando os handlers
  divergem de fato. O `awelc power status` mostra a agregada entre parênteses

### Validado

- Escrita real do modo térmico pelo helper com pkexec, no G5 5590
  (2026-10-28): `balanced` deixou agregada e os dois handlers em `balanced`;
  `performance` restaurou o par original `balanced-performance` +
  `performance`. Turbo, SMT e carga da bateria seguem só contra sysfs falso

### Decisões

- **Sem controle de ventoinha.** `fanN_boost` é recusado pelo firmware e o PWM
  manual rende +1 RPM — medido no awcc-fanctl. A ventoinha responde ao modo
  térmico.
- **Modo térmico pela interface agregada** (`/sys/firmware/acpi/platform_profile`),
  nunca por um handler isolado: gravar só no `alienware-wmi` deixa o
  `dell-pc` discordando, que é a origem do estado `custom`.
- O preset aplica o perfil do `system76-power` antes do modo térmico, porque
  o primeiro reescreve o segundo.
- O desenho do teclado usa quatro faixas verticais **só como ilustração**: o
  mapa físico das zonas do G5 5590 continua sem confirmação
  (`docs/g5590.md`). Ele passa a refletir a realidade quando o `zonemap.py`
  for rodado e a tabela preenchida
- O nome do perfil vira nome de arquivo sem barras nem caracteres especiais,
  então um nome como `../x` não escreve fora da pasta de perfis

### Não validado

- Escrita real como root pelo helper para turbo, SMT e carga da bateria. Os
  testes cobrem a lógica contra um sysfs falso; falta exercitar no hardware
- Carregar perfil com pré-visualização ligada no teclado real. A GUI foi
  exercitada só sem tela (offscreen), sem o controlador

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
