from __future__ import annotations

import math
import os
import sqlite3
import tempfile
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError

CAMINHO_BANCO_PADRAO = Path(__file__).with_name("hidrogeologia.db")
TOLERANCIA_PROFUNDIDADE = 1e-6
EPSG_SIRGAS_2000_GEOGRAFICO = 4674
EPSG_SIRGAS_2000_UTM_23S = 31983

STATUS_PLANEJADA = "Planejada"
STATUS_EXECUCAO = "Em execucao"
STATUS_CONCLUIDA = "Concluida"
STATUS_SONDAGEM_VALIDOS = [
    STATUS_PLANEJADA,
    STATUS_EXECUCAO,
    STATUS_CONCLUIDA,
]

CLASSIFICACOES_VALIDAS = [
    "Argila",
    "Areia Fina",
    "Areia Grossa",
    "Cascalho",
    "Rocha S\u00e3",
    "Rocha Alterada",
    "Silte",
]

TIPOS_AQUIFERO_VALIDOS = [
    "Livre",
    "Confinado",
    "Semiconfinado",
    "Aquitarde",
    "Aqu\u00edfugo",
]

ALIASES_CLASSIFICACOES = {
    "Rocha Sa": "Rocha S\u00e3",
}

ALIASES_TIPOS_AQUIFERO = {
    "Aquifugo": "Aqu\u00edfugo",
}

ZONAS_HIDRICAS_VALIDAS = [
    "Zona vadosa",
    "Zona saturada",
    "Transi\u00e7\u00e3o vadosa/saturada",
    "Indeterminada - NA n\u00e3o informado",
]

COLUNAS_CSV_BASE = [
    "projeto",
    "sondagem_nome",
    "altitude",
    "nivel_agua",
    "profundidade_inicial",
    "profundidade_final",
    "descricao",
    "classificacao",
    "tipo_aquifero",
]

COLUNAS_CSV_COORDENADAS_GENERICAS = [
    "crs_epsg",
    "coordenada_x",
    "coordenada_y",
]

COLUNAS_CSV_COORDENADAS_GEOGRAFICAS = [
    "latitude",
    "longitude",
]

# Mantido para compatibilidade com trechos externos que exibem as colunas basicas.
COLUNAS_CSV_OBRIGATORIAS = COLUNAS_CSV_BASE


class ErroValidacao(ValueError):
    """Erro de regra de negocio ou de consistencia dos dados."""


@lru_cache(maxsize=64)
def obter_crs(epsg: int) -> CRS:
    """Carrega e valida um sistema de referencia por codigo EPSG."""
    try:
        codigo = int(epsg)
        return CRS.from_epsg(codigo)
    except (TypeError, ValueError, CRSError) as erro:
        raise ErroValidacao(f"O codigo EPSG '{epsg}' nao e valido.") from erro


def obter_metadados_crs(epsg: int) -> dict[str, Any]:
    """Retorna metadados uteis para montar os campos de coordenadas."""
    crs = obter_crs(epsg)
    unidade = "graus" if crs.is_geographic else "metros"
    if crs.is_geographic:
        rotulo_x = "Longitude (graus)"
        rotulo_y = "Latitude (graus)"
    else:
        rotulo_x = "Coordenada X / Leste (m)"
        rotulo_y = "Coordenada Y / Norte (m)"
    return {
        "epsg": int(epsg),
        "nome": crs.name,
        "geografico": bool(crs.is_geographic),
        "projetado": bool(crs.is_projected),
        "unidade": unidade,
        "rotulo_x": rotulo_x,
        "rotulo_y": rotulo_y,
    }


def transformar_coordenadas(
    coordenada_x: float,
    coordenada_y: float,
    epsg_origem: int,
    epsg_destino: int,
) -> tuple[float, float]:
    """Transforma coordenadas usando sempre a ordem X/Y."""
    try:
        x = float(coordenada_x)
        y = float(coordenada_y)
    except (TypeError, ValueError) as erro:
        raise ErroValidacao("As coordenadas informadas nao sao numericas.") from erro

    if not math.isfinite(x) or not math.isfinite(y):
        raise ErroValidacao("As coordenadas devem conter valores finitos.")

    try:
        transformador = Transformer.from_crs(
            obter_crs(int(epsg_origem)),
            obter_crs(int(epsg_destino)),
            always_xy=True,
        )
        x_destino, y_destino = transformador.transform(x, y, errcheck=True)
    except (CRSError, ProjError, ValueError) as erro:
        raise ErroValidacao(
            "Nao foi possivel transformar as coordenadas entre os sistemas informados."
        ) from erro

    if not math.isfinite(x_destino) or not math.isfinite(y_destino):
        raise ErroValidacao("A transformacao produziu coordenadas invalidas.")
    return float(x_destino), float(y_destino)


def converter_para_sirgas2000(
    coordenada_x: float,
    coordenada_y: float,
    epsg_origem: int,
) -> tuple[float, float]:
    """Converte a entrada para latitude/longitude SIRGAS 2000 (EPSG:4674)."""
    longitude, latitude = transformar_coordenadas(
        coordenada_x,
        coordenada_y,
        int(epsg_origem),
        EPSG_SIRGAS_2000_GEOGRAFICO,
    )
    if not -90 <= latitude <= 90:
        raise ErroValidacao("A latitude transformada ficou fora do intervalo valido.")
    if not -180 <= longitude <= 180:
        raise ErroValidacao("A longitude transformada ficou fora do intervalo valido.")
    return latitude, longitude


def converter_de_sirgas2000(
    latitude: float,
    longitude: float,
    epsg_destino: int,
) -> tuple[float, float]:
    """Converte latitude/longitude SIRGAS 2000 para outro EPSG."""
    return transformar_coordenadas(
        longitude,
        latitude,
        EPSG_SIRGAS_2000_GEOGRAFICO,
        int(epsg_destino),
    )


def calcular_distribuicao_zonas_hidricas(
    profundidade_inicial: float,
    profundidade_final: float,
    nivel_agua_estatico: float | None,
) -> dict[str, float | str | None]:
    """Classifica um intervalo em relacao ao NA e calcula suas parcelas."""
    inicio = float(profundidade_inicial)
    final = float(profundidade_final)

    if nivel_agua_estatico is None or pd.isna(nivel_agua_estatico):
        return {
            "zona_hidrica": "Indeterminada - NA n\u00e3o informado",
            "espessura_vadosa": None,
            "espessura_saturada": None,
        }

    nivel_agua = float(nivel_agua_estatico)
    espessura_vadosa = max(0.0, min(final, nivel_agua) - inicio)
    espessura_saturada = max(0.0, final - max(inicio, nivel_agua))

    if final <= nivel_agua + TOLERANCIA_PROFUNDIDADE:
        zona_hidrica = "Zona vadosa"
    elif inicio >= nivel_agua - TOLERANCIA_PROFUNDIDADE:
        zona_hidrica = "Zona saturada"
    else:
        zona_hidrica = "Transi\u00e7\u00e3o vadosa/saturada"

    return {
        "zona_hidrica": zona_hidrica,
        "espessura_vadosa": round(espessura_vadosa, 6),
        "espessura_saturada": round(espessura_saturada, 6),
    }


def conectar(caminho_banco: str | Path = CAMINHO_BANCO_PADRAO) -> sqlite3.Connection:
    """Abre uma conexao SQLite com integridade referencial habilitada."""
    caminho = Path(caminho_banco)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(str(caminho), timeout=30)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA journal_mode = WAL")
    return conexao


def _colunas_tabela(conexao: sqlite3.Connection, tabela: str) -> set[str]:
    """Lista as colunas existentes em uma tabela."""
    return {
        str(linha["name"])
        for linha in conexao.execute(f"PRAGMA table_info({tabela})").fetchall()
    }


def _adicionar_coluna_se_ausente(
    conexao: sqlite3.Connection,
    tabela: str,
    coluna: str,
    definicao: str,
) -> None:
    """Aplica uma migracao simples de coluna sem apagar dados existentes."""
    if coluna not in _colunas_tabela(conexao, tabela):
        conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def _migrar_estrutura(conexao: sqlite3.Connection) -> None:
    """Atualiza bancos de versoes anteriores para o fluxo de campo atual."""
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "crs_entrada", "INTEGER DEFAULT 4674"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "coordenada_x", "REAL"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "coordenada_y", "REAL"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "profundidade_planejada", "REAL"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "profundidade_atual", "REAL DEFAULT 0"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "status", "TEXT DEFAULT 'Planejada'"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "data_inicio", "TEXT"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "data_conclusao", "TEXT"
    )

    conexao.execute(
        """
        UPDATE sondagens
        SET crs_entrada = COALESCE(crs_entrada, 4674),
            coordenada_x = COALESCE(coordenada_x, longitude),
            coordenada_y = COALESCE(coordenada_y, latitude),
            profundidade_planejada = COALESCE(profundidade_planejada, profundidade_total),
            profundidade_atual = COALESCE(profundidade_atual, 0),
            status = COALESCE(NULLIF(status, ''), 'Planejada')
        """
    )

    conexao.execute(
        """
        UPDATE sondagens
        SET profundidade_atual = COALESCE(
            (
                SELECT MAX(c.profundidade_final)
                FROM camadas_litologicas c
                WHERE c.sondagem_id = sondagens.id
            ),
            profundidade_atual,
            0
        )
        WHERE profundidade_atual IS NULL OR profundidade_atual <= 0
        """
    )

    conexao.execute(
        """
        UPDATE sondagens
        SET status = 'Concluida',
            data_conclusao = COALESCE(data_conclusao, data)
        WHERE EXISTS (
            SELECT 1
            FROM camadas_litologicas c
            WHERE c.sondagem_id = sondagens.id
        )
        AND ABS(
            COALESCE(
                (
                    SELECT MAX(c.profundidade_final)
                    FROM camadas_litologicas c
                    WHERE c.sondagem_id = sondagens.id
                ),
                0
            ) - profundidade_total
        ) <= 0.000001
        """
    )

    conexao.execute(
        """
        UPDATE sondagens
        SET status = 'Em execucao'
        WHERE status = 'Planejada' AND profundidade_atual > 0
        """
    )


def inicializar_banco(caminho_banco: str | Path = CAMINHO_BANCO_PADRAO) -> None:
    """Cria as tabelas, indices e migracoes necessarias."""
    with conectar(caminho_banco) as conexao:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS projetos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                descricao TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sondagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                projeto_id INTEGER NOT NULL,
                nome_furo TEXT NOT NULL,
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                crs_entrada INTEGER NOT NULL DEFAULT 31983,
                coordenada_x REAL NOT NULL,
                coordenada_y REAL NOT NULL,
                altitude REAL NOT NULL,
                profundidade_total REAL NOT NULL CHECK (profundidade_total > 0),
                profundidade_planejada REAL NOT NULL CHECK (profundidade_planejada > 0),
                profundidade_atual REAL NOT NULL DEFAULT 0 CHECK (profundidade_atual >= 0),
                nivel_agua_estatico REAL CHECK (
                    nivel_agua_estatico IS NULL OR nivel_agua_estatico >= 0
                ),
                status TEXT NOT NULL DEFAULT 'Planejada',
                data TEXT NOT NULL,
                data_inicio TEXT,
                data_conclusao TEXT,
                FOREIGN KEY (projeto_id) REFERENCES projetos(id) ON DELETE CASCADE,
                UNIQUE (projeto_id, nome_furo)
            );

            CREATE TABLE IF NOT EXISTS camadas_litologicas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                profundidade_inicial REAL NOT NULL CHECK (profundidade_inicial >= 0),
                profundidade_final REAL NOT NULL CHECK (
                    profundidade_final > profundidade_inicial
                ),
                descricao_tatil_visual TEXT NOT NULL,
                classificacao TEXT NOT NULL,
                tipo_aquifero TEXT NOT NULL,
                cota_topo REAL NOT NULL,
                cota_base REAL NOT NULL,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS rascunhos_camadas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                profundidade_inicial REAL NOT NULL CHECK (profundidade_inicial >= 0),
                profundidade_final REAL NOT NULL CHECK (
                    profundidade_final > profundidade_inicial
                ),
                descricao_tatil_visual TEXT NOT NULL,
                classificacao TEXT NOT NULL,
                tipo_aquifero TEXT NOT NULL,
                cota_topo REAL NOT NULL,
                cota_base REAL NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS coletas_amostras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                profundidade_coleta REAL NOT NULL CHECK (profundidade_coleta >= 0),
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS voc_medicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                profundidade REAL NOT NULL CHECK (profundidade >= 0),
                concentracao REAL NOT NULL CHECK (concentracao >= 0),
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sondagens_projeto
                ON sondagens(projeto_id);
            CREATE INDEX IF NOT EXISTS idx_camadas_sondagem
                ON camadas_litologicas(sondagem_id, profundidade_inicial);
            CREATE INDEX IF NOT EXISTS idx_rascunhos_sondagem
                ON rascunhos_camadas(sondagem_id, profundidade_inicial);
            CREATE INDEX IF NOT EXISTS idx_coletas_sondagem
                ON coletas_amostras(sondagem_id, profundidade_coleta);
            CREATE INDEX IF NOT EXISTS idx_voc_sondagem
                ON voc_medicoes(sondagem_id, profundidade);
            """
        )
        _migrar_estrutura(conexao)


def _dataframe_de_linhas(
    linhas: Iterable[sqlite3.Row],
    colunas: list[str],
) -> pd.DataFrame:
    """Converte linhas SQLite em DataFrame com ordem de colunas estavel."""
    registros = [dict(linha) for linha in linhas]
    return pd.DataFrame(registros, columns=colunas)


def criar_projeto(
    nome: str,
    descricao: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Insere um projeto e devolve o identificador criado."""
    nome_limpo = str(nome).strip()
    if not nome_limpo:
        raise ErroValidacao("O nome do projeto e obrigatorio.")

    try:
        with conectar(caminho_banco) as conexao:
            cursor = conexao.execute(
                "INSERT INTO projetos (nome, descricao) VALUES (?, ?)",
                (nome_limpo, str(descricao or "").strip()),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as erro:
        raise ErroValidacao(f"Nao foi possivel criar o projeto: {erro}") from erro


def listar_projetos(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista os projetos cadastrados."""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            "SELECT id, nome, descricao FROM projetos ORDER BY nome"
        ).fetchall()
    return _dataframe_de_linhas(linhas, ["id", "nome", "descricao"])


def obter_projeto(
    projeto_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem um projeto pelo identificador."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT id, nome, descricao FROM projetos WHERE id = ?",
            (int(projeto_id),),
        ).fetchone()
    return dict(linha) if linha else None


def _validar_numero_finito(valor: Any, nome: str) -> float:
    """Converte e valida um numero finito."""
    try:
        numero = float(valor)
    except (TypeError, ValueError) as erro:
        raise ErroValidacao(f"O campo '{nome}' deve ser numerico.") from erro
    if not math.isfinite(numero):
        raise ErroValidacao(f"O campo '{nome}' deve ser finito.")
    return numero


def _validar_dados_sondagem(
    nome_furo: str,
    coordenada_x: float,
    coordenada_y: float,
    crs_entrada: int,
    altitude: float,
    profundidade_planejada: float,
    nivel_agua_estatico: float | None,
) -> dict[str, Any]:
    """Valida o cabecalho de uma sondagem e normaliza suas coordenadas."""
    nome_limpo = str(nome_furo).strip()
    if not nome_limpo:
        raise ErroValidacao("O nome da sondagem e obrigatorio.")

    epsg = int(crs_entrada)
    obter_crs(epsg)
    x = _validar_numero_finito(coordenada_x, "coordenada X")
    y = _validar_numero_finito(coordenada_y, "coordenada Y")
    altitude_num = _validar_numero_finito(altitude, "altitude")
    profundidade_num = _validar_numero_finito(
        profundidade_planejada,
        "profundidade planejada",
    )
    if profundidade_num <= 0:
        raise ErroValidacao("A profundidade planejada deve ser maior que zero.")

    nivel_agua_num = None
    if nivel_agua_estatico is not None and not pd.isna(nivel_agua_estatico):
        nivel_agua_num = _validar_numero_finito(
            nivel_agua_estatico,
            "nivel d'agua",
        )
        if nivel_agua_num < 0:
            raise ErroValidacao("O nivel d'agua nao pode ser negativo.")
        if nivel_agua_num > profundidade_num + TOLERANCIA_PROFUNDIDADE:
            raise ErroValidacao(
                "O nivel d'agua nao pode ultrapassar a profundidade informada."
            )

    latitude, longitude = converter_para_sirgas2000(x, y, epsg)
    return {
        "nome_furo": nome_limpo,
        "coordenada_x": x,
        "coordenada_y": y,
        "crs_entrada": epsg,
        "latitude": latitude,
        "longitude": longitude,
        "altitude": altitude_num,
        "profundidade_planejada": profundidade_num,
        "nivel_agua_estatico": nivel_agua_num,
    }


def criar_sondagem(
    projeto_id: int,
    nome_furo: str,
    altitude: float,
    profundidade_planejada: float | None = None,
    data_sondagem: str | date = date.today(),
    crs_entrada: int = EPSG_SIRGAS_2000_UTM_23S,
    coordenada_x: float | None = None,
    coordenada_y: float | None = None,
    nivel_agua_estatico: float | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    profundidade_total: float | None = None,
) -> int:
    """Cria uma sondagem planejada com CRS de entrada configuravel."""
    if profundidade_planejada is None:
        profundidade_planejada = profundidade_total
    if profundidade_planejada is None:
        raise ErroValidacao("Informe a profundidade planejada.")

    # Aceita a assinatura antiga em latitude/longitude para manter compatibilidade.
    if coordenada_x is None or coordenada_y is None:
        if latitude is None or longitude is None:
            raise ErroValidacao("Informe as duas coordenadas da sondagem.")
        crs_entrada = EPSG_SIRGAS_2000_GEOGRAFICO
        coordenada_x = float(longitude)
        coordenada_y = float(latitude)

    dados = _validar_dados_sondagem(
        nome_furo,
        coordenada_x,
        coordenada_y,
        crs_entrada,
        altitude,
        profundidade_planejada,
        nivel_agua_estatico,
    )

    data_texto = str(data_sondagem).strip()
    if not data_texto:
        raise ErroValidacao("A data de planejamento e obrigatoria.")

    try:
        with conectar(caminho_banco) as conexao:
            cursor = conexao.execute(
                """
                INSERT INTO sondagens (
                    projeto_id, nome_furo, latitude, longitude,
                    crs_entrada, coordenada_x, coordenada_y, altitude,
                    profundidade_total, profundidade_planejada,
                    profundidade_atual, nivel_agua_estatico, status, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    int(projeto_id),
                    dados["nome_furo"],
                    dados["latitude"],
                    dados["longitude"],
                    dados["crs_entrada"],
                    dados["coordenada_x"],
                    dados["coordenada_y"],
                    dados["altitude"],
                    dados["profundidade_planejada"],
                    dados["profundidade_planejada"],
                    dados["nivel_agua_estatico"],
                    STATUS_PLANEJADA,
                    data_texto,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as erro:
        raise ErroValidacao(f"Nao foi possivel criar a sondagem: {erro}") from erro


def atualizar_coordenadas_sondagem(
    sondagem_id: int,
    coordenada_x: float,
    coordenada_y: float,
    crs_entrada: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Atualiza o CRS original e as coordenadas canonicas de uma sondagem."""
    x = _validar_numero_finito(coordenada_x, "coordenada X")
    y = _validar_numero_finito(coordenada_y, "coordenada Y")
    epsg = int(crs_entrada)
    latitude, longitude = converter_para_sirgas2000(x, y, epsg)
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            UPDATE sondagens
            SET crs_entrada = ?, coordenada_x = ?, coordenada_y = ?,
                latitude = ?, longitude = ?
            WHERE id = ?
            """,
            (epsg, x, y, latitude, longitude, int(sondagem_id)),
        )
        if cursor.rowcount == 0:
            raise ErroValidacao("Sondagem nao encontrada.")


def _consulta_sondagens() -> str:
    """Monta a consulta comum de sondagens com campos calculados."""
    return """
        SELECT
            s.id,
            s.projeto_id,
            p.nome AS projeto_nome,
            s.nome_furo,
            s.latitude,
            s.longitude,
            s.crs_entrada,
            s.coordenada_x,
            s.coordenada_y,
            s.altitude,
            s.profundidade_total,
            s.profundidade_planejada,
            s.profundidade_atual,
            s.nivel_agua_estatico,
            s.status,
            s.data,
            s.data_inicio,
            s.data_conclusao,
            CASE
                WHEN s.nivel_agua_estatico IS NULL THEN NULL
                ELSE s.altitude - s.nivel_agua_estatico
            END AS cota_nivel_agua,
            CASE
                WHEN s.nivel_agua_estatico IS NULL THEN NULL
                ELSE MIN(s.nivel_agua_estatico, s.profundidade_total)
            END AS espessura_zona_vadosa,
            CASE
                WHEN s.nivel_agua_estatico IS NULL THEN NULL
                ELSE MAX(s.profundidade_total - s.nivel_agua_estatico, 0)
            END AS espessura_trecho_saturado,
            CASE
                WHEN s.profundidade_planejada <= 0 THEN 0
                ELSE MIN(MAX(s.profundidade_atual / s.profundidade_planejada, 0), 1)
            END AS fracao_execucao
        FROM sondagens s
        INNER JOIN projetos p ON p.id = s.projeto_id
    """


def listar_sondagens(
    projeto_id: int | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
    status: str | None = None,
) -> pd.DataFrame:
    """Lista sondagens e inclui informacoes de execucao e coordenadas."""
    consulta = _consulta_sondagens()
    condicoes: list[str] = []
    parametros: list[Any] = []
    if projeto_id is not None:
        condicoes.append("s.projeto_id = ?")
        parametros.append(int(projeto_id))
    if status is not None:
        condicoes.append("s.status = ?")
        parametros.append(str(status))
    if condicoes:
        consulta += " WHERE " + " AND ".join(condicoes)
    consulta += " ORDER BY p.nome, s.nome_furo"

    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(consulta, tuple(parametros)).fetchall()
    colunas = [
        "id",
        "projeto_id",
        "projeto_nome",
        "nome_furo",
        "latitude",
        "longitude",
        "crs_entrada",
        "coordenada_x",
        "coordenada_y",
        "altitude",
        "profundidade_total",
        "profundidade_planejada",
        "profundidade_atual",
        "nivel_agua_estatico",
        "status",
        "data",
        "data_inicio",
        "data_conclusao",
        "cota_nivel_agua",
        "espessura_zona_vadosa",
        "espessura_trecho_saturado",
        "fracao_execucao",
    ]
    return _dataframe_de_linhas(linhas, colunas)


def obter_sondagem(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem todos os dados de uma sondagem pelo identificador."""
    consulta = _consulta_sondagens() + " WHERE s.id = ?"
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(consulta, (int(sondagem_id),)).fetchone()
    return dict(linha) if linha else None


def iniciar_sondagem(
    sondagem_id: int,
    data_inicio: str | date | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Muda uma sondagem planejada para o estado de execucao."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    if sondagem["status"] == STATUS_CONCLUIDA:
        raise ErroValidacao("Reabra a sondagem antes de alterar seu perfil.")
    data_texto = str(data_inicio or date.today())
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            """
            UPDATE sondagens
            SET status = ?, data_inicio = COALESCE(data_inicio, ?)
            WHERE id = ?
            """,
            (STATUS_EXECUCAO, data_texto, int(sondagem_id)),
        )


def atualizar_profundidade_planejada(
    sondagem_id: int,
    profundidade_planejada: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Atualiza a meta de profundidade sem reduzir abaixo do trecho executado."""
    nova = _validar_numero_finito(
        profundidade_planejada,
        "profundidade planejada",
    )
    if nova <= 0:
        raise ErroValidacao("A profundidade planejada deve ser maior que zero.")
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    if sondagem["status"] == STATUS_CONCLUIDA:
        raise ErroValidacao("Reabra a sondagem antes de alterar a profundidade.")
    atual = float(sondagem["profundidade_atual"] or 0)
    if nova < atual - TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"A nova meta nao pode ser menor que a profundidade executada ({atual:.3f} m)."
        )
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            """
            UPDATE sondagens
            SET profundidade_planejada = ?, profundidade_total = ?
            WHERE id = ?
            """,
            (nova, nova, int(sondagem_id)),
        )


def atualizar_nivel_agua(
    sondagem_id: int,
    nivel_agua_estatico: float | None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Registra ou remove o nivel d'agua observado na sondagem."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")

    if nivel_agua_estatico is None or pd.isna(nivel_agua_estatico):
        nivel = None
    else:
        nivel = _validar_numero_finito(nivel_agua_estatico, "nivel d'agua")
        if nivel < 0:
            raise ErroValidacao("O nivel d'agua nao pode ser negativo.")
        limite = max(
            float(sondagem["profundidade_atual"] or 0),
            float(sondagem["profundidade_total"] or 0)
            if sondagem["status"] == STATUS_CONCLUIDA
            else 0,
        )
        if limite <= 0:
            raise ErroValidacao(
                "Inicie a sondagem e registre ao menos um intervalo antes do NA."
            )
        if nivel > limite + TOLERANCIA_PROFUNDIDADE:
            raise ErroValidacao(
                f"O NA nao pode ultrapassar a profundidade executada ({limite:.3f} m)."
            )

    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "UPDATE sondagens SET nivel_agua_estatico = ? WHERE id = ?",
            (nivel, int(sondagem_id)),
        )


def _normalizar_e_validar_camadas(
    camadas: list[dict[str, Any]],
    profundidade_limite: float,
    altitude: float,
    exigir_cobertura_total: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normaliza camadas e valida continuidade, dominio e profundidade."""
    erros: list[str] = []
    normalizadas: list[dict[str, Any]] = []

    if not camadas:
        return [], ["O perfil deve possuir pelo menos uma camada."]

    for indice, camada in enumerate(camadas, start=1):
        try:
            inicio = float(camada["profundidade_inicial"])
            final = float(camada["profundidade_final"])
        except (KeyError, TypeError, ValueError) as erro:
            erros.append(f"Camada {indice}: profundidades invalidas ({erro}).")
            continue

        descricao = str(
            camada.get("descricao_tatil_visual", camada.get("descricao", "")) or ""
        ).strip()
        classificacao = str(camada.get("classificacao", "") or "").strip()
        tipo_aquifero = str(camada.get("tipo_aquifero", "") or "").strip()
        classificacao = ALIASES_CLASSIFICACOES.get(classificacao, classificacao)
        tipo_aquifero = ALIASES_TIPOS_AQUIFERO.get(tipo_aquifero, tipo_aquifero)

        if not math.isfinite(inicio) or not math.isfinite(final):
            erros.append(f"Camada {indice}: as profundidades devem ser finitas.")
            continue
        if inicio < 0:
            erros.append(f"Camada {indice}: profundidade inicial negativa.")
        if final <= inicio:
            erros.append(
                f"Camada {indice}: a profundidade final deve ser maior que a inicial."
            )
        if final > profundidade_limite + TOLERANCIA_PROFUNDIDADE:
            erros.append(
                f"Camada {indice}: a profundidade final excede o limite de "
                f"{profundidade_limite:.3f} m."
            )
        if not descricao:
            erros.append(f"Camada {indice}: a descricao tatil-visual e obrigatoria.")
        if classificacao not in CLASSIFICACOES_VALIDAS:
            erros.append(
                f"Camada {indice}: classificacao '{classificacao}' nao permitida."
            )
        if tipo_aquifero not in TIPOS_AQUIFERO_VALIDOS:
            erros.append(
                f"Camada {indice}: unidade hidroestratigrafica "
                f"'{tipo_aquifero}' nao permitida."
            )

        normalizadas.append(
            {
                "profundidade_inicial": round(inicio, 6),
                "profundidade_final": round(final, 6),
                "descricao_tatil_visual": descricao,
                "classificacao": classificacao,
                "tipo_aquifero": tipo_aquifero,
                "cota_topo": round(float(altitude) - inicio, 6),
                "cota_base": round(float(altitude) - final, 6),
            }
        )

    normalizadas.sort(key=lambda item: item["profundidade_inicial"])
    if erros or not normalizadas:
        return normalizadas, erros

    if abs(normalizadas[0]["profundidade_inicial"]) > TOLERANCIA_PROFUNDIDADE:
        erros.append("O perfil deve comecar exatamente na profundidade 0,00 m.")

    for indice in range(1, len(normalizadas)):
        anterior = normalizadas[indice - 1]
        atual = normalizadas[indice]
        diferenca = atual["profundidade_inicial"] - anterior["profundidade_final"]
        if diferenca < -TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "Ha sobreposicao entre as camadas "
                f"{indice} e {indice + 1}: {abs(diferenca):.6f} m."
            )
        elif diferenca > TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "Ha intervalo sem descricao entre as camadas "
                f"{indice} e {indice + 1}: {diferenca:.6f} m."
            )

    if exigir_cobertura_total:
        soma = sum(
            camada["profundidade_final"] - camada["profundidade_inicial"]
            for camada in normalizadas
        )
        ultima = normalizadas[-1]["profundidade_final"]
        if abs(soma - profundidade_limite) > TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "A soma das espessuras deve ser igual a profundidade final: "
                f"soma={soma:.6f} m; final={profundidade_limite:.6f} m."
            )
        if abs(ultima - profundidade_limite) > TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "A ultima camada deve terminar exatamente na profundidade final: "
                f"camada={ultima:.6f} m; final={profundidade_limite:.6f} m."
            )

    return normalizadas, erros


def _adicionar_zonas_dataframe(
    dataframe: pd.DataFrame,
    nivel_agua_estatico: float | None,
) -> pd.DataFrame:
    """Acrescenta a condicao hidrica derivada a um DataFrame de camadas."""
    if dataframe.empty:
        return dataframe
    dados = dataframe.copy()
    distribuicoes = [
        calcular_distribuicao_zonas_hidricas(
            linha["profundidade_inicial"],
            linha["profundidade_final"],
            nivel_agua_estatico,
        )
        for _, linha in dados.iterrows()
    ]
    dados["zona_hidrica"] = [item["zona_hidrica"] for item in distribuicoes]
    dados["espessura_vadosa"] = [
        item["espessura_vadosa"] for item in distribuicoes
    ]
    dados["espessura_saturada"] = [
        item["espessura_saturada"] for item in distribuicoes
    ]
    return dados


def _listar_camadas_tabela(
    tabela: str,
    sondagem_id: int,
    caminho_banco: str | Path,
) -> pd.DataFrame:
    """Le camadas de uma tabela final ou de campo."""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            f"""
            SELECT
                id, sondagem_id, profundidade_inicial, profundidade_final,
                descricao_tatil_visual, classificacao, tipo_aquifero,
                cota_topo, cota_base,
                profundidade_final - profundidade_inicial AS espessura
            FROM {tabela}
            WHERE sondagem_id = ?
            ORDER BY profundidade_inicial
            """,
            (int(sondagem_id),),
        ).fetchall()
    colunas = [
        "id",
        "sondagem_id",
        "profundidade_inicial",
        "profundidade_final",
        "descricao_tatil_visual",
        "classificacao",
        "tipo_aquifero",
        "cota_topo",
        "cota_base",
        "espessura",
    ]
    dados = _dataframe_de_linhas(linhas, colunas)
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    nivel = sondagem["nivel_agua_estatico"] if sondagem else None
    return _adicionar_zonas_dataframe(dados, nivel)


def listar_camadas(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista o perfil final de uma sondagem."""
    return _listar_camadas_tabela(
        "camadas_litologicas",
        sondagem_id,
        caminho_banco,
    )


def listar_rascunho_camadas(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista os intervalos persistidos durante a execucao de campo."""
    return _listar_camadas_tabela(
        "rascunhos_camadas",
        sondagem_id,
        caminho_banco,
    )


def validar_rascunho_parcial(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Valida o diario de campo sem exigir que a meta tenha sido alcancada."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        return False, ["Sondagem nao encontrada."], []
    camadas = listar_rascunho_camadas(sondagem_id, caminho_banco).to_dict("records")
    normalizadas, erros = _normalizar_e_validar_camadas(
        camadas,
        float(sondagem["profundidade_planejada"]),
        float(sondagem["altitude"]),
        exigir_cobertura_total=False,
    )
    return not erros, erros, normalizadas


def adicionar_intervalo_campo(
    sondagem_id: int,
    profundidade_final: float,
    descricao_tatil_visual: str,
    classificacao: str,
    tipo_aquifero: str,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
    profundidade_inicial: float | None = None,
) -> int:
    """Persiste o proximo intervalo sequencial do diario de sondagem."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    if sondagem["status"] == STATUS_CONCLUIDA:
        raise ErroValidacao("Reabra a sondagem antes de adicionar intervalos.")

    existentes_df = listar_rascunho_camadas(sondagem_id, caminho_banco)
    esperado = (
        0.0
        if existentes_df.empty
        else float(existentes_df["profundidade_final"].max())
    )
    inicio = esperado if profundidade_inicial is None else float(profundidade_inicial)
    if abs(inicio - esperado) > TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"O proximo intervalo deve comecar em {esperado:.3f} m."
        )

    nova = {
        "profundidade_inicial": inicio,
        "profundidade_final": profundidade_final,
        "descricao_tatil_visual": descricao_tatil_visual,
        "classificacao": classificacao,
        "tipo_aquifero": tipo_aquifero,
    }
    todas = existentes_df.to_dict("records") + [nova]
    normalizadas, erros = _normalizar_e_validar_camadas(
        todas,
        float(sondagem["profundidade_planejada"]),
        float(sondagem["altitude"]),
        exigir_cobertura_total=False,
    )
    if erros:
        raise ErroValidacao("\n".join(erros))
    camada = normalizadas[-1]

    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO rascunhos_camadas (
                sondagem_id, profundidade_inicial, profundidade_final,
                descricao_tatil_visual, classificacao, tipo_aquifero,
                cota_topo, cota_base
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(sondagem_id),
                camada["profundidade_inicial"],
                camada["profundidade_final"],
                camada["descricao_tatil_visual"],
                camada["classificacao"],
                camada["tipo_aquifero"],
                camada["cota_topo"],
                camada["cota_base"],
            ),
        )
        conexao.execute(
            """
            UPDATE sondagens
            SET profundidade_atual = ?, status = ?,
                data_inicio = COALESCE(data_inicio, ?)
            WHERE id = ?
            """,
            (
                camada["profundidade_final"],
                STATUS_EXECUCAO,
                date.today().isoformat(),
                int(sondagem_id),
            ),
        )
        return int(cursor.lastrowid)


def _profundidade_maxima_pontos(
    conexao: sqlite3.Connection,
    sondagem_id: int,
) -> float | None:
    """Obtem a maior profundidade entre amostras e medicoes de VOC."""
    linha = conexao.execute(
        """
        SELECT MAX(profundidade) AS profundidade_maxima
        FROM (
            SELECT profundidade_coleta AS profundidade
            FROM coletas_amostras WHERE sondagem_id = ?
            UNION ALL
            SELECT profundidade
            FROM voc_medicoes WHERE sondagem_id = ?
        )
        """,
        (int(sondagem_id), int(sondagem_id)),
    ).fetchone()
    valor = linha["profundidade_maxima"] if linha else None
    return None if valor is None else float(valor)


def remover_ultimo_intervalo_campo(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove o ultimo intervalo se nao houver ponto associado abaixo dele."""
    with conectar(caminho_banco) as conexao:
        ultima = conexao.execute(
            """
            SELECT id, profundidade_inicial, profundidade_final
            FROM rascunhos_camadas
            WHERE sondagem_id = ?
            ORDER BY profundidade_final DESC
            LIMIT 1
            """,
            (int(sondagem_id),),
        ).fetchone()
        if not ultima:
            raise ErroValidacao("O diario de campo ainda nao possui intervalos.")

        nova_profundidade = float(ultima["profundidade_inicial"])
        maxima_pontos = _profundidade_maxima_pontos(conexao, sondagem_id)
        if (
            maxima_pontos is not None
            and maxima_pontos > nova_profundidade + TOLERANCIA_PROFUNDIDADE
        ):
            raise ErroValidacao(
                "Remova primeiro as amostras ou medicoes localizadas no intervalo "
                "que sera excluido."
            )

        conexao.execute(
            "DELETE FROM rascunhos_camadas WHERE id = ?",
            (int(ultima["id"]),),
        )
        conexao.execute(
            "UPDATE sondagens SET profundidade_atual = ? WHERE id = ?",
            (nova_profundidade, int(sondagem_id)),
        )


def limpar_rascunho_camadas(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Limpa os intervalos de campo quando nao existem pontos associados."""
    with conectar(caminho_banco) as conexao:
        maxima_pontos = _profundidade_maxima_pontos(conexao, sondagem_id)
        if maxima_pontos is not None:
            raise ErroValidacao(
                "Remova as amostras e medicoes de VOC antes de limpar o diario."
            )
        conexao.execute(
            "DELETE FROM rascunhos_camadas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.execute(
            """
            UPDATE sondagens
            SET profundidade_atual = 0,
                status = CASE WHEN status = ? THEN ? ELSE status END
            WHERE id = ?
            """,
            (STATUS_EXECUCAO, STATUS_PLANEJADA, int(sondagem_id)),
        )


def validar_rascunho_para_conclusao(
    sondagem_id: int,
    profundidade_final: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Valida o diario de campo contra a profundidade final informada."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        return False, ["Sondagem nao encontrada."], []
    try:
        final = _validar_numero_finito(profundidade_final, "profundidade final")
    except ErroValidacao as erro:
        return False, [str(erro)], []
    if final <= 0:
        return False, ["A profundidade final deve ser maior que zero."], []

    camadas = listar_rascunho_camadas(sondagem_id, caminho_banco).to_dict("records")
    normalizadas, erros = _normalizar_e_validar_camadas(
        camadas,
        final,
        float(sondagem["altitude"]),
        exigir_cobertura_total=True,
    )

    with conectar(caminho_banco) as conexao:
        maxima_pontos = _profundidade_maxima_pontos(conexao, sondagem_id)
    if maxima_pontos is not None and maxima_pontos > final + TOLERANCIA_PROFUNDIDADE:
        erros.append(
            "Ha amostra ou medicao de VOC abaixo da profundidade final informada."
        )

    nivel = sondagem.get("nivel_agua_estatico")
    if nivel is not None and not pd.isna(nivel) and float(nivel) > final:
        erros.append("O NA informado esta abaixo da profundidade final da sondagem.")
    return not erros, erros, normalizadas


def finalizar_sondagem(
    sondagem_id: int,
    profundidade_final: float,
    nivel_agua_estatico: float | None,
    data_conclusao: str | date | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Valida, publica o perfil final e encerra a sondagem atomicamente."""
    final = _validar_numero_finito(profundidade_final, "profundidade final")
    valido, erros, normalizadas = validar_rascunho_para_conclusao(
        sondagem_id,
        final,
        caminho_banco,
    )
    if not valido:
        raise ErroValidacao("\n".join(erros))

    if nivel_agua_estatico is None or pd.isna(nivel_agua_estatico):
        nivel = None
    else:
        nivel = _validar_numero_finito(nivel_agua_estatico, "nivel d'agua")
        if nivel < 0 or nivel > final + TOLERANCIA_PROFUNDIDADE:
            raise ErroValidacao(
                "O nivel d'agua deve estar entre zero e a profundidade final."
            )

    data_texto = str(data_conclusao or date.today())
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM camadas_litologicas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.executemany(
            """
            INSERT INTO camadas_litologicas (
                sondagem_id, profundidade_inicial, profundidade_final,
                descricao_tatil_visual, classificacao, tipo_aquifero,
                cota_topo, cota_base
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(sondagem_id),
                    camada["profundidade_inicial"],
                    camada["profundidade_final"],
                    camada["descricao_tatil_visual"],
                    camada["classificacao"],
                    camada["tipo_aquifero"],
                    camada["cota_topo"],
                    camada["cota_base"],
                )
                for camada in normalizadas
            ],
        )
        conexao.execute(
            "DELETE FROM rascunhos_camadas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.execute(
            """
            UPDATE sondagens
            SET profundidade_total = ?, profundidade_planejada = ?,
                profundidade_atual = ?, nivel_agua_estatico = ?,
                status = ?, data_conclusao = ?
            WHERE id = ?
            """,
            (
                final,
                final,
                final,
                nivel,
                STATUS_CONCLUIDA,
                data_texto,
                int(sondagem_id),
            ),
        )


def reabrir_sondagem(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Copia o perfil final para o diario e libera a sondagem para correcao."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    if sondagem["status"] != STATUS_CONCLUIDA:
        raise ErroValidacao("A sondagem selecionada ainda nao esta concluida.")
    camadas = listar_camadas(sondagem_id, caminho_banco)
    if camadas.empty:
        raise ErroValidacao("O perfil final nao possui camadas para reabertura.")

    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM rascunhos_camadas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.executemany(
            """
            INSERT INTO rascunhos_camadas (
                sondagem_id, profundidade_inicial, profundidade_final,
                descricao_tatil_visual, classificacao, tipo_aquifero,
                cota_topo, cota_base
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(sondagem_id),
                    float(linha["profundidade_inicial"]),
                    float(linha["profundidade_final"]),
                    str(linha["descricao_tatil_visual"]),
                    str(linha["classificacao"]),
                    str(linha["tipo_aquifero"]),
                    float(linha["cota_topo"]),
                    float(linha["cota_base"]),
                )
                for _, linha in camadas.iterrows()
            ],
        )
        conexao.execute(
            """
            UPDATE sondagens
            SET status = ?, data_conclusao = NULL,
                profundidade_planejada = MAX(profundidade_planejada, profundidade_total)
            WHERE id = ?
            """,
            (STATUS_EXECUCAO, int(sondagem_id)),
        )


def validar_perfil_litologico(
    sondagem_id: int,
    camadas: list[dict[str, Any]],
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Valida um perfil final completo sem alterar o banco."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        return False, ["Sondagem nao encontrada."], []
    normalizadas, erros = _normalizar_e_validar_camadas(
        camadas,
        float(sondagem["profundidade_total"]),
        float(sondagem["altitude"]),
        exigir_cobertura_total=True,
    )
    return not erros, erros, normalizadas


def salvar_perfil_litologico(
    sondagem_id: int,
    camadas: list[dict[str, Any]],
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Substitui diretamente um perfil final apos validacao integral."""
    valido, erros, normalizadas = validar_perfil_litologico(
        sondagem_id,
        camadas,
        caminho_banco,
    )
    if not valido:
        raise ErroValidacao("\n".join(erros))
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")

    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM camadas_litologicas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.executemany(
            """
            INSERT INTO camadas_litologicas (
                sondagem_id, profundidade_inicial, profundidade_final,
                descricao_tatil_visual, classificacao, tipo_aquifero,
                cota_topo, cota_base
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    int(sondagem_id),
                    camada["profundidade_inicial"],
                    camada["profundidade_final"],
                    camada["descricao_tatil_visual"],
                    camada["classificacao"],
                    camada["tipo_aquifero"],
                    camada["cota_topo"],
                    camada["cota_base"],
                )
                for camada in normalizadas
            ],
        )
        conexao.execute(
            """
            UPDATE sondagens
            SET profundidade_atual = profundidade_total,
                profundidade_planejada = profundidade_total,
                status = ?, data_conclusao = COALESCE(data_conclusao, ?)
            WHERE id = ?
            """,
            (STATUS_CONCLUIDA, date.today().isoformat(), int(sondagem_id)),
        )


def adicionar_camada(
    sondagem_id: int,
    profundidade_inicial: float,
    profundidade_final: float,
    descricao_tatil_visual: str,
    classificacao: str,
    tipo_aquifero: str,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Mantem a operacao antiga para perfis finais completos."""
    existentes = listar_camadas(sondagem_id, caminho_banco).to_dict("records")
    existentes.append(
        {
            "profundidade_inicial": profundidade_inicial,
            "profundidade_final": profundidade_final,
            "descricao_tatil_visual": descricao_tatil_visual,
            "classificacao": classificacao,
            "tipo_aquifero": tipo_aquifero,
        }
    )
    salvar_perfil_litologico(sondagem_id, existentes, caminho_banco)


def _validar_profundidade_pontual(
    sondagem_id: int,
    profundidade: float,
    caminho_banco: str | Path,
) -> float:
    """Valida um ponto contra a profundidade realmente executada."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    profundidade_num = _validar_numero_finito(profundidade, "profundidade")
    if profundidade_num < 0:
        raise ErroValidacao("A profundidade nao pode ser negativa.")
    limite = float(sondagem["profundidade_atual"] or 0)
    if limite <= 0:
        raise ErroValidacao(
            "Registre ao menos um intervalo executado antes deste ponto."
        )
    if profundidade_num > limite + TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"A profundidade excede o trecho executado ({limite:.3f} m)."
        )
    return profundidade_num


def adicionar_coleta(
    sondagem_id: int,
    profundidade_coleta: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona um ponto de coleta de amostra."""
    profundidade_num = _validar_profundidade_pontual(
        sondagem_id,
        profundidade_coleta,
        caminho_banco,
    )
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO coletas_amostras (sondagem_id, profundidade_coleta)
            VALUES (?, ?)
            """,
            (int(sondagem_id), profundidade_num),
        )
        return int(cursor.lastrowid)


def listar_coletas(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista os pontos de coleta de uma sondagem."""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT id, sondagem_id, profundidade_coleta
            FROM coletas_amostras
            WHERE sondagem_id = ?
            ORDER BY profundidade_coleta
            """,
            (int(sondagem_id),),
        ).fetchall()
    return _dataframe_de_linhas(
        linhas,
        ["id", "sondagem_id", "profundidade_coleta"],
    )


def remover_coleta(
    coleta_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove um ponto de coleta."""
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM coletas_amostras WHERE id = ?",
            (int(coleta_id),),
        )


def adicionar_voc(
    sondagem_id: int,
    profundidade: float,
    concentracao: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona uma medicao de VOC em determinada profundidade."""
    profundidade_num = _validar_profundidade_pontual(
        sondagem_id,
        profundidade,
        caminho_banco,
    )
    concentracao_num = _validar_numero_finito(concentracao, "concentracao de VOC")
    if concentracao_num < 0:
        raise ErroValidacao("A concentracao de VOC nao pode ser negativa.")
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO voc_medicoes (sondagem_id, profundidade, concentracao)
            VALUES (?, ?, ?)
            """,
            (int(sondagem_id), profundidade_num, concentracao_num),
        )
        return int(cursor.lastrowid)


def listar_voc(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista medicoes de VOC em ordem de profundidade."""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT id, sondagem_id, profundidade, concentracao
            FROM voc_medicoes
            WHERE sondagem_id = ?
            ORDER BY profundidade
            """,
            (int(sondagem_id),),
        ).fetchall()
    return _dataframe_de_linhas(
        linhas,
        ["id", "sondagem_id", "profundidade", "concentracao"],
    )


def remover_voc(
    medicao_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove uma medicao de VOC."""
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM voc_medicoes WHERE id = ?",
            (int(medicao_id),),
        )


def _valor_unico(grupo: pd.DataFrame, coluna: str) -> Any:
    """Exige um valor unico por sondagem em uma coluna de cabecalho."""
    valores = [
        valor
        for valor in grupo[coluna].tolist()
        if not pd.isna(valor) and str(valor).strip() != ""
    ]
    if not valores:
        return None
    primeiro = valores[0]
    for valor in valores[1:]:
        try:
            iguais = math.isclose(
                float(valor),
                float(primeiro),
                rel_tol=0,
                abs_tol=TOLERANCIA_PROFUNDIDADE,
            )
        except (TypeError, ValueError):
            iguais = str(valor).strip() == str(primeiro).strip()
        if not iguais:
            raise ErroValidacao(
                f"A coluna '{coluna}' possui valores divergentes para a mesma sondagem."
            )
    return primeiro


def _obter_coordenadas_grupo(
    grupo: pd.DataFrame,
    tem_genericas: bool,
    tem_geograficas: bool,
) -> tuple[int, float, float, float, float]:
    """Le coordenadas de um dos dois esquemas aceitos no CSV."""
    usar_genericas = False
    if tem_genericas:
        valores = [
            _valor_unico(grupo, coluna)
            for coluna in COLUNAS_CSV_COORDENADAS_GENERICAS
        ]
        usar_genericas = all(valor is not None for valor in valores)

    if usar_genericas:
        epsg = int(float(_valor_unico(grupo, "crs_epsg")))
        x = float(_valor_unico(grupo, "coordenada_x"))
        y = float(_valor_unico(grupo, "coordenada_y"))
    elif tem_geograficas:
        epsg = EPSG_SIRGAS_2000_GEOGRAFICO
        x = float(_valor_unico(grupo, "longitude"))
        y = float(_valor_unico(grupo, "latitude"))
    else:
        raise ErroValidacao(
            "Informe crs_epsg/coordenada_x/coordenada_y ou latitude/longitude."
        )

    latitude, longitude = converter_para_sirgas2000(x, y, epsg)
    return epsg, x, y, latitude, longitude


def importar_dataframe(
    dataframe: pd.DataFrame,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Importa perfis completos com coordenadas em EPSG configuravel."""
    inicializar_banco(caminho_banco)
    dados = dataframe.copy()
    dados.columns = [str(coluna).strip().lower() for coluna in dados.columns]

    faltantes = [coluna for coluna in COLUNAS_CSV_BASE if coluna not in dados.columns]
    if faltantes:
        raise ErroValidacao(
            "Colunas obrigatorias ausentes: " + ", ".join(faltantes)
        )

    tem_genericas = all(
        coluna in dados.columns for coluna in COLUNAS_CSV_COORDENADAS_GENERICAS
    )
    tem_geograficas = all(
        coluna in dados.columns for coluna in COLUNAS_CSV_COORDENADAS_GEOGRAFICAS
    )
    if not tem_genericas and not tem_geograficas:
        raise ErroValidacao(
            "O CSV deve conter crs_epsg/coordenada_x/coordenada_y ou latitude/longitude."
        )

    dados["projeto"] = dados["projeto"].astype("string").str.strip()
    dados["sondagem_nome"] = dados["sondagem_nome"].astype("string").str.strip()
    relatorio: list[dict[str, str]] = []
    grupos = dados.groupby(["projeto", "sondagem_nome"], dropna=False, sort=False)

    for (projeto_nome, sondagem_nome), grupo in grupos:
        projeto_texto = "" if pd.isna(projeto_nome) else str(projeto_nome).strip()
        sondagem_texto = "" if pd.isna(sondagem_nome) else str(sondagem_nome).strip()
        identificacao = {
            "projeto": projeto_texto or "<vazio>",
            "sondagem": sondagem_texto or "<vazio>",
        }

        try:
            if not projeto_texto:
                raise ErroValidacao("O nome do projeto esta vazio.")
            if not sondagem_texto:
                raise ErroValidacao("O nome da sondagem esta vazio.")

            epsg, x, y, latitude, longitude = _obter_coordenadas_grupo(
                grupo,
                tem_genericas,
                tem_geograficas,
            )
            altitude = float(_valor_unico(grupo, "altitude"))
            nivel_bruto = _valor_unico(grupo, "nivel_agua")
            nivel = None if nivel_bruto is None else float(nivel_bruto)
            profundidade_total = float(
                pd.to_numeric(grupo["profundidade_final"], errors="raise").max()
            )
            _validar_dados_sondagem(
                sondagem_texto,
                x,
                y,
                epsg,
                altitude,
                profundidade_total,
                nivel,
            )

            camadas_brutas = [
                {
                    "profundidade_inicial": linha["profundidade_inicial"],
                    "profundidade_final": linha["profundidade_final"],
                    "descricao_tatil_visual": linha["descricao"],
                    "classificacao": linha["classificacao"],
                    "tipo_aquifero": linha["tipo_aquifero"],
                }
                for _, linha in grupo.iterrows()
            ]
            camadas_normalizadas, erros = _normalizar_e_validar_camadas(
                camadas_brutas,
                profundidade_total,
                altitude,
                exigir_cobertura_total=True,
            )
            if erros:
                raise ErroValidacao(" | ".join(erros))

            data_texto = date.today().isoformat()
            if "data" in grupo.columns:
                data_bruta = _valor_unico(grupo, "data")
                if data_bruta is not None:
                    data_texto = str(data_bruta)

            with conectar(caminho_banco) as conexao:
                projeto = conexao.execute(
                    "SELECT id FROM projetos WHERE nome = ?",
                    (projeto_texto,),
                ).fetchone()
                if projeto:
                    projeto_id = int(projeto["id"])
                else:
                    cursor = conexao.execute(
                        "INSERT INTO projetos (nome, descricao) VALUES (?, ?)",
                        (projeto_texto, "Projeto criado por importacao CSV."),
                    )
                    projeto_id = int(cursor.lastrowid)

                sondagem = conexao.execute(
                    """
                    SELECT id FROM sondagens
                    WHERE projeto_id = ? AND nome_furo = ?
                    """,
                    (projeto_id, sondagem_texto),
                ).fetchone()

                if sondagem:
                    sondagem_id = int(sondagem["id"])
                    maxima_pontos = _profundidade_maxima_pontos(conexao, sondagem_id)
                    if (
                        maxima_pontos is not None
                        and maxima_pontos > profundidade_total + TOLERANCIA_PROFUNDIDADE
                    ):
                        raise ErroValidacao(
                            "Ha coleta ou VOC existente abaixo da nova profundidade final."
                        )
                    conexao.execute(
                        """
                        UPDATE sondagens
                        SET latitude = ?, longitude = ?, crs_entrada = ?,
                            coordenada_x = ?, coordenada_y = ?, altitude = ?,
                            profundidade_total = ?, profundidade_planejada = ?,
                            profundidade_atual = ?, nivel_agua_estatico = ?,
                            status = ?, data = ?, data_conclusao = ?
                        WHERE id = ?
                        """,
                        (
                            latitude,
                            longitude,
                            epsg,
                            x,
                            y,
                            altitude,
                            profundidade_total,
                            profundidade_total,
                            profundidade_total,
                            nivel,
                            STATUS_CONCLUIDA,
                            data_texto,
                            data_texto,
                            sondagem_id,
                        ),
                    )
                else:
                    cursor = conexao.execute(
                        """
                        INSERT INTO sondagens (
                            projeto_id, nome_furo, latitude, longitude,
                            crs_entrada, coordenada_x, coordenada_y, altitude,
                            profundidade_total, profundidade_planejada,
                            profundidade_atual, nivel_agua_estatico,
                            status, data, data_conclusao
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            projeto_id,
                            sondagem_texto,
                            latitude,
                            longitude,
                            epsg,
                            x,
                            y,
                            altitude,
                            profundidade_total,
                            profundidade_total,
                            profundidade_total,
                            nivel,
                            STATUS_CONCLUIDA,
                            data_texto,
                            data_texto,
                        ),
                    )
                    sondagem_id = int(cursor.lastrowid)

                conexao.execute(
                    "DELETE FROM camadas_litologicas WHERE sondagem_id = ?",
                    (sondagem_id,),
                )
                conexao.execute(
                    "DELETE FROM rascunhos_camadas WHERE sondagem_id = ?",
                    (sondagem_id,),
                )
                conexao.executemany(
                    """
                    INSERT INTO camadas_litologicas (
                        sondagem_id, profundidade_inicial, profundidade_final,
                        descricao_tatil_visual, classificacao, tipo_aquifero,
                        cota_topo, cota_base
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            sondagem_id,
                            camada["profundidade_inicial"],
                            camada["profundidade_final"],
                            camada["descricao_tatil_visual"],
                            camada["classificacao"],
                            camada["tipo_aquifero"],
                            camada["cota_topo"],
                            camada["cota_base"],
                        )
                        for camada in camadas_normalizadas
                    ],
                )

            relatorio.append(
                {
                    **identificacao,
                    "status": "Sucesso",
                    "mensagem": (
                        f"{len(camadas_normalizadas)} camadas importadas; "
                        f"EPSG:{epsg}; profundidade final de {profundidade_total:.2f} m."
                    ),
                }
            )
        except Exception as erro:
            relatorio.append(
                {
                    **identificacao,
                    "status": "Erro",
                    "mensagem": str(erro),
                }
            )

    return pd.DataFrame(
        relatorio,
        columns=["projeto", "sondagem", "status", "mensagem"],
    )


TABELAS_OBRIGATORIAS_BANCO = {
    "projetos",
    "sondagens",
    "camadas_litologicas",
    "coletas_amostras",
    "voc_medicoes",
}


def validar_arquivo_banco(caminho_banco: str | Path) -> None:
    """Valida a integridade e a estrutura minima de um arquivo SQLite."""
    caminho = Path(caminho_banco)
    if not caminho.exists() or not caminho.is_file():
        raise ErroValidacao("O arquivo de banco de dados nao foi encontrado.")
    if caminho.stat().st_size == 0:
        raise ErroValidacao("O arquivo de banco de dados esta vazio.")

    try:
        conexao = sqlite3.connect(
            f"{caminho.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=10,
        )
        try:
            verificacoes = [
                str(linha[0]).lower()
                for linha in conexao.execute("PRAGMA quick_check").fetchall()
            ]
            if verificacoes != ["ok"]:
                raise ErroValidacao(
                    "O arquivo SQLite falhou na verificacao de integridade: "
                    + "; ".join(verificacoes)
                )
            tabelas = {
                str(linha[0])
                for linha in conexao.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            faltantes = sorted(TABELAS_OBRIGATORIAS_BANCO - tabelas)
            if faltantes:
                raise ErroValidacao(
                    "O arquivo nao possui as tabelas obrigatorias: "
                    + ", ".join(faltantes)
                )
        finally:
            conexao.close()
    except ErroValidacao:
        raise
    except sqlite3.DatabaseError as erro:
        raise ErroValidacao(f"O arquivo informado nao e um SQLite valido: {erro}") from erro


def exportar_banco_bytes(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> bytes:
    """Gera uma copia consistente do banco, incluindo alteracoes do WAL."""
    caminho = Path(caminho_banco)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    inicializar_banco(caminho)

    descritor, nome_temporario = tempfile.mkstemp(
        prefix="backup_hidro_",
        suffix=".db",
        dir=str(caminho.parent),
    )
    os.close(descritor)
    temporario = Path(nome_temporario)
    try:
        with conectar(caminho) as origem:
            destino = sqlite3.connect(str(temporario))
            try:
                origem.backup(destino)
                destino.commit()
            finally:
                destino.close()
        return temporario.read_bytes()
    finally:
        temporario.unlink(missing_ok=True)


def restaurar_banco_bytes(
    conteudo: bytes,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Valida e substitui atomicamente o banco atual por um backup enviado."""
    if not conteudo:
        raise ErroValidacao("O arquivo de backup esta vazio.")
    if len(conteudo) > 100 * 1024 * 1024:
        raise ErroValidacao("O backup excede o limite de 100 MB.")

    destino = Path(caminho_banco)
    destino.parent.mkdir(parents=True, exist_ok=True)
    descritor, nome_temporario = tempfile.mkstemp(
        prefix="restauracao_hidro_",
        suffix=".db",
        dir=str(destino.parent),
    )
    os.close(descritor)
    temporario = Path(nome_temporario)
    try:
        temporario.write_bytes(conteudo)
        validar_arquivo_banco(temporario)
        Path(f"{destino}-wal").unlink(missing_ok=True)
        Path(f"{destino}-shm").unlink(missing_ok=True)
        os.replace(temporario, destino)
        inicializar_banco(destino)
    finally:
        temporario.unlink(missing_ok=True)


def obter_resumo_banco(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, int]:
    """Retorna contagens simples para o painel lateral."""
    inicializar_banco(caminho_banco)
    with conectar(caminho_banco) as conexao:
        return {
            "projetos": int(
                conexao.execute("SELECT COUNT(*) FROM projetos").fetchone()[0]
            ),
            "sondagens": int(
                conexao.execute("SELECT COUNT(*) FROM sondagens").fetchone()[0]
            ),
            "camadas": int(
                conexao.execute(
                    "SELECT COUNT(*) FROM camadas_litologicas"
                ).fetchone()[0]
            ),
            "intervalos_campo": int(
                conexao.execute(
                    "SELECT COUNT(*) FROM rascunhos_camadas"
                ).fetchone()[0]
            ),
        }
