"""
main.py — Entry point SuperBot v5
Punto de entrada para Railway, Docker y ejecución local.
"""
import sys
import os

# Asegurar que /app está en el path (necesario en Docker)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import SuperBot

if __name__ == "__main__":
    SuperBot().run()
