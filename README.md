# awelc-ctl

Controle do backlight RGB de teclados Alienware/Dell G-Series no Linux, falando
direto com o controlador USB **AW-ELC `187c:0550`**.

Escrito e validado num **Dell G5 5590** — modelo que os projetos existentes não
cobriam.

## Por que existe

O RGB desses teclados não é um LED do kernel. É um dispositivo USB HID separado,
sem driver: não há `/sys/class/leds/dell::kbd_backlight` nas variantes RGB, e o
`alienware_wmi` não cria device algum para iluminação. Sem software em espaço de
usuário, não há controle nenhum.

Já existiam dois projetos para isso —
[G15kblight-cli](https://github.com/Vescrity/G15kblight-cli) e
[Dell-G-Series-Controller](https://github.com/cemkaya-mpi/Dell-G-Series-Controller),
ambos GPL-3.0 — mas mirando o Dell G15. Este aqui parte deles e acrescenta:

- **Reanexação garantida do `usbhid`.** O upstream desanexa o driver do kernel e
  nunca religa, deixando o dispositivo órfão até o próximo replug. Aqui isso vive
  num context manager com `finally`.
- **Zonas e brilho na linha de comando.** O protocolo sempre suportou os dois; as
  CLIs existentes é que não expunham.
- **Modo somente-leitura verificável.** `AwelcDevice(read_only=True)` recusa em
  tempo de execução qualquer comando fora da classe de consulta `0x20`.
- **Protocolo documentado a partir de medição**, incluindo dois comandos que a
  documentação implica existirem e que este firmware simplesmente não tem.

## Instalação

```bash
git clone <url> awelc-ctl && cd awelc-ctl
python3 -m venv .venv && .venv/bin/pip install -e .
```

Para usar sem `sudo`, instale a regra udev:

```bash
sudo cp udev/60-awelc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Sem ela, o dispositivo é `root:root` e todo comando falha com *"sem permissão"*.
Alternativa pontual, sem tocar no sistema: `pkexec .venv/bin/awelc …`

## Uso

```bash
awelc info                        # identifica o controlador (não escreve nada)

awelc color 7733FF                # cor sólida, volátil
awelc color 7733FF --persist      # grava no controlador, sobrevive a reboot
awelc color FF0000 --zones 0,1    # só nas zonas 0 e 1

awelc brightness 60               # 0 = apagado, 100 = máximo
awelc off                         # apaga

awelc morph 7733FF                # ciclo de cores
awelc clear --yes                 # remove todas as animações gravadas
```

## ⚠️ Antes da primeira escrita, leia isto

O controlador guarda animações **dentro do próprio hardware**. Elas persistem a
reboot e a troca de sistema operacional — se você já usou o Alienware Command
Center no Windows, a configuração dele está gravada aí.

**Este firmware não permite lê-las de volta.** Os comandos `GET_ANIMATION_BY_ID`
e `READ_SERIES` retornam "não suportado" (detalhes e evidência em
[`docs/protocol.md`](docs/protocol.md)). Não existe backup possível pelo Linux.

Portanto: **a primeira escrita destrói a configuração anterior de forma
irreversível.** Recuperá-la exigiria o Windows com o ACC. Nada disso toca BIOS ou
firmware — o pior caso é reconfigurar a iluminação do zero.

`awelc info` é seguro e não escreve nada.

## Estado

| Recurso | Situação |
|---|---|
| Identificação do controlador | ✅ validado no G5 5590 |
| Cor sólida (volátil e persistente) | ⚠️ implementado, não exercitado |
| Brilho | ⚠️ implementado, não exercitado |
| Morph | ⚠️ implementado, não exercitado |
| Mapa físico de zonas | ❌ pendente — ver `tools/zonemap.py` |
| Tecla de backlight do notebook | ❌ não corrigível em espaço de usuário |

Sobre a tecla: ela emite um evento WMI (`type 0x0012`) que o `dell_wmi` descarta
antes de chegar ao evdev. Não dá para mapear por `hwdb` nem `setkeycodes` —
exigiria patch no kernel. Contorno: vincular um atalho do desktop a `awelc off`.

## Documentação

- [`docs/protocol.md`](docs/protocol.md) — o protocolo ELC medido, comando a comando
- [`docs/g5590.md`](docs/g5590.md) — particularidades do modelo, permissões, mapa de zonas
- [`docs/probe-g5590-firmware-1.1.7.json`](docs/probe-g5590-firmware-1.1.7.json) — respostas cruas da sondagem inicial

## Licença

GPL-3.0-or-later, herdada dos projetos dos quais deriva.
