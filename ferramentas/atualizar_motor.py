"""Copia o motor de conversão e o catálogo de vozes do Audiobook Generator (o app com interface, na pasta
audiobook-generatior ao lado desta) para o BookStudio, que converte exatamente como o app. Rode depois de mudar
o motor do app:
    python atualizar_motor.py [caminho-do-app]
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # a raiz do projeto
PACOTE = os.path.join(BASE, "bookstudio")
APP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(BASE), "audiobook-generatior")
MODULOS = ["converter.py", "audio_output.py", "book_structure.py", "extract_metadata.py", "sync_text_tools.py",
           "ebook_audiobook_converter.py", "sync_audiobook.py", "capa_generica.py", "texto_falado.py", "emojis_fala.json"]

if not os.path.isfile(os.path.join(APP, "converter.py")):
    sys.exit(f"App não encontrado em {APP}")
for m in MODULOS:
    shutil.copy2(os.path.join(APP, m), os.path.join(PACOTE, "motor", m))
    print(f"✔ bookstudio/motor/{m}")
shutil.copy2(os.path.join(APP, "src", "data", "edgeVoices.json"), os.path.join(PACOTE, "dados", "vozes.json"))
print("✔ bookstudio/dados/vozes.json")
print("Motor atualizado. (Se o catálogo de vozes ganhou idiomas novos, gere de novo bookstudio/dados/idiomas.json.)")
