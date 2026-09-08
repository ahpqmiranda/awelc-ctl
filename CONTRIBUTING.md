# Como contribuir

O objetivo mais útil aqui não é código: é **confirmar em qual hardware isto
funciona**. O controlador `187c:0550` aparece em muitos Dell G3, G5, G7, G15,
G16 e Alienware, mas cada modelo tem topologia de zonas própria, e o firmware
varia. Hoje só o Dell G5 5590 foi validado.

Se você tem um desses e roda Linux, meia hora do seu tempo vale mais do que
qualquer refatoração.

## Antes de tudo: o que é seguro e o que não é

**Seguro, não altera nada:**

- `awelc info`
- `tools/collect_report.py`

Ambos usam só `ELC_QUERY` (`0x20`), a classe de leitura do protocolo. A classe
`AwelcDevice(read_only=True)` recusa em tempo de execução qualquer comando fora
dela — não é convenção, é verificado.

**Altera a iluminação, mas não grava nada:**

- `tools/zonemap.py`, `tools/diag_revert.py`, `tools/diag_effects.py`
- `awelc color`, `awelc brightness`, `awelc off` (sem `--persist`)

Usam animações transitórias (`FINISH_PLAY`), que somem quando o controlador
volta ao padrão dele.

**⚠️ Irreversível:**

- `awelc color --persist`, `awelc rainbow`, `awelc clear`
- o botão "Salvar no teclado" e "Gravar animações" da interface

O controlador guarda animações dentro do próprio hardware, e **este firmware não
permite lê-las de volta** (`GET_ANIMATION_BY_ID` e `READ_SERIES` respondem "não
suportado"). Se você já usou o Alienware Command Center no Windows, a
configuração dele está gravada aí e **será destruída sem backup possível**.

Nada disso toca BIOS ou firmware. O pior caso é reconfigurar a iluminação do
zero. Mas é uma escolha sua, e é bom fazê-la sabendo.

## Reportar um modelo novo

### 1. Instale

```bash
git clone https://github.com/ahpqmiranda/awelc-ctl && cd awelc-ctl
uv venv && uv pip install -e ".[gui]"
# ou: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

sudo cp udev/60-awelc.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Se o controlador já estava conectado, desconecte e reconecte — ou reinicie —
para a regra valer.

### 2. Colete o relatório

```bash
.venv/bin/python tools/collect_report.py -o relatorio.md
```

Isso monta um Markdown com modelo, BIOS, kernel, distribuição, saída do `lsusb`,
módulos de kernel carregados, firmware do controlador, id de plataforma e as
respostas cruas de todas as consultas. Só leitura.

### 3. Descubra o mapa de zonas

```bash
.venv/bin/python tools/zonemap.py
```

Acende uma zona por vez, quatro segundos cada, e depois todas juntas. **Anote
qual região do teclado acendeu em cada passo** — é a informação que mais falta
no projeto. No G5 5590 o mapa ainda está em branco.

Se o seu teclado tiver mais de quatro zonas, tente
`--zones 0,1,2,3,4,5` e veja até onde o controlador responde.

### 4. Abra uma issue

Cole o `relatorio.md` e o mapa de zonas. Título no formato
`[modelo] Dell G15 5520 — firmware 2.0.1`.

## As perguntas em aberto

Se quiser ir além do relatório, estas são as lacunas onde ajuda vale mais. Todas
podem ser investigadas com comandos transitórios, sem gravar nada.

### O parâmetro `loop` do `START_SERIES`

```
START_SERIES : 23 <loop:02x> <qtd:04x> <zonas...>
```

Está fixado em `1` desde os projetos originais, e **ninguém sabe o que
significa**. Suspeitamos que seja contagem de repetições, e que isso explique o
blink intermitente que observamos numa cor que deveria ser estática. Testar
`loop` em 0, 1, 8 e 255 com uma cor fixa e relatar o comportamento fecharia a
questão.

### A semântica de `tempo` e `duration`

O upstream fixa `tempo=0x01` sem explicar. Variamos entre 1, 64 e 255 sem
conseguir mapear o resultado com confiança. Se `tempo` controla suavidade da
transição, ajustá-lo daria fades decentes.

### `PULSE` (`0x01`) é o *breath* do Alienware Command Center?

O efeito `PULSE` **nunca foi exercitado por ninguém** — nem por este projeto,
nem pelos dois upstream. É o candidato natural ao *breath* do ACC (pulsa no
brilho máximo, decai até apagar, volta). `tools/diag_effects.py` já o testa;
falta alguém observar e relatar.

### `SET_EVENT` (`0x25`)

Consta das constantes de todos os projetos, propósito desconhecido, nunca
chamado.

## Contribuir com código

```bash
uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Os testes não dependem de hardware: usam um dispositivo falso que registra os
fragmentos enviados. Isso permite fixar sequências de comando sem um teclado por
perto — veja `tests/test_transient.py`, que trava a correção do blink garantindo
que o caminho transitório contém `FINISH_PLAY` e **não** contém `FINISH_SAVE`
nem `SET_DEFAULT`.

Convenções:

- Todo comando novo de escrita ganha teste que fixa a sequência
- Descobertas de protocolo vão para `docs/protocol.md`, com a evidência crua
- Particularidades de modelo vão para um arquivo próprio em `docs/`
- Comentários em português; nomes de código em inglês, como já está
- Nada de `time.sleep` no caminho da interface — use `QTimer`

## Um pedido sobre honestidade técnica

Este projeto documenta o que foi **medido**, e marca explicitamente o que é
suposição. Há uma seção de limitações conhecidas no README admitindo um bug não
resolvido, e o `CHANGELOG` registra que uma funcionalidade foi marcada como
validada cedo demais e teve de ser rebaixada.

Se você testar e não funcionar, isso é uma contribuição tão valiosa quanto o
contrário — talvez mais. Relate o que viu, não o que esperava ver.

## Licença

GPL-3.0-or-later, herdada de
[G15kblight-cli](https://github.com/Vescrity/G15kblight-cli) e
[Dell-G-Series-Controller](https://github.com/cemkaya-mpi/Dell-G-Series-Controller),
dos quais este projeto deriva. Contribuições entram sob a mesma licença.
