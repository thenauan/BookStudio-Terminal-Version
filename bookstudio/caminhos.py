"""Pastas do app: o pacote, os dados (idiomas, vozes, traduções da interface) e o motor de conversão."""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(BASE, "dados")
MOTOR = os.path.join(BASE, "motor")
# O motor (converter.py e companhia) roda como script e também é importado pelo texto sincronizado
if MOTOR not in sys.path:
    sys.path.insert(0, MOTOR)
