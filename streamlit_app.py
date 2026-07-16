from __future__ import annotations

import os

# Em hospedagem publica, cada sessao recebe um banco SQLite independente.
os.environ.setdefault("HIDRO_DB_MODE", "session")

from app import executar_aplicativo

executar_aplicativo()
