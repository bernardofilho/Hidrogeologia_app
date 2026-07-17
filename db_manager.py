from __future__ import annotations

import math
import os
import sqlite3
import tempfile
from datetime import date, datetime
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

COMPONENTES_CONSTRUTIVOS_VALIDOS = [
    "Tubo cego",
    "Seção filtrante",
    "Pré-filtro",
    "Selo de bentonita",
    "Cimentação",
    "Fundo / sedimentador",
]

CATEGORIAS_FOTOS_VALIDAS = [
    "Locação",
    "Perfuração",
    "Amostras",
    "Litologia",
    "Instalação do poço",
    "Seção filtrante",
    "Pré-filtro",
    "Selo e cimentação",
    "Câmara de calçada",
    "Desenvolvimento",
    "Acabamento final",
    "Outro",
]

TIPOS_LEITURA_NA_VALIDOS = [
    "Durante a perfuração",
    "Estático / estabilizado",
    "Após a perfuração",
    "Após a instalação",
    "Antes do desenvolvimento",
    "Após o desenvolvimento",
    "Monitoramento",
]

METODOS_DESENVOLVIMENTO_VALIDOS = [
    "Bombeamento",
    "Air lift",
    "Pistoneamento",
    "Bailer",
    "Bombeamento e pistoneamento",
    "Outro",
]


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
    _adicionar_coluna_se_ausente(
        conexao, "projetos", "cliente", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "projetos", "localizacao", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "projetos", "responsavel_tecnico", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "projetos", "registro_profissional", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "origem_coordenada", "TEXT DEFAULT 'Manual'"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "precisao_gps_m", "REAL"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "data_captura_gps", "TEXT"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "metodo_perfuracao", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "equipamento", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "empresa_executora", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "responsavel_campo", "TEXT DEFAULT ''"
    )
    _adicionar_coluna_se_ausente(
        conexao, "sondagens", "observacoes_gerais", "TEXT DEFAULT ''"
    )

    conexao.execute(
        """
        UPDATE sondagens
        SET crs_entrada = COALESCE(crs_entrada, 4674),
            coordenada_x = COALESCE(coordenada_x, longitude),
            coordenada_y = COALESCE(coordenada_y, latitude),
            profundidade_planejada = COALESCE(profundidade_planejada, profundidade_total),
            profundidade_atual = COALESCE(profundidade_atual, 0),
            status = COALESCE(NULLIF(status, ''), 'Planejada'),
            origem_coordenada = COALESCE(NULLIF(origem_coordenada, ''), 'Manual'),
            metodo_perfuracao = COALESCE(metodo_perfuracao, ''),
            equipamento = COALESCE(equipamento, ''),
            empresa_executora = COALESCE(empresa_executora, ''),
            responsavel_campo = COALESCE(responsavel_campo, ''),
            observacoes_gerais = COALESCE(observacoes_gerais, '')
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
                descricao TEXT NOT NULL DEFAULT '',
                cliente TEXT NOT NULL DEFAULT '',
                localizacao TEXT NOT NULL DEFAULT '',
                responsavel_tecnico TEXT NOT NULL DEFAULT '',
                registro_profissional TEXT NOT NULL DEFAULT ''
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
                origem_coordenada TEXT NOT NULL DEFAULT 'Manual',
                precisao_gps_m REAL,
                data_captura_gps TEXT,
                metodo_perfuracao TEXT NOT NULL DEFAULT '',
                equipamento TEXT NOT NULL DEFAULT '',
                empresa_executora TEXT NOT NULL DEFAULT '',
                responsavel_campo TEXT NOT NULL DEFAULT '',
                observacoes_gerais TEXT NOT NULL DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS leituras_nivel_agua (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                data_hora TEXT NOT NULL,
                profundidade_m REAL NOT NULL CHECK (profundidade_m >= 0),
                tipo TEXT NOT NULL,
                usar_como_estatico INTEGER NOT NULL DEFAULT 0,
                observacoes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS fotos_sondagem (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                categoria TEXT NOT NULL,
                profundidade_m REAL,
                legenda TEXT NOT NULL DEFAULT '',
                nome_arquivo TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                conteudo BLOB NOT NULL,
                largura_px INTEGER,
                altura_px INTEGER,
                tamanho_bytes INTEGER NOT NULL,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pocos_monitoramento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL UNIQUE,
                data_instalacao TEXT,
                profundidade_poco REAL NOT NULL CHECK (profundidade_poco > 0),
                diametro_perfuracao_mm REAL,
                diametro_revestimento_mm REAL,
                material_revestimento TEXT NOT NULL DEFAULT '',
                fabricante_modelo TEXT NOT NULL DEFAULT '',
                cota_boca_tubo REAL,
                altura_boca_tubo_m REAL,
                tipo_protecao_superficial TEXT NOT NULL DEFAULT '',
                camara_calcada INTEGER NOT NULL DEFAULT 0,
                tampa TEXT NOT NULL DEFAULT '',
                responsavel_instalacao TEXT NOT NULL DEFAULT '',
                observacoes TEXT NOT NULL DEFAULT '',
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS intervalos_construtivos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL,
                componente TEXT NOT NULL,
                profundidade_inicial REAL NOT NULL CHECK (profundidade_inicial >= 0),
                profundidade_final REAL NOT NULL CHECK (
                    profundidade_final > profundidade_inicial
                ),
                material TEXT NOT NULL DEFAULT '',
                especificacao TEXT NOT NULL DEFAULT '',
                diametro_mm REAL,
                abertura_ranhura_mm REAL,
                granulometria TEXT NOT NULL DEFAULT '',
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS desenvolvimentos_poco (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sondagem_id INTEGER NOT NULL UNIQUE,
                realizado INTEGER NOT NULL DEFAULT 0,
                data TEXT,
                metodo TEXT NOT NULL DEFAULT '',
                duracao_min REAL,
                profundidade_equipamento_m REAL,
                na_antes_m REAL,
                na_depois_m REAL,
                vazao_l_min REAL,
                volume_retirado_l REAL,
                turbidez_inicial_ntu REAL,
                turbidez_final_ntu REAL,
                ph_final REAL,
                condutividade_final_us_cm REAL,
                temperatura_final_c REAL,
                responsavel TEXT NOT NULL DEFAULT '',
                motivo_nao_realizado TEXT NOT NULL DEFAULT '',
                observacoes TEXT NOT NULL DEFAULT '',
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sondagem_id) REFERENCES sondagens(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS leituras_desenvolvimento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                desenvolvimento_id INTEGER NOT NULL,
                tempo_min REAL NOT NULL CHECK (tempo_min >= 0),
                nivel_agua_m REAL,
                vazao_l_min REAL,
                turbidez_ntu REAL,
                ph REAL,
                condutividade_us_cm REAL,
                temperatura_c REAL,
                observacoes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (desenvolvimento_id)
                    REFERENCES desenvolvimentos_poco(id) ON DELETE CASCADE
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
            CREATE INDEX IF NOT EXISTS idx_leituras_na_sondagem
                ON leituras_nivel_agua(sondagem_id, data_hora);
            CREATE INDEX IF NOT EXISTS idx_fotos_sondagem
                ON fotos_sondagem(sondagem_id, criado_em);
            CREATE INDEX IF NOT EXISTS idx_intervalos_construtivos_sondagem
                ON intervalos_construtivos(sondagem_id, profundidade_inicial);
            CREATE INDEX IF NOT EXISTS idx_leituras_desenvolvimento
                ON leituras_desenvolvimento(desenvolvimento_id, tempo_min);
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
    *,
    cliente: str = "",
    localizacao: str = "",
    responsavel_tecnico: str = "",
    registro_profissional: str = "",
) -> int:
    """Insere um projeto e devolve o identificador criado."""
    nome_limpo = str(nome).strip()
    if not nome_limpo:
        raise ErroValidacao("O nome do projeto e obrigatorio.")

    try:
        with conectar(caminho_banco) as conexao:
            cursor = conexao.execute(
                """
                INSERT INTO projetos (
                    nome, descricao, cliente, localizacao,
                    responsavel_tecnico, registro_profissional
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    nome_limpo,
                    str(descricao or "").strip(),
                    str(cliente or "").strip(),
                    str(localizacao or "").strip(),
                    str(responsavel_tecnico or "").strip(),
                    str(registro_profissional or "").strip(),
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as erro:
        raise ErroValidacao(f"Nao foi possivel criar o projeto: {erro}") from erro


def atualizar_projeto(
    projeto_id: int,
    descricao: str = "",
    cliente: str = "",
    localizacao: str = "",
    responsavel_tecnico: str = "",
    registro_profissional: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Atualiza os metadados usados nos relatorios do projeto."""
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            UPDATE projetos
            SET descricao = ?, cliente = ?, localizacao = ?,
                responsavel_tecnico = ?, registro_profissional = ?
            WHERE id = ?
            """,
            (
                str(descricao or "").strip(),
                str(cliente or "").strip(),
                str(localizacao or "").strip(),
                str(responsavel_tecnico or "").strip(),
                str(registro_profissional or "").strip(),
                int(projeto_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ErroValidacao("Projeto nao encontrado.")


def listar_projetos(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista os projetos cadastrados."""
    colunas = [
        "id", "nome", "descricao", "cliente", "localizacao",
        "responsavel_tecnico", "registro_profissional",
    ]
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT id, nome, descricao, cliente, localizacao,
                   responsavel_tecnico, registro_profissional
            FROM projetos ORDER BY nome
            """
        ).fetchall()
    return _dataframe_de_linhas(linhas, colunas)


def obter_projeto(
    projeto_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem um projeto pelo identificador."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            """
            SELECT id, nome, descricao, cliente, localizacao,
                   responsavel_tecnico, registro_profissional
            FROM projetos WHERE id = ?
            """,
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
    origem_coordenada: str = "Manual",
    precisao_gps_m: float | None = None,
    data_captura_gps: str | None = None,
    metodo_perfuracao: str = "",
    equipamento: str = "",
    empresa_executora: str = "",
    responsavel_campo: str = "",
    observacoes_gerais: str = "",
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
                    profundidade_atual, nivel_agua_estatico, status, data,
                    origem_coordenada, precisao_gps_m, data_captura_gps,
                    metodo_perfuracao, equipamento, empresa_executora,
                    responsavel_campo, observacoes_gerais
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    str(origem_coordenada or "Manual").strip() or "Manual",
                    (
                        None
                        if precisao_gps_m is None
                        else _validar_numero_finito(precisao_gps_m, "precisao GPS")
                    ),
                    str(data_captura_gps).strip() if data_captura_gps else None,
                    str(metodo_perfuracao or "").strip(),
                    str(equipamento or "").strip(),
                    str(empresa_executora or "").strip(),
                    str(responsavel_campo or "").strip(),
                    str(observacoes_gerais or "").strip(),
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
    *,
    origem_coordenada: str = "Manual",
    precisao_gps_m: float | None = None,
    data_captura_gps: str | None = None,
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
                latitude = ?, longitude = ?, origem_coordenada = ?,
                precisao_gps_m = ?, data_captura_gps = ?
            WHERE id = ?
            """,
            (
                epsg,
                x,
                y,
                latitude,
                longitude,
                str(origem_coordenada or "Manual").strip() or "Manual",
                None if precisao_gps_m is None else float(precisao_gps_m),
                str(data_captura_gps).strip() if data_captura_gps else None,
                int(sondagem_id),
            ),
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
            s.origem_coordenada,
            s.precisao_gps_m,
            s.data_captura_gps,
            s.metodo_perfuracao,
            s.equipamento,
            s.empresa_executora,
            s.responsavel_campo,
            s.observacoes_gerais,
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
        "origem_coordenada",
        "precisao_gps_m",
        "data_captura_gps",
        "metodo_perfuracao",
        "equipamento",
        "empresa_executora",
        "responsavel_campo",
        "observacoes_gerais",
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


def atualizar_dados_execucao(
    sondagem_id: int,
    metodo_perfuracao: str = "",
    equipamento: str = "",
    empresa_executora: str = "",
    responsavel_campo: str = "",
    observacoes_gerais: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Atualiza informacoes operacionais usadas no diario e no relatorio."""
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            UPDATE sondagens
            SET metodo_perfuracao = ?, equipamento = ?, empresa_executora = ?,
                responsavel_campo = ?, observacoes_gerais = ?
            WHERE id = ?
            """,
            (
                str(metodo_perfuracao or "").strip(),
                str(equipamento or "").strip(),
                str(empresa_executora or "").strip(),
                str(responsavel_campo or "").strip(),
                str(observacoes_gerais or "").strip(),
                int(sondagem_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ErroValidacao("Sondagem nao encontrada.")


def adicionar_leitura_nivel_agua(
    sondagem_id: int,
    profundidade_m: float,
    tipo: str,
    data_hora: str | datetime | None = None,
    observacoes: str = "",
    usar_como_estatico: bool = False,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Registra uma leitura historica de nivel d'agua."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    profundidade = _validar_numero_finito(profundidade_m, "nivel d'agua")
    if profundidade < 0:
        raise ErroValidacao("O nivel d'agua nao pode ser negativo.")
    limite = max(
        float(sondagem.get("profundidade_atual") or 0),
        float(sondagem.get("profundidade_total") or 0),
    )
    if limite > 0 and profundidade > limite + TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"A leitura nao pode ultrapassar a profundidade da sondagem ({limite:.3f} m)."
        )
    tipo_limpo = str(tipo or "").strip()
    if tipo_limpo not in TIPOS_LEITURA_NA_VALIDOS:
        raise ErroValidacao("Selecione um tipo de leitura de nivel d'agua valido.")
    data_texto = (
        data_hora.isoformat(timespec="minutes")
        if isinstance(data_hora, datetime)
        else str(data_hora or datetime.now().isoformat(timespec="minutes"))
    )
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO leituras_nivel_agua (
                sondagem_id, data_hora, profundidade_m, tipo,
                usar_como_estatico, observacoes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(sondagem_id),
                data_texto,
                profundidade,
                tipo_limpo,
                int(bool(usar_como_estatico)),
                str(observacoes or "").strip(),
            ),
        )
        if usar_como_estatico:
            conexao.execute(
                "UPDATE leituras_nivel_agua SET usar_como_estatico = 0 WHERE sondagem_id = ? AND id <> ?",
                (int(sondagem_id), int(cursor.lastrowid)),
            )
            conexao.execute(
                "UPDATE sondagens SET nivel_agua_estatico = ? WHERE id = ?",
                (profundidade, int(sondagem_id)),
            )
        return int(cursor.lastrowid)


def listar_leituras_nivel_agua(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista o historico de leituras de nivel d'agua."""
    colunas = [
        "id", "sondagem_id", "data_hora", "profundidade_m", "tipo",
        "usar_como_estatico", "observacoes",
    ]
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT id, sondagem_id, data_hora, profundidade_m, tipo,
                   usar_como_estatico, observacoes
            FROM leituras_nivel_agua
            WHERE sondagem_id = ?
            ORDER BY datetime(data_hora), id
            """,
            (int(sondagem_id),),
        ).fetchall()
    dados = _dataframe_de_linhas(linhas, colunas)
    if not dados.empty:
        dados["usar_como_estatico"] = dados["usar_como_estatico"].astype(bool)
    return dados


def remover_leitura_nivel_agua(
    leitura_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove uma leitura historica de nivel d'agua."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT sondagem_id, usar_como_estatico FROM leituras_nivel_agua WHERE id = ?",
            (int(leitura_id),),
        ).fetchone()
        if not linha:
            return
        conexao.execute(
            "DELETE FROM leituras_nivel_agua WHERE id = ?",
            (int(leitura_id),),
        )
        if bool(linha["usar_como_estatico"]):
            conexao.execute(
                "UPDATE sondagens SET nivel_agua_estatico = NULL WHERE id = ?",
                (int(linha["sondagem_id"]),),
            )


def adicionar_foto_sondagem(
    sondagem_id: int,
    categoria: str,
    nome_arquivo: str,
    mime_type: str,
    conteudo: bytes,
    legenda: str = "",
    profundidade_m: float | None = None,
    largura_px: int | None = None,
    altura_px: int | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Armazena uma fotografia compactada dentro do backup SQLite."""
    if not obter_sondagem(sondagem_id, caminho_banco):
        raise ErroValidacao("Sondagem nao encontrada.")
    categoria_limpa = str(categoria or "").strip()
    if categoria_limpa not in CATEGORIAS_FOTOS_VALIDAS:
        raise ErroValidacao("Selecione uma categoria de fotografia valida.")
    bytes_foto = bytes(conteudo or b"")
    if not bytes_foto:
        raise ErroValidacao("A fotografia esta vazia.")
    if len(bytes_foto) > 8 * 1024 * 1024:
        raise ErroValidacao("A fotografia compactada excede 8 MB.")
    profundidade = None
    if profundidade_m is not None and not pd.isna(profundidade_m):
        profundidade = _validar_numero_finito(profundidade_m, "profundidade da foto")
        if profundidade < 0:
            raise ErroValidacao("A profundidade da foto nao pode ser negativa.")
    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO fotos_sondagem (
                sondagem_id, categoria, profundidade_m, legenda,
                nome_arquivo, mime_type, conteudo, largura_px,
                altura_px, tamanho_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(sondagem_id),
                categoria_limpa,
                profundidade,
                str(legenda or "").strip(),
                str(nome_arquivo or "foto.jpg").strip(),
                str(mime_type or "image/jpeg").strip(),
                sqlite3.Binary(bytes_foto),
                None if largura_px is None else int(largura_px),
                None if altura_px is None else int(altura_px),
                len(bytes_foto),
            ),
        )
        return int(cursor.lastrowid)


def listar_fotos_sondagem(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
    incluir_conteudo: bool = False,
) -> list[dict[str, Any]]:
    """Lista fotos com ou sem o BLOB para reduzir uso de memoria na galeria."""
    campo_conteudo = ", conteudo" if incluir_conteudo else ""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            f"""
            SELECT id, sondagem_id, categoria, profundidade_m, legenda,
                   nome_arquivo, mime_type, largura_px, altura_px,
                   tamanho_bytes, criado_em{campo_conteudo}
            FROM fotos_sondagem
            WHERE sondagem_id = ?
            ORDER BY criado_em, id
            """,
            (int(sondagem_id),),
        ).fetchall()
    return [dict(linha) for linha in linhas]


def obter_foto_sondagem(
    foto_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem uma fotografia completa pelo identificador."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT * FROM fotos_sondagem WHERE id = ?",
            (int(foto_id),),
        ).fetchone()
    return dict(linha) if linha else None


def remover_foto_sondagem(
    foto_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove uma fotografia do banco."""
    with conectar(caminho_banco) as conexao:
        conexao.execute("DELETE FROM fotos_sondagem WHERE id = ?", (int(foto_id),))


def salvar_poco_monitoramento(
    sondagem_id: int,
    profundidade_poco: float,
    data_instalacao: str | date | None = None,
    diametro_perfuracao_mm: float | None = None,
    diametro_revestimento_mm: float | None = None,
    material_revestimento: str = "PVC geomecanico",
    fabricante_modelo: str = "",
    cota_boca_tubo: float | None = None,
    altura_boca_tubo_m: float | None = None,
    tipo_protecao_superficial: str = "Camara de calcada",
    camara_calcada: bool = True,
    tampa: str = "",
    responsavel_instalacao: str = "",
    observacoes: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Cria ou atualiza os dados gerais do poco de monitoramento."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    profundidade = _validar_numero_finito(profundidade_poco, "profundidade do poco")
    if profundidade <= 0:
        raise ErroValidacao("A profundidade do poco deve ser maior que zero.")
    limite = max(
        float(sondagem.get("profundidade_total") or 0),
        float(sondagem.get("profundidade_atual") or 0),
    )
    if limite > 0 and profundidade > limite + TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"A profundidade do poco nao pode ultrapassar a perfuracao ({limite:.3f} m)."
        )

    def opcional_positivo(valor: Any, nome: str) -> float | None:
        if valor is None or pd.isna(valor):
            return None
        numero = _validar_numero_finito(valor, nome)
        if numero < 0:
            raise ErroValidacao(f"O campo '{nome}' nao pode ser negativo.")
        return numero

    dados = (
        int(sondagem_id),
        str(data_instalacao).strip() if data_instalacao else None,
        profundidade,
        opcional_positivo(diametro_perfuracao_mm, "diametro da perfuracao"),
        opcional_positivo(diametro_revestimento_mm, "diametro do revestimento"),
        str(material_revestimento or "").strip(),
        str(fabricante_modelo or "").strip(),
        None if cota_boca_tubo is None else _validar_numero_finito(cota_boca_tubo, "cota da boca do tubo"),
        None if altura_boca_tubo_m is None else _validar_numero_finito(altura_boca_tubo_m, "altura da boca do tubo"),
        str(tipo_protecao_superficial or "").strip(),
        int(bool(camara_calcada)),
        str(tampa or "").strip(),
        str(responsavel_instalacao or "").strip(),
        str(observacoes or "").strip(),
    )
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            """
            INSERT INTO pocos_monitoramento (
                sondagem_id, data_instalacao, profundidade_poco,
                diametro_perfuracao_mm, diametro_revestimento_mm,
                material_revestimento, fabricante_modelo, cota_boca_tubo,
                altura_boca_tubo_m, tipo_protecao_superficial,
                camara_calcada, tampa, responsavel_instalacao, observacoes,
                atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sondagem_id) DO UPDATE SET
                data_instalacao = excluded.data_instalacao,
                profundidade_poco = excluded.profundidade_poco,
                diametro_perfuracao_mm = excluded.diametro_perfuracao_mm,
                diametro_revestimento_mm = excluded.diametro_revestimento_mm,
                material_revestimento = excluded.material_revestimento,
                fabricante_modelo = excluded.fabricante_modelo,
                cota_boca_tubo = excluded.cota_boca_tubo,
                altura_boca_tubo_m = excluded.altura_boca_tubo_m,
                tipo_protecao_superficial = excluded.tipo_protecao_superficial,
                camara_calcada = excluded.camara_calcada,
                tampa = excluded.tampa,
                responsavel_instalacao = excluded.responsavel_instalacao,
                observacoes = excluded.observacoes,
                atualizado_em = CURRENT_TIMESTAMP
            """,
            dados,
        )


def obter_poco_monitoramento(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem os dados gerais do poco de monitoramento."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT * FROM pocos_monitoramento WHERE sondagem_id = ?",
            (int(sondagem_id),),
        ).fetchone()
    if not linha:
        return None
    dados = dict(linha)
    dados["camara_calcada"] = bool(dados.get("camara_calcada"))
    return dados


def adicionar_intervalo_construtivo(
    sondagem_id: int,
    componente: str,
    profundidade_inicial: float,
    profundidade_final: float,
    material: str = "",
    especificacao: str = "",
    diametro_mm: float | None = None,
    abertura_ranhura_mm: float | None = None,
    granulometria: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona um intervalo do revestimento ou do espaco anular."""
    componente_limpo = str(componente or "").strip()
    if componente_limpo not in COMPONENTES_CONSTRUTIVOS_VALIDOS:
        raise ErroValidacao("Selecione um componente construtivo valido.")
    inicio = _validar_numero_finito(profundidade_inicial, "profundidade inicial")
    final = _validar_numero_finito(profundidade_final, "profundidade final")
    if inicio < 0 or final <= inicio:
        raise ErroValidacao("O intervalo construtivo deve ter inicio >= 0 e final > inicio.")
    poco = obter_poco_monitoramento(sondagem_id, caminho_banco)
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    limite = float(poco["profundidade_poco"]) if poco else float(sondagem["profundidade_total"])
    if final > limite + TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao(
            f"O intervalo nao pode ultrapassar a profundidade do poco ({limite:.3f} m)."
        )

    def opcional(valor: Any, nome: str) -> float | None:
        if valor is None or pd.isna(valor):
            return None
        numero = _validar_numero_finito(valor, nome)
        if numero < 0:
            raise ErroValidacao(f"O campo '{nome}' nao pode ser negativo.")
        return numero

    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO intervalos_construtivos (
                sondagem_id, componente, profundidade_inicial,
                profundidade_final, material, especificacao, diametro_mm,
                abertura_ranhura_mm, granulometria
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(sondagem_id), componente_limpo, inicio, final,
                str(material or "").strip(), str(especificacao or "").strip(),
                opcional(diametro_mm, "diametro"),
                opcional(abertura_ranhura_mm, "abertura das ranhuras"),
                str(granulometria or "").strip(),
            ),
        )
        return int(cursor.lastrowid)


def listar_intervalos_construtivos(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista os componentes construtivos por profundidade."""
    colunas = [
        "id", "sondagem_id", "componente", "profundidade_inicial",
        "profundidade_final", "espessura", "material", "especificacao",
        "diametro_mm", "abertura_ranhura_mm", "granulometria",
    ]
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT id, sondagem_id, componente, profundidade_inicial,
                   profundidade_final,
                   profundidade_final - profundidade_inicial AS espessura,
                   material, especificacao, diametro_mm,
                   abertura_ranhura_mm, granulometria
            FROM intervalos_construtivos
            WHERE sondagem_id = ?
            ORDER BY profundidade_inicial, profundidade_final, componente
            """,
            (int(sondagem_id),),
        ).fetchall()
    return _dataframe_de_linhas(linhas, colunas)


def remover_intervalo_construtivo(
    intervalo_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove um intervalo construtivo."""
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM intervalos_construtivos WHERE id = ?",
            (int(intervalo_id),),
        )


def validar_perfil_construtivo(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> tuple[bool, list[str], list[str], dict[str, float]]:
    """Valida o revestimento, filtro e materiais anulares do poco."""
    erros: list[str] = []
    avisos: list[str] = []
    poco = obter_poco_monitoramento(sondagem_id, caminho_banco)
    if not poco:
        return False, ["Cadastre primeiro os dados gerais do poco."], [], {}
    intervalos = listar_intervalos_construtivos(sondagem_id, caminho_banco)
    profundidade_poco = float(poco["profundidade_poco"])
    if intervalos.empty:
        return False, ["Cadastre ao menos um intervalo construtivo."], [], {
            "profundidade_poco": profundidade_poco,
            "comprimento_filtro": 0.0,
        }

    grupos_exclusivos = {
        "coluna": ["Tubo cego", "Seção filtrante", "Fundo / sedimentador"],
        "anular": ["Pré-filtro", "Selo de bentonita", "Cimentação"],
    }
    for nome_grupo, componentes in grupos_exclusivos.items():
        grupo = intervalos[intervalos["componente"].isin(componentes)].sort_values(
            "profundidade_inicial"
        )
        anterior_final: float | None = None
        anterior_nome = ""
        for _, linha in grupo.iterrows():
            inicio = float(linha["profundidade_inicial"])
            final = float(linha["profundidade_final"])
            if anterior_final is not None and inicio < anterior_final - TOLERANCIA_PROFUNDIDADE:
                erros.append(
                    f"Ha sobreposicao no grupo {nome_grupo}: {anterior_nome} e {linha['componente']}."
                )
            anterior_final = max(anterior_final or final, final)
            anterior_nome = str(linha["componente"])

    filtros = intervalos[intervalos["componente"] == "Seção filtrante"]
    comprimento_filtro = float(filtros["espessura"].sum()) if not filtros.empty else 0.0
    if filtros.empty:
        erros.append("Cadastre ao menos uma secao filtrante.")

    pre_filtros = intervalos[intervalos["componente"] == "Pré-filtro"]
    for _, filtro in filtros.iterrows():
        inicio = float(filtro["profundidade_inicial"])
        final = float(filtro["profundidade_final"])
        cobre = any(
            float(pre["profundidade_inicial"]) <= inicio + TOLERANCIA_PROFUNDIDADE
            and float(pre["profundidade_final"]) >= final - TOLERANCIA_PROFUNDIDADE
            for _, pre in pre_filtros.iterrows()
        )
        if not cobre:
            avisos.append(
                f"A secao filtrante {inicio:.2f}-{final:.2f} m nao esta integralmente envolvida por pre-filtro."
            )

    selos = intervalos[intervalos["componente"].isin(["Selo de bentonita", "Cimentação"])]
    for _, filtro in filtros.iterrows():
        for _, selo in selos.iterrows():
            sobrepoe = (
                float(selo["profundidade_inicial"]) < float(filtro["profundidade_final"]) - TOLERANCIA_PROFUNDIDADE
                and float(selo["profundidade_final"]) > float(filtro["profundidade_inicial"]) + TOLERANCIA_PROFUNDIDADE
            )
            if sobrepoe:
                erros.append(
                    f"{selo['componente']} se sobrepoe a secao filtrante entre "
                    f"{filtro['profundidade_inicial']:.2f} e {filtro['profundidade_final']:.2f} m."
                )

    maior_final = float(intervalos["profundidade_final"].max())
    if maior_final < profundidade_poco - TOLERANCIA_PROFUNDIDADE:
        avisos.append(
            f"Os intervalos cadastrados terminam em {maior_final:.2f} m, antes da profundidade do poco ({profundidade_poco:.2f} m)."
        )

    metricas = {
        "profundidade_poco": profundidade_poco,
        "comprimento_filtro": comprimento_filtro,
        "topo_filtro": float(filtros["profundidade_inicial"].min()) if not filtros.empty else math.nan,
        "base_filtro": float(filtros["profundidade_final"].max()) if not filtros.empty else math.nan,
    }
    return not erros, erros, avisos, metricas


def salvar_desenvolvimento(
    sondagem_id: int,
    realizado: bool,
    data_desenvolvimento: str | date | None = None,
    metodo: str = "",
    duracao_min: float | None = None,
    profundidade_equipamento_m: float | None = None,
    na_antes_m: float | None = None,
    na_depois_m: float | None = None,
    vazao_l_min: float | None = None,
    volume_retirado_l: float | None = None,
    turbidez_inicial_ntu: float | None = None,
    turbidez_final_ntu: float | None = None,
    ph_final: float | None = None,
    condutividade_final_us_cm: float | None = None,
    temperatura_final_c: float | None = None,
    responsavel: str = "",
    motivo_nao_realizado: str = "",
    observacoes: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Cria ou atualiza o registro de desenvolvimento do poco."""
    if not obter_sondagem(sondagem_id, caminho_banco):
        raise ErroValidacao("Sondagem nao encontrada.")
    metodo_limpo = str(metodo or "").strip()
    if realizado and metodo_limpo not in METODOS_DESENVOLVIMENTO_VALIDOS:
        raise ErroValidacao("Selecione um metodo de desenvolvimento valido.")
    if not realizado and not str(motivo_nao_realizado or "").strip():
        raise ErroValidacao("Informe o motivo quando o desenvolvimento nao foi realizado.")

    def numero_opcional(valor: Any, nome: str, minimo: float | None = 0.0) -> float | None:
        if valor is None or pd.isna(valor):
            return None
        numero = _validar_numero_finito(valor, nome)
        if minimo is not None and numero < minimo:
            raise ErroValidacao(f"O campo '{nome}' nao pode ser menor que {minimo}.")
        return numero

    parametros = (
        int(sondagem_id), int(bool(realizado)),
        str(data_desenvolvimento).strip() if data_desenvolvimento else None,
        metodo_limpo if realizado else "",
        numero_opcional(duracao_min, "duracao"),
        numero_opcional(profundidade_equipamento_m, "profundidade do equipamento"),
        numero_opcional(na_antes_m, "NA antes"),
        numero_opcional(na_depois_m, "NA depois"),
        numero_opcional(vazao_l_min, "vazao"),
        numero_opcional(volume_retirado_l, "volume retirado"),
        numero_opcional(turbidez_inicial_ntu, "turbidez inicial"),
        numero_opcional(turbidez_final_ntu, "turbidez final"),
        numero_opcional(ph_final, "pH", None),
        numero_opcional(condutividade_final_us_cm, "condutividade"),
        numero_opcional(temperatura_final_c, "temperatura", None),
        str(responsavel or "").strip(),
        str(motivo_nao_realizado or "").strip(),
        str(observacoes or "").strip(),
    )
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            """
            INSERT INTO desenvolvimentos_poco (
                sondagem_id, realizado, data, metodo, duracao_min,
                profundidade_equipamento_m, na_antes_m, na_depois_m,
                vazao_l_min, volume_retirado_l, turbidez_inicial_ntu,
                turbidez_final_ntu, ph_final, condutividade_final_us_cm,
                temperatura_final_c, responsavel, motivo_nao_realizado,
                observacoes, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sondagem_id) DO UPDATE SET
                realizado = excluded.realizado,
                data = excluded.data,
                metodo = excluded.metodo,
                duracao_min = excluded.duracao_min,
                profundidade_equipamento_m = excluded.profundidade_equipamento_m,
                na_antes_m = excluded.na_antes_m,
                na_depois_m = excluded.na_depois_m,
                vazao_l_min = excluded.vazao_l_min,
                volume_retirado_l = excluded.volume_retirado_l,
                turbidez_inicial_ntu = excluded.turbidez_inicial_ntu,
                turbidez_final_ntu = excluded.turbidez_final_ntu,
                ph_final = excluded.ph_final,
                condutividade_final_us_cm = excluded.condutividade_final_us_cm,
                temperatura_final_c = excluded.temperatura_final_c,
                responsavel = excluded.responsavel,
                motivo_nao_realizado = excluded.motivo_nao_realizado,
                observacoes = excluded.observacoes,
                atualizado_em = CURRENT_TIMESTAMP
            """,
            parametros,
        )
        linha = conexao.execute(
            "SELECT id FROM desenvolvimentos_poco WHERE sondagem_id = ?",
            (int(sondagem_id),),
        ).fetchone()
        return int(linha["id"])


def obter_desenvolvimento(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtem o desenvolvimento cadastrado para uma sondagem."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT * FROM desenvolvimentos_poco WHERE sondagem_id = ?",
            (int(sondagem_id),),
        ).fetchone()
    if not linha:
        return None
    dados = dict(linha)
    dados["realizado"] = bool(dados.get("realizado"))
    return dados


def adicionar_leitura_desenvolvimento(
    sondagem_id: int,
    tempo_min: float,
    nivel_agua_m: float | None = None,
    vazao_l_min: float | None = None,
    turbidez_ntu: float | None = None,
    ph: float | None = None,
    condutividade_us_cm: float | None = None,
    temperatura_c: float | None = None,
    observacoes: str = "",
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona uma leitura cronologica do desenvolvimento."""
    desenvolvimento = obter_desenvolvimento(sondagem_id, caminho_banco)
    if not desenvolvimento:
        raise ErroValidacao("Salve primeiro os dados gerais do desenvolvimento.")
    if not desenvolvimento["realizado"]:
        raise ErroValidacao("Nao e possivel adicionar leituras a um desenvolvimento nao realizado.")
    tempo = _validar_numero_finito(tempo_min, "tempo")
    if tempo < 0:
        raise ErroValidacao("O tempo nao pode ser negativo.")

    def numero(valor: Any, nome: str, minimo: float | None = 0.0) -> float | None:
        if valor is None or pd.isna(valor):
            return None
        resultado = _validar_numero_finito(valor, nome)
        if minimo is not None and resultado < minimo:
            raise ErroValidacao(f"O campo '{nome}' nao pode ser menor que {minimo}.")
        return resultado

    with conectar(caminho_banco) as conexao:
        cursor = conexao.execute(
            """
            INSERT INTO leituras_desenvolvimento (
                desenvolvimento_id, tempo_min, nivel_agua_m, vazao_l_min,
                turbidez_ntu, ph, condutividade_us_cm, temperatura_c,
                observacoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(desenvolvimento["id"]), tempo,
                numero(nivel_agua_m, "nivel d'agua"),
                numero(vazao_l_min, "vazao"),
                numero(turbidez_ntu, "turbidez"),
                numero(ph, "pH", None),
                numero(condutividade_us_cm, "condutividade"),
                numero(temperatura_c, "temperatura", None),
                str(observacoes or "").strip(),
            ),
        )
        return int(cursor.lastrowid)


def listar_leituras_desenvolvimento(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista as leituras do desenvolvimento em ordem de tempo."""
    colunas = [
        "id", "desenvolvimento_id", "tempo_min", "nivel_agua_m",
        "vazao_l_min", "turbidez_ntu", "ph", "condutividade_us_cm",
        "temperatura_c", "observacoes",
    ]
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT l.id, l.desenvolvimento_id, l.tempo_min, l.nivel_agua_m,
                   l.vazao_l_min, l.turbidez_ntu, l.ph,
                   l.condutividade_us_cm, l.temperatura_c, l.observacoes
            FROM leituras_desenvolvimento l
            INNER JOIN desenvolvimentos_poco d ON d.id = l.desenvolvimento_id
            WHERE d.sondagem_id = ?
            ORDER BY l.tempo_min, l.id
            """,
            (int(sondagem_id),),
        ).fetchall()
    return _dataframe_de_linhas(linhas, colunas)


def remover_leitura_desenvolvimento(
    leitura_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove uma leitura do desenvolvimento."""
    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM leituras_desenvolvimento WHERE id = ?",
            (int(leitura_id),),
        )


def obter_dados_completos_sondagem(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any]:
    """Agrupa todos os dados usados nos relatorios Word e Excel."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem nao encontrada.")
    projeto = obter_projeto(int(sondagem["projeto_id"]), caminho_banco) or {}
    camadas = (
        listar_camadas(sondagem_id, caminho_banco)
        if sondagem["status"] == STATUS_CONCLUIDA
        else listar_rascunho_camadas(sondagem_id, caminho_banco)
    )
    return {
        "projeto": projeto,
        "sondagem": sondagem,
        "camadas": camadas,
        "coletas": listar_coletas(sondagem_id, caminho_banco),
        "voc": listar_voc(sondagem_id, caminho_banco),
        "leituras_na": listar_leituras_nivel_agua(sondagem_id, caminho_banco),
        "poco": obter_poco_monitoramento(sondagem_id, caminho_banco),
        "intervalos_construtivos": listar_intervalos_construtivos(sondagem_id, caminho_banco),
        "desenvolvimento": obter_desenvolvimento(sondagem_id, caminho_banco),
        "leituras_desenvolvimento": listar_leituras_desenvolvimento(sondagem_id, caminho_banco),
        "fotos": listar_fotos_sondagem(sondagem_id, caminho_banco, incluir_conteudo=True),
    }


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
            "pocos": int(
                conexao.execute("SELECT COUNT(*) FROM pocos_monitoramento").fetchone()[0]
            ),
            "fotos": int(
                conexao.execute("SELECT COUNT(*) FROM fotos_sondagem").fetchone()[0]
            ),
            "desenvolvimentos": int(
                conexao.execute(
                    "SELECT COUNT(*) FROM desenvolvimentos_poco WHERE realizado = 1"
                ).fetchone()[0]
            ),
        }
