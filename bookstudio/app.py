"""BookStudio: ponto de entrada, argumentos e menu principal.

    bookstudio                      abre o menu (criar audiobook, traduzir, converter, texto sincronizado...)
    bookstudio livro.epub [...]     cria o audiobook direto, como o antigo Audiobook Generator de terminal
    bookstudio --help               todas as opções
"""
import argparse
import os
import shutil
import sys

from bookstudio import NOME, VERSAO, atualizacao, i18n, trabalhos, ui
from bookstudio.arquivos import TECLA_IDIOMA, escolher_idioma_interface
from bookstudio.audiobook.opcoes import DESTINOS_TEXTO, FORMATOS_CLI, QUALIDADES, SEPARACOES
from bookstudio.config import caminho_config, ler_config, salvar_config
from bookstudio.i18n import t
from bookstudio.idiomas import IDIOMAS, VOZES


def _idioma_pedido(argv):
    """--lang xx / --lang=xx, lido antes do argparse para a própria ajuda (--help) sair no idioma certo."""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def _idioma_valido(valor):
    if valor not in i18n.IDIOMAS:
        raise argparse.ArgumentTypeError(", ".join(i18n.IDIOMAS))
    return valor


def _argumentos():
    parser = argparse.ArgumentParser(prog="bookstudio", description=t("cli_description"))
    parser.add_argument("arquivos", nargs="*", help=t("cli_files_help"))
    parser.add_argument("-o", "--saida", "--output", help=t("cli_output_help"))
    parser.add_argument("--lang", nargs="?", const=True, type=_idioma_valido, metavar="LANG",
                        help=t("cli_lang_help", langs=", ".join(i18n.IDIOMAS)))
    parser.add_argument("--voice", help=t("cli_voice_help"))
    parser.add_argument("--format", choices=list(FORMATOS_CLI), help=t("cli_format_help"))
    parser.add_argument("--quality", choices=QUALIDADES, help=t("cli_quality_help"))
    parser.add_argument("--translate", choices=i18n.IDIOMAS, metavar="LANG", help=t("cli_translate_help"))
    sync = parser.add_mutually_exclusive_group()
    sync.add_argument("--sync", dest="texto", action="store_true", default=None, help=t("cli_sync_help"))
    sync.add_argument("--no-sync", dest="texto", action="store_false", help=t("cli_no_sync_help"))
    parser.add_argument("--sync-detail", choices=[s[0] for s in SEPARACOES], help=t("cli_sync_detail_help"))
    parser.add_argument("--sync-save", choices=[d[0] for d in DESTINOS_TEXTO], help=t("cli_sync_save_help"))
    parser.add_argument("-y", "--yes", action="store_true", help=t("cli_yes_help"))
    leve = parser.add_mutually_exclusive_group()
    leve.add_argument("--lite", dest="leve", action="store_true", default=None, help=t("cli_lite_help"))
    leve.add_argument("--no-lite", dest="leve", action="store_false", help=t("cli_no_lite_help"))
    parser.add_argument("--no-update-check", action="store_true", help=t("cli_no_update_help"))
    parser.add_argument("--version", action="version", version=f"{NOME} {VERSAO}")
    return parser.parse_args()


def avisar_atualizacao(verificacao, sem_perguntas):
    """Versão nova no GitHub: mostra o que mudou e oferece atualizar pelo terminal.
    Devolve True quando atualizou (o app fecha para abrir de novo já na versão nova)."""
    release = verificacao.resultado() if verificacao else None
    if not release:
        return False
    nova = str(release.get("tag_name", "")).lstrip("v")
    tipo = atualizacao.tipo_de_instalacao()
    itens = atualizacao.traduzir(atualizacao.novidades(release), i18n.atual)
    ui.painel([t("update_available", new=nova, current=VERSAO)] + [f"{ui.ICONE['item']} {i}" for i in itens]
              + ["", t("update_download_page", url=release.get("html_url") or atualizacao.PAGINA)],
              titulo=t("update_whats_new"), cor=ui.AZUL)
    depois = t("update_later", command=atualizacao.comando_texto(tipo))
    if sem_perguntas or not sys.stdin.isatty() or not ui.sim_nao(t("update_q"), padrao=True):
        ui.dica(depois)
        return False
    ui.dica(t("updating"))
    resultado = atualizacao.atualizar(release, tipo)
    if resultado == "ok":
        ui.ok(t("update_done", version=nova))
        return True
    if resultado == "janela":
        ui.ok(t("update_windows_window"))
        return True
    ui.falha(t("update_failed"))
    ui.dica("  " + atualizacao.comando_texto(tipo))
    return False


# Arquivos que a versão 1.x deixava na pasta principal; a atualização por pacote copia os novos por cima sem
# apagar nada, então eles são removidos aqui (só fora de um clone do git, que cuida disso sozinho)
SOBRAS_1X = ["ui.py", "i18n.py", "atualizacao.py", "gerar_idiomas.py", "atualizar_motor.py", "__init__.py",
             "requirements-dev.txt", "Audiobook Generator (terminal).bat", "Audiobook Generator (terminal).command",
             "audiobook-cli.sh"]
PASTAS_1X = ["motor", "dados", "__pycache__"]


def limpar_sobras():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(raiz, ".git")) or "site-packages" in raiz or not os.path.isfile(os.path.join(raiz, "bookstudio", "app.py")):
        return
    for nome in SOBRAS_1X:
        try:
            os.remove(os.path.join(raiz, nome))
        except OSError:
            pass
    for nome in PASTAS_1X:
        shutil.rmtree(os.path.join(raiz, nome), ignore_errors=True)


def migrar_config_1x(config):
    """Configuração do Audiobook Generator de terminal (1.x): vozes, formato, pasta... continuam valendo."""
    if config or not os.name:
        return config
    if os.name == "nt":
        antiga = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Audiobook Generator Terminal", "config.json")
    elif sys.platform == "darwin":
        antiga = os.path.expanduser("~/Library/Application Support/Audiobook Generator Terminal/config.json")
    else:
        antiga = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "audiobook-generator-terminal", "config.json")
    try:
        import json
        with open(antiga, encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            config.update(dados)
            salvar_config(config)
    except (OSError, ValueError):
        pass
    return config


# ---------------------------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------------------------
def _ic(emoji, simples="›"):
    return emoji if ui.EMOJI else simples


OPCOES = [
    ("audiobook", "🎧", "menu_audiobook", "menu_audiobook_desc"),
    ("traduzir_audiobook", "🌍", "menu_translate_audiobook", "menu_translate_audiobook_desc"),
    ("texto", "📝", "menu_sync", "menu_sync_desc"),
    ("traduzir_texto", "🌍", "menu_translate_sync", "menu_translate_sync_desc"),
    ("ebook_audio", "📖", "menu_ebook_from_audio", "menu_ebook_from_audio_desc"),
    ("converter", "🔄", "menu_convert", "menu_convert_desc"),
    ("traduzir", "🌐", "menu_translate", "menu_translate_desc"),
    ("metadados", "✏️", "menu_metadata", "menu_metadata_desc"),
    ("config", "⚙️", "menu_settings", "menu_settings_desc"),
    ("idioma", "🗣️", "menu_language", ""),
    ("sair", "🚪", "menu_exit", ""),
]


def _ebook():
    from bookstudio.ebook.settings import Settings, setup_logging
    from bookstudio.ebook.storage.cache import Store
    from bookstudio.ebook.settings import CACHE_DIR
    setup_logging()
    import logging
    logging.getLogger(__name__).info("iniciando BookStudio %s", VERSAO)
    return Settings.load(), Store(CACHE_DIR / "cache.sqlite3")


def _retomar_pendentes(args, config, settings, store):
    """Trabalhos pausados (audiobooks, textos sincronizados e traduções): oferece continuar."""
    lista = [("audio", j) for j in trabalhos.pendentes()] + [("traducao", j) for j in store.pending_jobs()]
    if not lista or args.yes or not sys.stdin.isatty():
        return
    opcoes = []
    for tipo, j in lista[:8]:
        if tipo == "audio":
            icone = "🎧" if j["tipo"] == "audiobook" else "📝"
            opcoes.append((f"{_ic(icone)} {j['titulo']}", (tipo, j), t("paused_" + j["tipo"])))
        else:
            ident, params = j
            opcoes.append((f"{_ic('🌍')} {os.path.basename(params.get('src', ''))}", (tipo, j),
                           t("paused_translation", language=params.get("target", ""))))
    opcoes += [(t("resume_later"), "later"), (t("resume_discard"), "discard")]
    escolha = ui.menu(t("resume_q", n=len(lista)), opcoes)
    if escolha == "later":
        return
    if escolha == "discard":
        for tipo, j in lista:
            if tipo == "audio":
                trabalhos.remover(j["id"])
            else:
                store.set_job_status(j[0], "discarded")
        ui.ok(t("resume_discarded"))
        return
    tipo, j = escolha
    if tipo == "audio" and j["tipo"] == "audiobook":
        from bookstudio.audiobook.fluxo import criar_audiobook
        criar_audiobook(args, config, retomar={**j["dados"], "id": j["id"]})
    elif tipo == "audio":
        from bookstudio import sincronia
        sincronia.retomar(j)
    else:
        from bookstudio.ebook import fluxos
        store.set_job_status(j[0], "resumed")
        fluxos.retomar(j[1], settings, store)


def _executar_opcao(opcao, args, config, settings, store):
    from bookstudio.audiobook.fluxo import criar_audiobook
    from bookstudio.ebook import fluxos
    from bookstudio import sincronia
    if opcao == "audiobook":
        criar_audiobook(args, config)
    elif opcao == "traduzir_audiobook":
        ui.dica(t("menu_translate_audiobook_tip"))
        saida = fluxos.fluxo(fluxos.TRADUZIR, settings, store, formato="epub")
        if saida:
            ui.secao(t("menu_now_audiobook"), "converter")
            base = settings.target_lang.split("-")[0].lower()
            criar_audiobook(args, config, caminhos=[str(saida)], idioma=base, sem_traducao=True)
    elif opcao == "texto":
        sincronia.gerar_texto(config)
    elif opcao == "traduzir_texto":
        audio = sincronia.escolher_audio()
        saida = fluxos.fluxo(fluxos.TRADUZIR, settings, store, formato="epub", rotulo_destino=t("sy_audiobook_language"))
        if saida:
            modo = ui.menu(t("sy_how_q"), [(t("sy_mode_both"), "ambos", t("sy_mode_both_desc")),
                                           (t("sy_mode_text"), "texto", t("sy_mode_text_desc"))])
            sincronia.gerar_texto(config, modo=modo, audio=audio, ebook=saida)
    elif opcao == "ebook_audio":
        sincronia.gerar_ebook(config)
    elif opcao == "converter":
        fluxos.fluxo(fluxos.CONVERTER, settings, store)
    elif opcao == "traduzir":
        fluxos.fluxo(fluxos.TRADUZIR, settings, store)
    elif opcao == "metadados":
        fluxos.fluxo(fluxos.METADADOS, settings, store)
    elif opcao == "config":
        _configuracoes(args, config, settings, store)
    elif opcao == "idioma":
        escolher_idioma_interface(config)


def _configuracoes(args, config, settings, store):
    from bookstudio.ebook import fluxos
    while True:
        leve = config.get("leve")
        estado = t("lite_on") if leve is True else t("lite_off") if leve is False else t("lite_auto")
        escolha = ui.menu(t("menu_settings"), [(t("settings_translation"), "tr"), (t("settings_lite", state=estado), "leve"),
                                              (t("settings_open_config"), "pasta"), (t("back"), "back")])
        if escolha == "back":
            return
        if escolha == "tr":
            fluxos.menu_configuracoes(settings, store)
        elif escolha == "leve":
            valor = ui.menu(t("settings_lite_q"), [(t("lite_auto"), None, t("lite_auto_desc")), (t("lite_on"), True), (t("lite_off"), False)],
                            padrao=leve)
            if valor is None:
                config.pop("leve", None)
            else:
                config["leve"] = valor
            salvar_config(config)
        elif escolha == "pasta":
            from bookstudio.sistema import abrir
            os.makedirs(os.path.dirname(caminho_config()), exist_ok=True)
            abrir(os.path.dirname(caminho_config()))


def menu_principal(args, config):
    settings, store = _ebook()
    try:
        _retomar_pendentes(args, config, settings, store)
    except ui.Cancelado:
        pass
    while True:
        print()
        opcoes = [(f"{_ic(e)} {t(r)}", v, t(d) if d else "") for v, e, r, d in OPCOES]
        try:
            opcao = ui.menu(t("menu_q"), opcoes, padrao=config.get("ultima_opcao") or "audiobook")
        except ui.Cancelado:
            return 0
        if opcao == "sair":
            return 0
        config["ultima_opcao"] = opcao
        salvar_config(config)
        try:
            _executar_opcao(opcao, args, config, settings, store)
        except ui.Cancelado:
            ui.dica(t("back_to_menu"))


def main():
    config = migrar_config_1x(ler_config())
    i18n.configurar(_idioma_pedido(sys.argv[1:]), config.get("idioma"))
    args = _argumentos()
    limpar_sobras()
    from bookstudio import dependencias
    if dependencias.faltando() and not dependencias.garantir(perguntar=not args.yes):
        return 1
    if ui.console is None or ui.questionary is None:  # acabaram de ser instaladas: abre de novo para carregar
        os.execv(sys.executable, [sys.executable, "-m", "bookstudio", *sys.argv[1:]])
    verificacao = None if atualizacao.desligada(args.no_update_check) else atualizacao.Verificacao(VERSAO)
    if args.voice and not any(v["c"] == args.voice for v in VOZES):
        ui.falha(t("voice_unknown", voice=args.voice))
        return 1
    ui.banner(NOME, VERSAO, t("app_tagline"))
    if avisar_atualizacao(verificacao, args.yes):
        return 0
    if not args.yes and sys.stdin.isatty():
        if args.lang is True:  # --lang sem valor: abre a lista
            escolher_idioma_interface(config)
        elif not args.lang and not config.get("idioma"):
            ui.dica(t("language_hint_menu", language=IDIOMAS[i18n.atual]["nativo"]))
    # Com arquivos na linha de comando (ou --yes), cria o audiobook direto, como na versão 1.x
    if args.arquivos or args.yes or not sys.stdin.isatty():
        from bookstudio.audiobook.fluxo import criar_audiobook
        return criar_audiobook(args, config)
    return menu_principal(args, config)


def executar():
    """Ponto de entrada (BookStudio.bat, python -m bookstudio e o comando bookstudio instalado com pip/pipx)."""
    try:
        sys.exit(main())
    except (KeyboardInterrupt, ui.Cancelado):
        print()
        ui.dica(t("exiting"))
        sys.exit(130)
