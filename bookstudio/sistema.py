"""sistema: parte do BookStudio (separado de audiobook_cli.py)."""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from bookstudio import atualizacao, i18n, ui
from bookstudio.i18n import t
from bookstudio.caminhos import BASE, MOTOR
from bookstudio import VERSAO



# ---------------------------------------------------------------------------------------------
# Seletores do sistema (arquivos e pasta)
# ---------------------------------------------------------------------------------------------
def _tk():
    try:
        import tkinter
        from tkinter import filedialog
        raiz = tkinter.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        return raiz, filedialog
    except Exception:
        return None, None


def abrir(caminho):
    try:
        if os.name == "nt":
            os.startfile(caminho)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", caminho])
        else:
            subprocess.Popen(["xdg-open", caminho], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        ui.alerta(t("cannot_open", error=e))


# ---------------------------------------------------------------------------------------------
# Verificações
# ---------------------------------------------------------------------------------------------
def verificar_dependencias():
    """Confere as bibliotecas sem importá-las (abrir pdfplumber e lxml só para conferir atrasava a abertura).
    Devolve se o FFmpeg está instalado: sem ele, só os formatos MP3."""
    faltando = []
    for modulo, pacote in [("edge_tts", "edge-tts"), ("bs4", "beautifulsoup4"), ("lxml", "lxml"), ("mutagen", "mutagen"),
                           ("pypdf", "pypdf"), ("pdfplumber", "pdfplumber"), ("PIL", "pillow"), ("mobi", "mobi"), ("langdetect", "langdetect")]:
        try:
            if importlib.util.find_spec(modulo) is None:
                faltando.append(pacote)
        except (ImportError, ValueError):
            faltando.append(pacote)
    if faltando:
        ui.falha(t("missing_libs", libs=", ".join(faltando)))
        python = "python" if os.name == "nt" else "python3"
        ui.dica(t("install_with", command=f"{python} -m pip install -r requirements.txt"))
        sys.exit(1)
    if shutil.which("ffmpeg"):
        return True
    ui.alerta(t("ffmpeg_missing_mp3"))
    if os.name == "nt":
        comando = "winget install Gyan.FFmpeg"
    elif sys.platform == "darwin":
        comando = "brew install ffmpeg"
    else:
        comando = "sudo apt install ffmpeg  " + t("ffmpeg_linux_hint")
    ui.dica(t("ffmpeg_install_for_more", command=comando))
    return False


# ---------------------------------------------------------------------------------------------
# Recursos do computador (para ligar o modo leve sozinho em computadores mais fracos)
# ---------------------------------------------------------------------------------------------
def memoria_gb():
    """Memória RAM total em GB (None se não der para saber)."""
    try:
        if os.name == "nt":
            import ctypes

            class Estado(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            estado = Estado()
            estado.dwLength = ctypes.sizeof(Estado)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
                return estado.ullTotalPhys / 1024 ** 3
            return None
        if sys.platform == "darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            return int(r.stdout.strip()) / 1024 ** 3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except Exception:
        return None


def na_bateria():
    """True se o notebook está fora da tomada, False se está na tomada, None se não der para saber (ou desktop)."""
    try:
        if os.name == "nt":
            import ctypes

            class Energia(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

            energia = Energia()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(energia)) or energia.BatteryFlag == 128:
                return None  # 128: sem bateria (desktop)
            return {0: True, 1: False}.get(energia.ACLineStatus)
        if sys.platform == "darwin":
            r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3)
            return "Battery Power" in r.stdout if "InternalBattery" in r.stdout else None
        base = "/sys/class/power_supply"
        fontes = os.listdir(base) if os.path.isdir(base) else []
        if not any(open(os.path.join(base, f, "type")).read().strip() == "Battery" for f in fontes
                   if os.path.exists(os.path.join(base, f, "type"))):
            return None
        tomadas = [os.path.join(base, f, "online") for f in fontes
                   if os.path.exists(os.path.join(base, f, "type"))
                   and open(os.path.join(base, f, "type")).read().strip() == "Mains"
                   and os.path.exists(os.path.join(base, f, "online"))]
        return all(open(p).read().strip() == "0" for p in tomadas) if tomadas else None
    except Exception:
        return None


def computador_mais_fraco(nucleos, ram, bateria):
    """Modo leve automático: pelo menos dois sinais (até 4 núcleos, menos de 8 GB, fora da tomada)
    ou um sinal forte (até 2 núcleos ou menos de 4 GB)."""
    if nucleos <= 2 or (ram is not None and ram < 4):
        return True
    return sum([nucleos <= 4, ram is not None and ram < 8, bateria is True]) >= 2


_ANCESTRAIS = None


def _processos_ancestrais():
    """Processos que abriram este (o shell, o Windows Terminal, o VS Code...), lidos uma vez."""
    global _ANCESTRAIS
    if _ANCESTRAIS is not None:
        return _ANCESTRAIS
    import ctypes
    from ctypes import wintypes

    class Entrada(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    foto = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    pais = {}
    try:
        e = Entrada()
        e.dwSize = ctypes.sizeof(Entrada)
        ok = k32.Process32FirstW(foto, ctypes.byref(e))
        while ok:
            pais[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32NextW(foto, ctypes.byref(e))
    finally:
        k32.CloseHandle(foto)
    vistos, pid = set(), os.getpid()
    while pid and pid not in vistos:
        vistos.add(pid)
        pid = pais.get(pid)
    _ANCESTRAIS = vistos
    return vistos


def janela_em_primeiro_plano():
    """Se a janela deste terminal está em primeiro plano (não minimizada nem atrás de outra). Só no Windows;
    None quando não dá para saber (macOS, Linux), e aí nada muda na conversão."""
    if os.name != "nt" or not sys.stdin.isatty():
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.GetForegroundWindow.restype = wintypes.HWND
        k32.GetConsoleWindow.restype = wintypes.HWND
        u32.GetAncestor.restype = wintypes.HWND
        u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        ativa, console = u32.GetForegroundWindow(), k32.GetConsoleWindow()
        if not ativa:
            return False
        # Console clássico: a própria janela; Windows Terminal: a janela dona do console
        if console and (ativa == console or ativa == u32.GetAncestor(console, 3)):  # GA_ROOTOWNER
            return True
        dono = wintypes.DWORD()
        u32.GetWindowThreadProcessId(ativa, ctypes.byref(dono))
        return dono.value in _processos_ancestrais()  # terminal do VS Code, Windows Terminal que abriu o shell...
    except Exception:
        return None
