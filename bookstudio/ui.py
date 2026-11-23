"""
Visual do BookStudio no terminal: cores, ícones, painéis, menus e barras de progresso.

Tudo o que aparece na tela passa por aqui, para manter um padrão só. Os painéis e menus usam o Rich e o
questionary (menus com as setas, como no Ebook Studio); a cor de destaque é o azul da Apple (#0A84FF).
Sem terminal interativo (scripts, --yes, testes), as perguntas viram texto simples.

  Cores (papéis)                              Ícones
  ─────────────────────────────────────       ───────────────────────────────
  destaque  azul Apple  etapas, linhas, teclas ◆  etapa          ✔  concluído
  texto     branco      valores                ›  pergunta       ✖  erro
  suave     cinza       dicas                  •  item de lista  ▲  aviso
  sucesso   verde                              ●  informação     ♪  amostra de voz
  aviso     amarelo                            📚 livros   🎙 voz   🌐 idioma
  erro      vermelho                           📝 texto    💾 pasta 🎧 formato
"""
import os
import re
import shutil
import sys
import threading
import unicodedata

from bookstudio.i18n import palavras, t

AZUL = "#0A84FF"  # azul da Apple (modo escuro; também lê bem em fundo claro)

# ---------------------------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------------------------
if os.name == "nt":
    os.system("")  # liga as cores ANSI no console do Windows
try:  # acentos e símbolos mesmo em terminais com locale antigo (LANG=C no Linux, página de código no Windows)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CORES = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb"
# Emojis só onde o terminal os desenha bem (Terminal do Windows, macOS, Linux gráfico); no console antigo do
# Windows e no console de texto do Linux (TERM=linux), símbolos simples
if os.name == "nt":
    EMOJI = bool(os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"))
else:
    EMOJI = os.environ.get("TERM") != "linux"


def _c(codigo):
    return (lambda t: f"\033[{codigo}m{t}\033[0m") if CORES else (lambda t: str(t))


destaque = _c("38;2;10;132;255")       # azul da Apple
destaque_forte = _c("1;38;2;10;132;255")
texto = _c("97")          # branco
suave = _c("90")          # cinza
sucesso = _c("92")        # verde
aviso = _c("93")          # amarelo
erro = _c("91")           # vermelho
negrito = _c("1")

ICONE = {
    "etapa": "◆", "pergunta": "›", "item": "•", "info": "●", "ok": "✔", "erro": "✖", "aviso": "▲", "amostra": "♪",
    "livros": "📚" if EMOJI else "■", "voz": "🎙" if EMOJI else "♪", "idioma": "🌐" if EMOJI else "◎",
    "texto": "📝" if EMOJI else "≡", "pasta": "💾" if EMOJI else "▣", "formato": "🎧" if EMOJI else "♫",
    "traduzir": "🔁" if EMOJI else "⇄", "converter": "🚀" if EMOJI else "►",
}


def largura():
    # Uma coluna a menos que o terminal: linha que encosta na borda quebra e desalinha o painel de progresso
    return max(40, min(110, shutil.get_terminal_size((100, 30)).columns - 1))


def largura_painel():
    """Largura do painel de progresso: o terminal inteiro (uma coluna a menos, para a linha não quebrar)."""
    return max(40, shutil.get_terminal_size((100, 30)).columns - 1)


def visivel(t):
    """Largura do texto na tela (sem os códigos de cor; emojis e ideogramas ocupam 2 colunas)."""
    import re
    limpo = re.sub(r"\033\[[0-9;]*m", "", str(t))
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 0 if unicodedata.combining(c) else 1 for c in limpo)


def quebrar(texto, n):
    """Quebra o texto em linhas de até n colunas na tela (por palavras; ideogramas, que não têm espaço, por caractere)."""
    import re
    linhas, atual = [], ""
    for pedaco in re.findall(r"\S+\s*", str(texto)):
        if visivel(atual + pedaco.rstrip()) <= n:
            atual += pedaco
            continue
        if atual.strip():
            linhas.append(atual.rstrip())
        atual = ""
        for c in pedaco:  # pedaço maior que a linha (frase em chinês): quebra onde couber
            if visivel(atual + c) > n:
                linhas.append(atual.rstrip())
                atual = ""
            atual += c
    if atual.strip():
        linhas.append(atual.rstrip())
    return linhas or [""]


def cortar(t, n):
    t = str(t)
    if visivel(t) <= n:
        return t
    saida = ""
    for c in t:
        if visivel(saida + c + "…") > n:
            break
        saida += c
    return saida + "…"


# ---------------------------------------------------------------------------------------------
# Blocos de tela
# ---------------------------------------------------------------------------------------------
# Rich (painéis, regras, tabelas) e questionary (menus com setas). Sem eles (instalação antiga), o visual simples.
try:
    from rich.console import Console
    from rich.markup import escape as _esc
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
    console = Console(theme=Theme({"destaque": f"bold {AZUL}", "azul": AZUL, "suave": "grey62", "ok": "bold green",
                                   "aviso": "bold yellow", "erro": "bold red"}),
                      no_color=not CORES, highlight=False, emoji=False)
except ImportError:  # pragma: no cover
    console = None
try:
    import questionary
    from questionary import Choice, Style
    ESTILO = Style([("qmark", f"fg:{AZUL} bold"), ("question", "bold"), ("answer", f"fg:{AZUL} bold"),
                    ("pointer", f"fg:{AZUL} bold"), ("highlighted", f"fg:{AZUL} bold"), ("selected", "fg:#30D158"),
                    ("instruction", "fg:#8E8E93 italic"), ("text", ""), ("disabled", "fg:#636366 italic")])
except ImportError:  # pragma: no cover
    questionary = None


def interativo():
    """Menus com setas só num terminal de verdade (e com o questionary instalado). BOOKSTUDIO_SIMPLES=1 força o texto."""
    return (questionary is not None and sys.stdin.isatty() and sys.stdout.isatty()
            and not os.environ.get("BOOKSTUDIO_SIMPLES"))


class Cancelado(Exception):
    """Esc ou Ctrl+C num menu: volta para o passo anterior (ou para o menu principal)."""


def banner(nome, versao, slogan):
    if console is None:
        print(f"\n  {ICONE['livros']}  {nome}  v{versao}\n  {slogan}")
        return
    titulo = Text.assemble((f"{ICONE['livros']}  {nome}", f"bold {AZUL}"), (f"  v{versao}", "grey62"), "\n", (slogan, "grey62"))
    console.print()
    console.print(Panel(titulo, border_style=AZUL, padding=(1, 2), expand=False))


def etapa(numero, total, titulo, icone=None):
    marcador = f"{ICONE[icone]}  " if icone else ""
    rotulo = t("step", n=numero, total=total) if total else ""
    if console is None:
        print(f"\n  {ICONE['etapa']} {rotulo}  {marcador}{titulo}")
        return
    console.print()
    console.rule(Text.assemble((f"{rotulo}  " if rotulo else "", "grey62"), (f"{marcador}{titulo}", "bold")),
                 style=AZUL, align="left")


def secao(titulo, icone=None):
    """Título de uma parte do app (sem número de etapa)."""
    etapa(0, 0, titulo, icone)


def _imprimir(icone, msg, estilo=None):
    if console is None:
        print(f"  {icone} {msg}")
    else:
        console.print(Text.assemble("  ", (icone, estilo or AZUL), " ", Text.from_ansi(str(msg), style=estilo or "")))


def info(msg):
    _imprimir(ICONE["info"], msg, AZUL)


def ok(msg):
    _imprimir(ICONE["ok"], msg, "green")


def alerta(msg):
    _imprimir(ICONE["aviso"], msg, "yellow")


def falha(msg):
    _imprimir(ICONE["erro"], msg, "red")


def dica(msg):
    if console is None:
        print("  " + str(msg))
    else:
        console.print(Text.assemble("  ", Text.from_ansi(str(msg), style="grey62")))


def cartao(titulo, linhas, cor=AZUL):
    """Quadro com um título e os dados em duas colunas (rótulo, valor)."""
    if console is None:
        print(f"  {titulo}")
        for r, v in linhas:
            print(f"    {r}: {v or '—'}")
        return
    grade = Table.grid(padding=(0, 2))
    grade.add_column(style="grey62")
    grade.add_column()
    for r, v in linhas:
        grade.add_row(r, Text(str(v)) if v else Text("—", style="grey62"))
    console.print(Panel(grade, title=Text(cortar(titulo, 80), style="bold"), title_align="left",
                        border_style=cor, expand=False, padding=(0, 1)))


def aguarde(msg, fn, *args, **kwargs):
    """Roda fn mostrando um indicador animado com a mensagem (ler o livro, detectar o idioma, testar a chave...)."""
    if console is None or not CORES:
        dica(msg)
        return fn(*args, **kwargs)
    with console.status(Text(msg, style=AZUL), spinner="dots", spinner_style=AZUL):
        return fn(*args, **kwargs)


def painel(linhas, titulo="", cor=AZUL):
    """Quadro com linhas de texto (resultado de uma tarefa, resumo)."""
    if console is None:
        print("\n".join(f"  {l}" for l in ([titulo] if titulo else []) + list(linhas)))
        return
    corpo = Text("\n").join(Text.from_ansi(str(l)) for l in linhas)
    console.print(Panel(corpo, title=Text(titulo, style="bold") if titulo else None, title_align="left",
                        border_style=cor, expand=False, padding=(0, 1)))


# ---------------------------------------------------------------------------------------------
# Perguntas
# ---------------------------------------------------------------------------------------------
def _ask(pergunta):
    resposta = pergunta.ask()
    if resposta is None:  # Esc ou Ctrl+C
        raise Cancelado
    return resposta


def menu(rotulo, opcoes, padrao=None, busca=False):
    """Menu com setas. `opcoes`: [(rótulo, valor)] ou [(rótulo, valor, detalhe)]. Devolve o valor escolhido.
    Sem terminal interativo: lista numerada."""
    itens = [(o[0], o[1], o[2] if len(o) > 2 else "") for o in opcoes]
    if not interativo():
        i = escolher(rotulo, [(r, d) for r, _, d in itens],
                     padrao=next((k for k, (_, v, _) in enumerate(itens) if v == padrao), 0))
        return itens[i][1]
    escolhas = [Choice(title=[("", r)] + ([("class:instruction", f"  {d}")] if d else []), value=v) for r, v, d in itens]
    extra = dict(use_search_filter=True, use_jk_keys=False, instruction=t("type_to_filter")) if busca else {"instruction": " "}
    padrao_choice = next((c for c in escolhas if c.value == padrao), None)
    return _ask(questionary.select(rotulo, choices=escolhas, default=padrao_choice, style=ESTILO, qmark="›", **extra))


def perguntar(rotulo, padrao=""):
    """Texto livre; Enter mantém o padrão."""
    if interativo():
        return (_ask(questionary.text(rotulo, default=str(padrao or ""), style=ESTILO, qmark="›")) or "").strip() or padrao
    extra = suave(f" [{padrao}]") if padrao else ""
    try:
        r = input("  " + destaque(ICONE["pergunta"]) + " " + rotulo + extra + " ").strip()
    except EOFError:
        r = ""
    return r or padrao


def sim_nao(rotulo, padrao=True):
    if interativo():
        return _ask(questionary.confirm(rotulo, default=padrao, style=ESTILO, qmark="›"))
    op = t("yn_default_yes") if padrao else t("yn_default_no")
    # Respostas no idioma da interface e, em qualquer idioma, y/yes e n/no
    sim = set(palavras("yes_words")) | {"y", "yes"}
    nao = set(palavras("no_words")) | {"n", "no"}
    while True:
        try:
            r = input("  " + destaque(ICONE["pergunta"]) + " " + rotulo + " " + suave(f"({op})") + " ").strip().lower()
        except EOFError:
            r = ""
        if not r:
            return padrao
        if r in sim:
            return True
        if r in nao:
            return False
        alerta(t("answer_yes_no"))


def escolher(rotulo, opcoes, padrao=0, extras=None, colunas=1):
    """Lista de opções. `opcoes`: [(rótulo, detalhe)]. `extras`: {tecla: descrição} para ações como amostras.
    Devolve o índice escolhido ou (tecla, argumento) quando é uma ação extra. No terminal interativo é um menu
    com setas (com busca quando a lista é longa); as ações extras aparecem no fim do menu."""
    if interativo():
        opcoes_menu = [(r, i, d) for i, (r, d) in enumerate(opcoes)]
        for tecla, desc in (extras or {}).items():
            rotulo_extra = re.sub(r"<[^>]+>\s*", "", desc).strip()
            opcoes_menu.append((f"{ICONE['amostra'] if tecla == 'o' else '›'} {rotulo_extra}", ("extra", tecla), ""))
        valor = menu(rotulo, opcoes_menu, padrao=padrao, busca=len(opcoes) > 12)
        if isinstance(valor, tuple):
            tecla = valor[1]
            # Ação com argumento ("<n>  ouvir a voz n", "r <região>"): pergunta o argumento
            arg = perguntar(re.sub(r"^\S+\s+", "", extras[tecla]).strip() or tecla) if "<" in extras[tecla] else ""
            return tecla, arg
        return valor
    w = largura()
    if colunas > 1:
        larg = (w - 6) // colunas
        for i in range(0, len(opcoes), colunas):
            linha = ""
            for j, (r, d) in enumerate(opcoes[i:i + colunas]):
                k = i + j
                marca = destaque_forte("›") if k == padrao else " "
                cel = f"{marca}{destaque(str(k + 1).rjust(3))} {cortar(r, larg - 7)}"
                linha += cel + " " * max(1, larg - visivel(cel))
            print("  " + linha.rstrip())
    else:
        for k, (r, d) in enumerate(opcoes):
            marca = destaque_forte("›") if k == padrao else " "
            print(f"  {marca}{destaque(str(k + 1).rjust(3))}  {r}" + (suave(f"  {d}") if d else ""))
    if extras:
        print("  " + suave("   " + "   ".join(f"{destaque(t)} {d}" for t, d in extras.items())))
    while True:
        r = perguntar(rotulo, str(padrao + 1))
        partes = r.split(maxsplit=1)
        if extras and partes and partes[0].lower() in extras:
            return partes[0].lower(), (partes[1] if len(partes) > 1 else "")
        if r.isdigit() and 1 <= int(r) <= len(opcoes):
            return int(r) - 1
        alerta(t("enter_number", max=len(opcoes)))


# ---------------------------------------------------------------------------------------------
# Progresso das conversões (uma linha por livro, redesenhada no lugar)
# ---------------------------------------------------------------------------------------------
def tamanho(n_bytes):
    """1536 -> "1.5 KB"; 3_200_000 -> "3.1 MB"."""
    n = float(n_bytes or 0)
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}"
        n /= 1024


def tempo(segundos):
    s = max(1, int(round(segundos)))
    return f"{s // 3600}h {(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


class EstimativaTempo:
    """Tempo restante de uma tarefa a partir da porcentagem ao longo do tempo.

    O ritmo é recalculado só quando chega um dado novo: reta ajustada aos últimos 90 s (o ritmo de agora) e a
    média desde o começo (60/40). A estimativa nova entra aos poucos (meia-vida de ~15 s) para não pular, e
    entre um dado e outro o tempo desce de segundo em segundo, em tempo real (não depende de quantas vezes a
    tela é desenhada)."""
    JANELA_S = 90.0

    def __init__(self):
        self.amostras = []      # (instante, porcentagem)
        self.restante = None    # segundos, no instante self.base
        self.base = None

    def registrar(self, pct, agora=None):
        import math
        import time as _t
        agora = agora or _t.time()
        if self.amostras and pct <= self.amostras[-1][1]:
            return
        self.amostras.append((agora, pct))
        inicio = self.amostras[0]
        self.amostras = [inicio] + [a for a in self.amostras[1:] if agora - a[0] <= self.JANELA_S]
        janela = [a for a in self.amostras if agora - a[0] <= self.JANELA_S]
        ritmos = []
        if len(janela) >= 3 and janela[-1][0] - janela[0][0] >= 8:  # reta pelos pontos recentes
            n = len(janela)
            mt = sum(x for x, _ in janela) / n
            mp = sum(y for _, y in janela) / n
            var = sum((x - mt) ** 2 for x, _ in janela)
            if var > 0:
                ritmos.append((sum((x - mt) * (y - mp) for x, y in janela) / var, 0.6))
        if agora - inicio[0] >= 8 and pct > inicio[1]:
            ritmos.append(((pct - inicio[1]) / (agora - inicio[0]), 0.4))
        ritmos = [(r, p) for r, p in ritmos if r > 0]
        if not ritmos:
            return
        ritmo = sum(r * p for r, p in ritmos) / sum(p for _, p in ritmos)
        calculado = (100 - pct) / ritmo
        atual = self.agora_restante(agora)
        if atual is None:
            self.restante = calculado
        else:
            peso = 1 - math.exp(-(agora - (self.base or agora)) / 15) if self.base else 1
            self.restante = atual + (calculado - atual) * max(0.15, peso)
        self.base = agora

    def agora_restante(self, agora=None):
        import time as _t
        if self.restante is None:
            return None
        return max(0.0, self.restante - ((agora or _t.time()) - self.base))

    def texto(self):
        r = self.agora_restante()
        return t("calculating") if r is None else tempo(r)


class PainelProgresso:
    """Uma tarefa (ou um livro) por item, em duas linhas: título, barra, % e tempo restante; embaixo, a etapa,
    as partes processadas, a velocidade do download, o tamanho baixado e as conexões. Redesenhado no lugar;
    o tempo restante anda a cada segundo."""

    def __init__(self, livros):
        self.livros = livros  # lista de dicts com id e titulo
        self.estado = {l["id"]: {"pct": 0.0, "etapa": t("queued"), "partes": "", "vel": "", "tamanho": "", "fim": None,
                                 "estimativa": EstimativaTempo()} for l in livros}
        self.desenhado = 0
        self.pausado = False
        self.ultimo = 0.0
        self.trava = threading.Lock()  # o painel é desenhado pela thread dos eventos e pela principal

    def desenhar_se_preciso(self, intervalo=1.0):
        """Redesenha se passou `intervalo` desde o último desenho (só no terminal, onde o painel é redesenhado no lugar)."""
        import time as _t
        if CORES and _t.time() - self.ultimo >= intervalo:
            self.desenhar()

    def pausar(self):
        """Para de redesenhar (enquanto uma pergunta ocupa a tela); o progresso continua sendo anotado."""
        with self.trava:
            self.pausado = True

    def retomar(self):
        """Volta a desenhar. Depois de uma pausa, o painel recomeça abaixo do que foi escrito na tela."""
        with self.trava:
            if self.pausado:
                self.desenhado = 0
            self.pausado = False
        self.desenhar()

    def atualizar(self, fid, **dados):
        if fid in self.estado:
            e = self.estado[fid]
            e.update({k: v for k, v in dados.items() if v is not None})
            if "pct" in dados and dados["pct"] is not None:
                e["estimativa"].registrar(float(dados["pct"]))

    def _restante(self, e):
        return e["estimativa"].texto()

    def _linhas(self, livro):
        """Duas linhas por livro, na largura toda do terminal: título, barra, % e tempo restante; embaixo,
        a etapa, a contagem, a velocidade do download e as conexões. Nada passa da borda (senão a linha
        quebra e o painel se desalinha ao ser redesenhado)."""
        e = self.estado[livro["id"]]
        w = largura_painel()
        larg_titulo = min(48, max(14, w // 3))
        titulo = cortar(livro["titulo"], larg_titulo)
        titulo += " " * max(0, larg_titulo - visivel(titulo))
        recuo = " " * 5
        if e["fim"] == "ok":
            return ["  " + sucesso(ICONE["ok"]) + " " + texto(titulo) + "  " + sucesso(t("done"))]
        if e["fim"] == "erro":
            return ["  " + erro(ICONE["erro"]) + " " + texto(titulo) + "  " + erro(cortar(e["etapa"], w - larg_titulo - 6))]
        fim_linha = f"  {e['pct']:5.1f}%  " + self._restante(e)
        n = max(8, min(40, w - larg_titulo - visivel(fim_linha) - 6))  # a barra usa o espaço que sobra
        cheio = int(round(e["pct"] / 100 * n))
        barra = destaque("━" * cheio) + suave("━" * (n - cheio))
        linha1 = "  " + destaque(ICONE["info"]) + " " + texto(titulo) + "  " + barra + negrito(fim_linha)
        detalhes = [e["etapa"]] + [x for x in (e["partes"], e["vel"], e["tamanho"]) if x]
        linha2 = recuo + suave(cortar(" · ".join(detalhes), w - len(recuo) - 1))
        return [linha1, linha2]

    def desenhar(self):
        with self.trava:
            if self.pausado:
                return
            if CORES and self.desenhado:
                sys.stdout.write(f"\r\033[{self.desenhado}A")  # volta ao início do painel
            total = 0
            for livro in self.livros:
                for linha in self._linhas(livro):
                    sys.stdout.write("\033[2K" + linha + "\n" if CORES else linha + "\n")
                    total += 1
            if CORES and total < self.desenhado:  # um livro terminou e o painel encolheu: limpa as linhas que sobraram
                for _ in range(self.desenhado - total):
                    sys.stdout.write("\033[2K\n")
                sys.stdout.write(f"\033[{self.desenhado - total}A")
            sys.stdout.flush()
            self.desenhado = total
            import time as _t
            self.ultimo = _t.time()
