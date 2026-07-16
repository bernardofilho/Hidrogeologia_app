from __future__ import annotations

import os

# Em hospedagem pública, cada sessão recebe um banco SQLite independente.
os.environ.setdefault("HIDRO_DB_MODE", "session")

from app import executar_aplicativo

executar_aplicativo()
