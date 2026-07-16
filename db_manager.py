from __future__ import annotations

import math
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

CAMINHO_BANCO_PADRAO = Path(__file__).with_name("hidrogeologia.db")
TOLERANCIA_PROFUNDIDADE = 1e-6

CLASSIFICACOES_VALIDAS = [
    "Argila",
    "Areia Fina",
    "Areia Grossa",
    "Cascalho",
    "Rocha Sã",
    "Rocha Alterada",
    "Silte",
]

TIPOS_AQUIFERO_VALIDOS = [
    "Livre",
    "Confinado",
    "Semiconfinado",
    "Aquitarde",
    "Aquífugo",
]

COLUNAS_CSV_OBRIGATORIAS = [
    "projeto",
    "sondagem_nome",
    "latitude",
    "longitude",
    "altitude",
    "nivel_agua",
    "profundidade_inicial",
    "profundidade_final",
    "descricao",
    "classificacao",
    "tipo_aquifero",
]


class ErroValidacao(ValueError):
    """Erro de regra de negócio ou de consistência dos dados."""


def conectar(caminho_banco: str | Path = CAMINHO_BANCO_PADRAO) -> sqlite3.Connection:
    """Abre uma conexão SQLite com integridade referencial habilitada."""
    conexao = sqlite3.connect(str(caminho_banco), timeout=30)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA journal_mode = WAL")
    return conexao


def inicializar_banco(caminho_banco: str | Path = CAMINHO_BANCO_PADRAO) -> None:
    """Cria as tabelas e os índices necessários para o aplicativo."""
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
                altitude REAL NOT NULL,
                profundidade_total REAL NOT NULL CHECK (profundidade_total > 0),
                nivel_agua_estatico REAL CHECK (
                    nivel_agua_estatico IS NULL OR nivel_agua_estatico >= 0
                ),
                data TEXT NOT NULL,
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
                classificacao TEXT NOT NULL CHECK (
                    classificacao IN (
                        'Argila', 'Areia Fina', 'Areia Grossa', 'Cascalho',
                        'Rocha Sã', 'Rocha Alterada', 'Silte'
                    )
                ),
                tipo_aquifero TEXT NOT NULL CHECK (
                    tipo_aquifero IN (
                        'Livre', 'Confinado', 'Semiconfinado', 'Aquitarde', 'Aquífugo'
                    )
                ),
                cota_topo REAL NOT NULL,
                cota_base REAL NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_coletas_sondagem
                ON coletas_amostras(sondagem_id, profundidade_coleta);
            CREATE INDEX IF NOT EXISTS idx_voc_sondagem
                ON voc_medicoes(sondagem_id, profundidade);
            """
        )


def _dataframe_de_linhas(linhas: Iterable[sqlite3.Row], colunas: list[str]) -> pd.DataFrame:
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
        raise ErroValidacao("O nome do projeto é obrigatório.")

    try:
        with conectar(caminho_banco) as conexao:
            cursor = conexao.execute(
                "INSERT INTO projetos (nome, descricao) VALUES (?, ?)",
                (nome_limpo, str(descricao or "").strip()),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as erro:
        raise ErroValidacao(f"Não foi possível criar o projeto: {erro}") from erro


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
    """Obtém um projeto pelo identificador."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            "SELECT id, nome, descricao FROM projetos WHERE id = ?",
            (int(projeto_id),),
        ).fetchone()
    return dict(linha) if linha else None


def _validar_dados_sondagem(
    nome_furo: str,
    latitude: float,
    longitude: float,
    altitude: float,
    profundidade_total: float,
    nivel_agua_estatico: float | None,
) -> tuple[str, float, float, float, float, float | None]:
    nome_limpo = str(nome_furo).strip()
    if not nome_limpo:
        raise ErroValidacao("O nome da sondagem é obrigatório.")

    try:
        latitude_num = float(latitude)
        longitude_num = float(longitude)
        altitude_num = float(altitude)
        profundidade_num = float(profundidade_total)
        nivel_agua_num = (
            None if nivel_agua_estatico is None else float(nivel_agua_estatico)
        )
    except (TypeError, ValueError) as erro:
        raise ErroValidacao("Os campos numéricos da sondagem são inválidos.") from erro

    valores_obrigatorios = [
        latitude_num,
        longitude_num,
        altitude_num,
        profundidade_num,
    ]
    if any(not math.isfinite(valor) for valor in valores_obrigatorios):
        raise ErroValidacao("Os campos numéricos devem conter valores finitos.")
    if not -90 <= latitude_num <= 90:
        raise ErroValidacao("A latitude deve estar entre -90 e 90 graus decimais.")
    if not -180 <= longitude_num <= 180:
        raise ErroValidacao("A longitude deve estar entre -180 e 180 graus decimais.")
    if profundidade_num <= 0:
        raise ErroValidacao("A profundidade total deve ser maior que zero.")
    if nivel_agua_num is not None:
        if not math.isfinite(nivel_agua_num) or nivel_agua_num < 0:
            raise ErroValidacao("O nível d'água deve ser nulo ou não negativo.")
        if nivel_agua_num > profundidade_num + TOLERANCIA_PROFUNDIDADE:
            raise ErroValidacao(
                "O nível d'água não pode ser maior que a profundidade total."
            )

    return (
        nome_limpo,
        latitude_num,
        longitude_num,
        altitude_num,
        profundidade_num,
        nivel_agua_num,
    )


def criar_sondagem(
    projeto_id: int,
    nome_furo: str,
    latitude: float,
    longitude: float,
    altitude: float,
    profundidade_total: float,
    nivel_agua_estatico: float | None,
    data_sondagem: str | date,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Insere uma sondagem vinculada a um projeto."""
    (
        nome_limpo,
        latitude_num,
        longitude_num,
        altitude_num,
        profundidade_num,
        nivel_agua_num,
    ) = _validar_dados_sondagem(
        nome_furo,
        latitude,
        longitude,
        altitude,
        profundidade_total,
        nivel_agua_estatico,
    )

    data_texto = str(data_sondagem)
    if not data_texto.strip():
        raise ErroValidacao("A data da sondagem é obrigatória.")

    try:
        with conectar(caminho_banco) as conexao:
            cursor = conexao.execute(
                """
                INSERT INTO sondagens (
                    projeto_id, nome_furo, latitude, longitude, altitude,
                    profundidade_total, nivel_agua_estatico, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(projeto_id),
                    nome_limpo,
                    latitude_num,
                    longitude_num,
                    altitude_num,
                    profundidade_num,
                    nivel_agua_num,
                    data_texto,
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as erro:
        raise ErroValidacao(f"Não foi possível criar a sondagem: {erro}") from erro


def listar_sondagens(
    projeto_id: int | None = None,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista sondagens e inclui o nome do projeto associado."""
    consulta = """
        SELECT
            s.id,
            s.projeto_id,
            p.nome AS projeto_nome,
            s.nome_furo,
            s.latitude,
            s.longitude,
            s.altitude,
            s.profundidade_total,
            s.nivel_agua_estatico,
            s.data,
            CASE
                WHEN s.nivel_agua_estatico IS NULL THEN NULL
                ELSE s.altitude - s.nivel_agua_estatico
            END AS cota_nivel_agua
        FROM sondagens s
        INNER JOIN projetos p ON p.id = s.projeto_id
    """
    parametros: tuple[Any, ...] = ()
    if projeto_id is not None:
        consulta += " WHERE s.projeto_id = ?"
        parametros = (int(projeto_id),)
    consulta += " ORDER BY p.nome, s.nome_furo"

    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(consulta, parametros).fetchall()

    colunas = [
        "id",
        "projeto_id",
        "projeto_nome",
        "nome_furo",
        "latitude",
        "longitude",
        "altitude",
        "profundidade_total",
        "nivel_agua_estatico",
        "data",
        "cota_nivel_agua",
    ]
    return _dataframe_de_linhas(linhas, colunas)


def obter_sondagem(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, Any] | None:
    """Obtém todos os dados de uma sondagem pelo identificador."""
    with conectar(caminho_banco) as conexao:
        linha = conexao.execute(
            """
            SELECT
                s.id,
                s.projeto_id,
                p.nome AS projeto_nome,
                s.nome_furo,
                s.latitude,
                s.longitude,
                s.altitude,
                s.profundidade_total,
                s.nivel_agua_estatico,
                s.data,
                CASE
                    WHEN s.nivel_agua_estatico IS NULL THEN NULL
                    ELSE s.altitude - s.nivel_agua_estatico
                END AS cota_nivel_agua
            FROM sondagens s
            INNER JOIN projetos p ON p.id = s.projeto_id
            WHERE s.id = ?
            """,
            (int(sondagem_id),),
        ).fetchone()
    return dict(linha) if linha else None


def _normalizar_e_validar_camadas(
    camadas: list[dict[str, Any]],
    profundidade_total: float,
    altitude: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normaliza um perfil e verifica cobertura integral, continuidade e domínio."""
    erros: list[str] = []
    normalizadas: list[dict[str, Any]] = []

    if not camadas:
        return [], ["O perfil deve possuir pelo menos uma camada."]

    for indice, camada in enumerate(camadas, start=1):
        try:
            profundidade_inicial = float(camada["profundidade_inicial"])
            profundidade_final = float(camada["profundidade_final"])
        except (KeyError, TypeError, ValueError) as erro:
            erros.append(f"Camada {indice}: profundidades inválidas ({erro}).")
            continue

        descricao = str(
            camada.get("descricao_tatil_visual", camada.get("descricao", "")) or ""
        ).strip()
        classificacao = str(camada.get("classificacao", "") or "").strip()
        tipo_aquifero = str(camada.get("tipo_aquifero", "") or "").strip()

        if not math.isfinite(profundidade_inicial) or not math.isfinite(
            profundidade_final
        ):
            erros.append(f"Camada {indice}: as profundidades devem ser finitas.")
            continue
        if profundidade_inicial < 0:
            erros.append(f"Camada {indice}: profundidade inicial negativa.")
        if profundidade_final <= profundidade_inicial:
            erros.append(
                f"Camada {indice}: a profundidade final deve ser maior que a inicial."
            )
        if profundidade_final > profundidade_total + TOLERANCIA_PROFUNDIDADE:
            erros.append(
                f"Camada {indice}: profundidade final excede a profundidade total."
            )
        if not descricao:
            erros.append(f"Camada {indice}: a descrição tátil-visual é obrigatória.")
        if classificacao not in CLASSIFICACOES_VALIDAS:
            erros.append(
                f"Camada {indice}: classificação '{classificacao}' não permitida."
            )
        if tipo_aquifero not in TIPOS_AQUIFERO_VALIDOS:
            erros.append(
                f"Camada {indice}: tipo de aquífero '{tipo_aquifero}' não permitido."
            )

        normalizadas.append(
            {
                "profundidade_inicial": round(profundidade_inicial, 6),
                "profundidade_final": round(profundidade_final, 6),
                "descricao_tatil_visual": descricao,
                "classificacao": classificacao,
                "tipo_aquifero": tipo_aquifero,
                "cota_topo": round(float(altitude) - profundidade_inicial, 6),
                "cota_base": round(float(altitude) - profundidade_final, 6),
            }
        )

    normalizadas.sort(key=lambda item: item["profundidade_inicial"])

    if erros:
        return normalizadas, erros

    primeira = normalizadas[0]
    if abs(primeira["profundidade_inicial"]) > TOLERANCIA_PROFUNDIDADE:
        erros.append("O perfil deve começar exatamente na profundidade 0,00 m.")

    for indice in range(1, len(normalizadas)):
        anterior = normalizadas[indice - 1]
        atual = normalizadas[indice]
        diferenca = atual["profundidade_inicial"] - anterior["profundidade_final"]
        if diferenca < -TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "Há sobreposição entre as camadas "
                f"{indice} e {indice + 1}: {abs(diferenca):.6f} m."
            )
        elif diferenca > TOLERANCIA_PROFUNDIDADE:
            erros.append(
                "Há intervalo sem descrição entre as camadas "
                f"{indice} e {indice + 1}: {diferenca:.6f} m."
            )

    soma_espessuras = sum(
        camada["profundidade_final"] - camada["profundidade_inicial"]
        for camada in normalizadas
    )
    ultima_profundidade = normalizadas[-1]["profundidade_final"]

    if abs(soma_espessuras - profundidade_total) > TOLERANCIA_PROFUNDIDADE:
        erros.append(
            "A soma das espessuras deve ser igual à profundidade total: "
            f"soma={soma_espessuras:.6f} m; total={profundidade_total:.6f} m."
        )
    if abs(ultima_profundidade - profundidade_total) > TOLERANCIA_PROFUNDIDADE:
        erros.append(
            "A última camada deve terminar exatamente na profundidade total: "
            f"final={ultima_profundidade:.6f} m; total={profundidade_total:.6f} m."
        )

    return normalizadas, erros


def validar_perfil_litologico(
    sondagem_id: int,
    camadas: list[dict[str, Any]],
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Valida um perfil completo sem alterar o banco."""
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        return False, ["Sondagem não encontrada."], []

    normalizadas, erros = _normalizar_e_validar_camadas(
        camadas,
        float(sondagem["profundidade_total"]),
        float(sondagem["altitude"]),
    )
    return not erros, erros, normalizadas


def salvar_perfil_litologico(
    sondagem_id: int,
    camadas: list[dict[str, Any]],
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Substitui o perfil inteiro em uma transação após validação integral."""
    valido, erros, normalizadas = validar_perfil_litologico(
        sondagem_id, camadas, caminho_banco
    )
    if not valido:
        raise ErroValidacao("\n".join(erros))

    with conectar(caminho_banco) as conexao:
        conexao.execute(
            "DELETE FROM camadas_litologicas WHERE sondagem_id = ?",
            (int(sondagem_id),),
        )
        conexao.executemany(
            """
            INSERT INTO camadas_litologicas (
                sondagem_id,
                profundidade_inicial,
                profundidade_final,
                descricao_tatil_visual,
                classificacao,
                tipo_aquifero,
                cota_topo,
                cota_base
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


def adicionar_camada(
    sondagem_id: int,
    profundidade_inicial: float,
    profundidade_final: float,
    descricao_tatil_visual: str,
    classificacao: str,
    tipo_aquifero: str,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Adiciona uma camada somente quando o conjunto resultante forma um perfil completo."""
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


def listar_camadas(
    sondagem_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Lista as camadas de uma sondagem em ordem de profundidade."""
    with conectar(caminho_banco) as conexao:
        linhas = conexao.execute(
            """
            SELECT
                id,
                sondagem_id,
                profundidade_inicial,
                profundidade_final,
                descricao_tatil_visual,
                classificacao,
                tipo_aquifero,
                cota_topo,
                cota_base,
                profundidade_final - profundidade_inicial AS espessura
            FROM camadas_litologicas
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
    return _dataframe_de_linhas(linhas, colunas)


def _validar_profundidade_pontual(
    sondagem_id: int,
    profundidade: float,
    caminho_banco: str | Path,
) -> float:
    sondagem = obter_sondagem(sondagem_id, caminho_banco)
    if not sondagem:
        raise ErroValidacao("Sondagem não encontrada.")

    try:
        profundidade_num = float(profundidade)
    except (TypeError, ValueError) as erro:
        raise ErroValidacao("A profundidade informada é inválida.") from erro

    if not math.isfinite(profundidade_num) or profundidade_num < 0:
        raise ErroValidacao("A profundidade deve ser finita e não negativa.")
    if profundidade_num > float(sondagem["profundidade_total"]) + TOLERANCIA_PROFUNDIDADE:
        raise ErroValidacao("A profundidade excede a profundidade total da sondagem.")
    return profundidade_num


def adicionar_coleta(
    sondagem_id: int,
    profundidade_coleta: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona um ponto de coleta de amostra."""
    profundidade_num = _validar_profundidade_pontual(
        sondagem_id, profundidade_coleta, caminho_banco
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
        linhas, ["id", "sondagem_id", "profundidade_coleta"]
    )


def remover_coleta(
    coleta_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove um ponto de coleta."""
    with conectar(caminho_banco) as conexao:
        conexao.execute("DELETE FROM coletas_amostras WHERE id = ?", (int(coleta_id),))


def adicionar_voc(
    sondagem_id: int,
    profundidade: float,
    concentracao: float,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> int:
    """Adiciona uma medição de VOC em determinada profundidade."""
    profundidade_num = _validar_profundidade_pontual(
        sondagem_id, profundidade, caminho_banco
    )
    try:
        concentracao_num = float(concentracao)
    except (TypeError, ValueError) as erro:
        raise ErroValidacao("A concentração de VOC é inválida.") from erro

    if not math.isfinite(concentracao_num) or concentracao_num < 0:
        raise ErroValidacao("A concentração de VOC deve ser finita e não negativa.")

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
    """Lista medições de VOC em ordem de profundidade."""
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
        linhas, ["id", "sondagem_id", "profundidade", "concentracao"]
    )


def remover_voc(
    medicao_id: int,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> None:
    """Remove uma medição de VOC."""
    with conectar(caminho_banco) as conexao:
        conexao.execute("DELETE FROM voc_medicoes WHERE id = ?", (int(medicao_id),))


def _valor_unico(grupo: pd.DataFrame, coluna: str) -> Any:
    valores = grupo[coluna].dropna().tolist()
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


def importar_dataframe(
    dataframe: pd.DataFrame,
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> pd.DataFrame:
    """Importa projetos, sondagens e perfis completos a partir de um DataFrame."""
    inicializar_banco(caminho_banco)
    dados = dataframe.copy()
    dados.columns = [str(coluna).strip().lower() for coluna in dados.columns]

    faltantes = [
        coluna for coluna in COLUNAS_CSV_OBRIGATORIAS if coluna not in dados.columns
    ]
    if faltantes:
        raise ErroValidacao(
            "Colunas obrigatórias ausentes: " + ", ".join(faltantes)
        )

    dados = dados[COLUNAS_CSV_OBRIGATORIAS].copy()
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
                raise ErroValidacao("O nome do projeto está vazio.")
            if not sondagem_texto:
                raise ErroValidacao("O nome da sondagem está vazio.")

            latitude = float(_valor_unico(grupo, "latitude"))
            longitude = float(_valor_unico(grupo, "longitude"))
            altitude = float(_valor_unico(grupo, "altitude"))
            nivel_agua_bruto = _valor_unico(grupo, "nivel_agua")
            nivel_agua = (
                None
                if nivel_agua_bruto is None or pd.isna(nivel_agua_bruto)
                else float(nivel_agua_bruto)
            )

            profundidades_finais = pd.to_numeric(
                grupo["profundidade_final"], errors="raise"
            )
            profundidade_total = float(profundidades_finais.max())

            (
                nome_validado,
                latitude,
                longitude,
                altitude,
                profundidade_total,
                nivel_agua,
            ) = _validar_dados_sondagem(
                sondagem_texto,
                latitude,
                longitude,
                altitude,
                profundidade_total,
                nivel_agua,
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
            )
            if erros:
                raise ErroValidacao(" | ".join(erros))

            with conectar(caminho_banco) as conexao:
                projeto = conexao.execute(
                    "SELECT id FROM projetos WHERE nome = ?",
                    (projeto_texto,),
                ).fetchone()
                if projeto:
                    projeto_id = int(projeto["id"])
                else:
                    cursor_projeto = conexao.execute(
                        "INSERT INTO projetos (nome, descricao) VALUES (?, ?)",
                        (projeto_texto, "Projeto criado por importação CSV."),
                    )
                    projeto_id = int(cursor_projeto.lastrowid)

                sondagem = conexao.execute(
                    """
                    SELECT id FROM sondagens
                    WHERE projeto_id = ? AND nome_furo = ?
                    """,
                    (projeto_id, nome_validado),
                ).fetchone()

                if sondagem:
                    sondagem_id = int(sondagem["id"])
                    profundidade_maxima_pontual = conexao.execute(
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
                        (sondagem_id, sondagem_id),
                    ).fetchone()["profundidade_maxima"]
                    if (
                        profundidade_maxima_pontual is not None
                        and float(profundidade_maxima_pontual)
                        > profundidade_total + TOLERANCIA_PROFUNDIDADE
                    ):
                        raise ErroValidacao(
                            "Há coleta ou VOC existente abaixo da nova profundidade total."
                        )

                    conexao.execute(
                        """
                        UPDATE sondagens
                        SET latitude = ?, longitude = ?, altitude = ?,
                            profundidade_total = ?, nivel_agua_estatico = ?
                        WHERE id = ?
                        """,
                        (
                            latitude,
                            longitude,
                            altitude,
                            profundidade_total,
                            nivel_agua,
                            sondagem_id,
                        ),
                    )
                else:
                    cursor_sondagem = conexao.execute(
                        """
                        INSERT INTO sondagens (
                            projeto_id, nome_furo, latitude, longitude, altitude,
                            profundidade_total, nivel_agua_estatico, data
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            projeto_id,
                            nome_validado,
                            latitude,
                            longitude,
                            altitude,
                            profundidade_total,
                            nivel_agua,
                            date.today().isoformat(),
                        ),
                    )
                    sondagem_id = int(cursor_sondagem.lastrowid)

                conexao.execute(
                    "DELETE FROM camadas_litologicas WHERE sondagem_id = ?",
                    (sondagem_id,),
                )
                conexao.executemany(
                    """
                    INSERT INTO camadas_litologicas (
                        sondagem_id,
                        profundidade_inicial,
                        profundidade_final,
                        descricao_tatil_visual,
                        classificacao,
                        tipo_aquifero,
                        cota_topo,
                        cota_base
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
                        f"profundidade total de {profundidade_total:.2f} m."
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
    """Valida a integridade e a estrutura mínima de um arquivo SQLite."""
    caminho = Path(caminho_banco)
    if not caminho.exists() or not caminho.is_file():
        raise ErroValidacao("O arquivo de banco de dados não foi encontrado.")
    if caminho.stat().st_size == 0:
        raise ErroValidacao("O arquivo de banco de dados está vazio.")

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
                    "O arquivo SQLite falhou na verificação de integridade: "
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
                    "O arquivo não possui as tabelas obrigatórias: "
                    + ", ".join(faltantes)
                )
        finally:
            conexao.close()
    except ErroValidacao:
        raise
    except sqlite3.DatabaseError as erro:
        raise ErroValidacao(f"O arquivo informado não é um SQLite válido: {erro}") from erro


def exportar_banco_bytes(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> bytes:
    """Gera uma cópia consistente do banco, incluindo alterações presentes no WAL."""
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
        raise ErroValidacao("O arquivo de backup está vazio.")
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

        # Remove arquivos auxiliares do banco anterior antes da troca atômica.
        Path(f"{destino}-wal").unlink(missing_ok=True)
        Path(f"{destino}-shm").unlink(missing_ok=True)
        os.replace(temporario, destino)
        inicializar_banco(destino)
    finally:
        temporario.unlink(missing_ok=True)


def obter_resumo_banco(
    caminho_banco: str | Path = CAMINHO_BANCO_PADRAO,
) -> dict[str, int]:
    """Retorna contagens simples para apresentação no painel lateral."""
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
        }

