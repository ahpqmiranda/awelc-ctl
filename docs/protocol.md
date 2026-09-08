# Protocolo Alienware ELC — o que foi confirmado

Notas de engenharia reversa do controlador **AW-ELC `187c:0550`**, feitas
contra um Dell G5 5590 com firmware **1.1.7**.

Tudo aqui foi **medido**, não inferido de documentação. Onde há suposição, está
marcado como tal.

## Transporte

O controlador é um dispositivo USB HID separado do teclado. Não há driver de
kernel que o exponha — não existe `/sys/class/leds/dell::kbd_backlight` nas
variantes RGB, e isso é normal, não defeito.

A comunicação usa **control transfers HID**, não os endpoints de interrupção:

| Direção | bmRequestType | bRequest | wValue | Tamanho |
|---|---|---|---|---|
| Envio | `0x21` (classe, interface, OUT) | `9` (SET_REPORT) | `0x0200` | 33 bytes |
| Resposta | `0xA1` (classe, interface, IN) | `1` (GET_REPORT) | `0x0100` | 33 bytes |

Todo pacote começa com o report id `0x03` e é preenchido com zeros até 33 bytes.

```
byte 0 : 0x03          report id
byte 1 : comando maior (0x20 = consulta, 0x21+ = escrita)
byte 2+: subcomando e argumentos
```

A resposta ecoa o report id e o comando nos bytes 0–2, com o payload a partir do
byte 3.

Para falar com a interface é preciso desanexar o `usbhid` do kernel. **Sempre
reanexe depois** — o upstream não faz isso e deixa o dispositivo sem driver até
o próximo replug. `AwelcDevice` faz a reanexação num `finally`.

## Comandos maiores

| Código | Nome | Classe |
|---|---|---|
| `0x20` | `ELC_QUERY` | **leitura** |
| `0x21` | `USER_ANIMATION` | escrita |
| `0x22` | `POWER_ANIMATION` | escrita |
| `0x23` | `START_SERIES` | escrita |
| `0x24` | `ADD_ACTION` | escrita |
| `0x25` | `SET_EVENT` | escrita |
| `0x26` | `DIMMING` | escrita |
| `0x27` | `SET_COLOR` | escrita |
| `0x28` | `RESET` | escrita |

`0x20` é a única classe de leitura. Ferramentas de sondagem devem se restringir
a ela — `AwelcDevice(read_only=True)` recusa qualquer outra.

## Consultas — o que este firmware suporta

Medido enviando cada subcomando e observando a resposta:

| Sub | Nome | Firmware 1.1.7 | Resposta observada |
|---|---|---|---|
| `0x00` | `GET_VERSION` | ✅ funciona | `03 20 00 01 01 07` → 1.1.7 |
| `0x01` | `GET_STATUS` | ⚠️ responde zeros | `03 20 01 00 00 00` |
| `0x02` | `GET_PLATFORM` | ✅ funciona | `03 20 02 06 01 04` |
| `0x03` | `GET_ANIMATION_COUNT` | ✅ funciona | `03 20 03 00 02 00 61` |
| `0x04` | `GET_ANIMATION_BY_ID` | ❌ **não suportado** | `03 20 04 ff ff ff…` |
| `0x05` | `READ_SERIES` | ❌ **não suportado** | `03 20 05 ff ff ff…` |

### O achado que mais importa

**`GET_ANIMATION_BY_ID` e `READ_SERIES` não existem neste firmware.** Ambos
aparecem nas constantes do `G15kblight-cli` e do `Dell-G-Series-Controller`, mas
**nenhum dos dois projetos jamais os chamou** — são constantes mortas. Testamos
todas as codificações plausíveis:

- por índice: `20 04 0000`, `0001`, `0002`, `0003`
- por id conhecido: `20 04 005b` … `20 04 0061`
- id em um byte: `20 04 61`, `20 04 5d`
- `READ_SERIES` sem argumento, com id, e com id + índice de série

Todas retornaram payload `0xff` em todos os bytes — a resposta padrão de comando
inválido, distinta das respostas reais dos comandos suportados.

**Consequência prática:** não é possível ler de volta as animações gravadas no
controlador. Como elas persistem no hardware (sobrevivem a reboot e a troca de
sistema operacional), qualquer escrita **destrói irreversivelmente** a
configuração anterior — inclusive a que o Alienware Command Center do Windows
tenha deixado gravada. Não há backup possível pelo Linux.

`GET_ANIMATION_COUNT` continua útil: devolve quantas animações existem e o id da
primeira, o que basta para removê-las em laço.

## Animações

Ids na faixa `0x5b`–`0x60` usam `POWER_ANIMATION` (`0x22`); fora dela,
`USER_ANIMATION` (`0x21`).

| Id | Nome | Quando toca |
|---|---|---|
| `0x5b` | `AC_SLEEP` | suspenso, na tomada |
| `0x5c` | `AC_CHARGED` | na tomada, carregado |
| `0x5d` | `AC_CHARGING` | na tomada, carregando |
| `0x5e` | `DC_SLEEP` | suspenso, na bateria |
| `0x5f` | `DC_ON` | na bateria |
| `0x60` | `DC_LOW` | bateria fraca |
| `0x61` | `DEFAULT_POST_BOOT` | após o boot |

O formato dos comandos de animação é:

```
<maior:02x> <subcomando:04x> <id:04x>
```

Note que o subcomando ocupa **dois** bytes aqui, ao contrário das consultas.

A sequência para gravar uma animação é:

```
REMOVE → START_NEW → START_SERIES(zonas) → ADD_ACTION(1..3 ações)
       → FINISH_SAVE → SET_DEFAULT
```

`ADD_ACTION` aceita no máximo 3 ações por pacote. Cada ação são 8 bytes:

```
<efeito:02x> <duração:04x> <tempo:04x> <r:02x> <g:02x> <b:02x>
```

Efeitos: `0x00` cor estática, `0x01` pulse, `0x02` morph.

## O motor de animação sobrepõe SET_COLOR

Este é o achado mais útil do projeto, e vale para qualquer um que tente usar
este controlador.

**Sintoma:** você manda `SET_COLOR` com a cor desejada, o teclado pisca a cor
certa e volta ao padrão anterior. Reenviando em laço, ele alterna entre as duas.

**Causa:** `SET_COLOR` (`0x27`) pinta os LEDs, mas **não substitui a animação em
execução**. O motor de animação do controlador continua rodando e repinta no
quadro seguinte. Os dois ficam disputando o LED.

### Como isso foi comprovado

O experimento que separa as hipóteses está em `tools/diag_revert.py`. Ele aplica
a cor e **segura o dispositivo aberto**, sem reanexar o `usbhid`:

- se revertesse só ao soltar → a culpa seria do ciclo detach/attach
- se reverter com o dispositivo ainda aberto → é a animação repintando

No G5 5590, observou-se **alternância entre a cor pedida e o branco padrão com o
dispositivo aberto**, o que descarta o detach/attach e confirma o motor de
animação. O branco vinha das animações gravadas pelo Alienware Command Center.

### A solução: FINISH_PLAY

A montagem de uma animação pode terminar de duas formas:

| Encerramento | Efeito |
|---|---|
| `FINISH_SAVE` (`0x02`) | grava no armazenamento; persiste a reboot |
| `FINISH_PLAY` (`0x03`) | **toca agora, sem gravar** |

Terminar com `FINISH_PLAY` faz a animação montada virar a **animação corrente**,
substituindo a que estava rodando. O motor de animação para de repintar porque
agora é a nossa animação que ele está tocando.

Sequência:

```
REMOVE(0xFFFF) → START_NEW(0xFFFF) → START_SERIES(zonas) → ADD_ACTION(...)
               → FINISH_PLAY(0xFFFF)
```

O id `0xFFFF` é `RUNNING_START`, fora da faixa de energia, portanto usa
`USER_ANIMATION`. **Não** se chama `SET_DEFAULT`, e **não** se chama
`FINISH_SAVE` — por isso nada é gravado.

Verificado no G5 5590: a contagem de animações permaneceu em 2 antes e depois,
e a cor ficou firme, sem alternância. Implementado em `Elc.play_zone_map()`.

### Nenhum projeto upstream usa isso

Tanto o `G15kblight-cli` quanto o `Dell-G-Series-Controller` **têm**
`finish_play_animation()` no `elc.py` e **nunca o chamam**. `RUNNING_START` e
`RUNNING_FINISH` só aparecem em linhas comentadas. É código morto que resolvia
um problema que os dois têm — ambos só conseguem trocar cor gravando
permanentemente no controlador.

### Cores diferentes por zona

Várias séries cabem dentro de **uma** animação. Encadeando
`START_SERIES` + `ADD_ACTION` uma vez por grupo de cor antes do encerramento,
obtêm-se cores distintas por zona:

```
START_NEW → [START_SERIES(z_a) → ADD_ACTION(cor_a)]
          → [START_SERIES(z_b) → ADD_ACTION(cor_b)] → FINISH_PLAY
```

`Elc._build_zone_map()` agrupa zonas de mesma cor para emitir o mínimo de séries.

## Zonas

Zonas são uma lista de índices serializada no pacote:

```
START_SERIES : 23 <loop:02x> <qtd:04x> <zonas...>
SET_COLOR    : 27 <r> <g> <b> <qtd:04x> <zonas...>
DIMMING      : 26 <nível:02x> <qtd:04x> <zonas...>
```

**Isto significa que controle por zona existe no protocolo.** O README do
`G15kblight-cli` diz que zonas não são suportadas — isso descreve a limitação da
interface de linha de comando dele, não do protocolo. `SET_COLOR` e `DIMMING`
aceitam qualquer subconjunto de zonas.

O mapa físico (qual índice acende qual região) **varia por modelo** e precisa ser
descoberto empiricamente. Ver `tools/zonemap.py`.

## Brilho

`DIMMING` (`0x26`) recebe um nível de 0 a 100 onde **0 é brilho máximo e 100 é
apagado** — é atenuação, não brilho. A CLI inverte isso para a convenção que o
usuário espera (`awelc brightness 100` = mais claro).

## Identificação de plataforma

`GET_PLATFORM` devolve `06 01` com variante `0x04` no G5 5590. O significado dos
campos é desconhecido; registramos para comparação futura com outros modelos.
Nenhum dos projetos upstream usa essa consulta.

## O que ainda não foi investigado

- `SET_EVENT` (`0x25`) — propósito desconhecido
- `PULSE` como efeito dentro de `FINISH_PLAY` — não exercitado
- `RESET` (`0x28`) — provavelmente restaura padrão de fábrica; **não testado**
- `SPI_FLASH` (`0xFF`) — acesso à flash; não tocar
- efeito `PULSE` (`0x01`) — não exercitado
- `SET_STARTUP` (`0x07`) — não exercitado
