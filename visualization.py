from __future__ import annotations

import html
import math
from typing import Any

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.interpolate import interp1d

PADROES_ABNT = {
    "Argila": {"cor": "#8B4513", "padrao": "/"},
    "Areia Fina": {"cor": "#F4D03F", "padrao": "."},
    "Areia Grossa": {"cor": "#F5B041", "padrao": "x"},
    "Cascalho": {"cor": "#A9A9A9", "padrao": "+"},
    "Rocha Sã": {"cor": "#2C3E50", "padrao": "|"},
    "Rocha Alterada": {"cor": "#7F8C8D", "padrao": "-"},
    "Silte": {"cor": "#BFC9CA", "padrao": "o"},
}

PADROES_NATIVOS_PLOTLY = {"", "/", "\\", "x", "-", "|", "+", "."}

CORES_MARCADORES = [
    "blue",
    "green",
    "purple",
    "orange",
    "darkred",
    "cadetblue",
    "darkgreen",
    "darkpurple",
    "pink",
    "gray",
]


def _padrao_nativo_plotly(padrao: str) -> str:
    """Retorna apenas padrões aceitos nativamente pelo Plotly."""
    return padrao if padrao in PADROES_NATIVOS_PLOTLY else ""


def _adicionar_circulos_silte_perfil(
    figura: go.Figure,
    profundidade_inicial: float,
    profundidade_final: float,
) -> None:
    """Simula o padrão circular de silte, que não existe no enumerador do Plotly."""
    espessura = profundidade_final - profundidade_inicial
    espacamento_vertical = max(espessura / 6, 0.25)
    niveis = np.arange(
        profundidade_inicial + espacamento_vertical / 2,
        profundidade_final,
        espacamento_vertical,
    )
    pontos_x: list[float] = []
    pontos_y: list[float] = []

    for indice, nivel in enumerate(niveis):
        deslocamento = 0.11 if indice % 2 else 0.0
        for posicao_x in (0.22 + deslocamento, 0.50 + deslocamento, 0.78 + deslocamento):
            if posicao_x < 0.98:
                pontos_x.append(posicao_x)
                pontos_y.append(float(nivel))

    figura.add_trace(
        go.Scatter(
            x=pontos_x,
            y=pontos_y,
            mode="markers",
            marker=dict(
                symbol="circle-open",
                size=6,
                color="#111111",
                line=dict(width=1),
            ),
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )


def criar_mapa_sondagens(sondagens: pd.DataFrame) -> folium.Map:
    """Cria mapa Folium com marcadores das sondagens em graus decimais."""
    if sondagens.empty:
        return folium.Map(
            location=[-14.2350, -51.9253],
            zoom_start=4,
            tiles="OpenStreetMap",
            control_scale=True,
        )

    centro_latitude = float(sondagens["latitude"].mean())
    centro_longitude = float(sondagens["longitude"].mean())
    mapa = folium.Map(
        location=[centro_latitude, centro_longitude],
        zoom_start=7 if len(sondagens) > 1 else 12,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    projetos = list(dict.fromkeys(sondagens["projeto_id"].tolist()))
    cores_por_projeto = {
        projeto_id: CORES_MARCADORES[indice % len(CORES_MARCADORES)]
        for indice, projeto_id in enumerate(projetos)
    }

    for _, sondagem in sondagens.iterrows():
        nivel_agua = sondagem.get("nivel_agua_estatico")
        texto_nivel = (
            "Não informado"
            if pd.isna(nivel_agua)
            else f"{float(nivel_agua):.2f} m"
        )
        conteudo = (
            f"<b>{html.escape(str(sondagem['nome_furo']))}</b><br>"
            f"Projeto: {html.escape(str(sondagem['projeto_nome']))}<br>"
            f"Profundidade: {float(sondagem['profundidade_total']):.2f} m<br>"
            f"NA: {texto_nivel}<br>"
            f"Altitude: {float(sondagem['altitude']):.2f} m"
        )
        folium.Marker(
            location=[float(sondagem["latitude"]), float(sondagem["longitude"])],
            tooltip=f"{sondagem['nome_furo']} — {sondagem['projeto_nome']}",
            popup=folium.Popup(conteudo, max_width=320),
            icon=folium.Icon(
                color=cores_por_projeto[sondagem["projeto_id"]],
                icon="tint",
                prefix="fa",
            ),
        ).add_to(mapa)

    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def criar_perfil_litologico(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    coletas: pd.DataFrame,
    voc: pd.DataFrame,
) -> go.Figure:
    """Monta o perfil litológico individual e a curva de VOC em subplots."""
    figura = make_subplots(
        rows=1,
        cols=2,
        shared_yaxes=True,
        horizontal_spacing=0.09,
        column_widths=[0.42, 0.58],
        subplot_titles=("Coluna litológica", "Concentração de VOC"),
    )

    classificacoes_na_legenda: set[str] = set()
    for _, camada in camadas.sort_values("profundidade_inicial").iterrows():
        profundidade_inicial = float(camada["profundidade_inicial"])
        profundidade_final = float(camada["profundidade_final"])
        espessura = profundidade_final - profundidade_inicial
        centro = (profundidade_inicial + profundidade_final) / 2
        classificacao = str(camada["classificacao"])
        estilo = PADROES_ABNT.get(
            classificacao, {"cor": "#D5D8DC", "padrao": ""}
        )
        mostrar_legenda = classificacao not in classificacoes_na_legenda
        classificacoes_na_legenda.add(classificacao)

        figura.add_trace(
            go.Bar(
                x=[1.0],
                y=[centro],
                width=[espessura],
                base=0,
                orientation="h",
                name=classificacao,
                legendgroup=classificacao,
                showlegend=mostrar_legenda,
                marker_color=estilo["cor"],
                marker_line_color="#1F1F1F",
                marker_line_width=1,
                marker_pattern_shape=_padrao_nativo_plotly(estilo["padrao"]),
                marker_pattern_fillmode="overlay",
                marker_pattern_fgcolor="#111111",
                marker_pattern_solidity=0.18,
                customdata=[
                    [
                        profundidade_inicial,
                        profundidade_final,
                        camada["descricao_tatil_visual"],
                        camada["tipo_aquifero"],
                        camada["cota_topo"],
                        camada["cota_base"],
                    ]
                ],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Topo: %{customdata[0]:.2f} m<br>"
                    "Base: %{customdata[1]:.2f} m<br>"
                    "Descrição: %{customdata[2]}<br>"
                    "Unidade hidrogeológica: %{customdata[3]}<br>"
                    "Cota do topo: %{customdata[4]:.2f} m<br>"
                    "Cota da base: %{customdata[5]:.2f} m"
                    "<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

        if estilo["padrao"] == "o":
            _adicionar_circulos_silte_perfil(
                figura,
                profundidade_inicial,
                profundidade_final,
            )

    if not coletas.empty:
        figura.add_trace(
            go.Scatter(
                x=[1.08] * len(coletas),
                y=coletas["profundidade_coleta"].astype(float),
                mode="markers",
                name="Coleta de amostra",
                marker=dict(
                    symbol="star",
                    size=13,
                    color="#8E44AD",
                    line=dict(color="#FFFFFF", width=1),
                ),
                hovertemplate="Coleta em %{y:.2f} m<extra></extra>",
            ),
            row=1,
            col=1,
        )

    if not voc.empty:
        voc_ordenado = voc.sort_values("profundidade")
        figura.add_trace(
            go.Scatter(
                x=voc_ordenado["concentracao"].astype(float),
                y=voc_ordenado["profundidade"].astype(float),
                mode="lines+markers",
                name="VOC",
                line=dict(color="#2471A3", width=2),
                marker=dict(size=8, symbol="circle"),
                hovertemplate=(
                    "Profundidade: %{y:.2f} m<br>"
                    "Concentração: %{x:.4g}<extra></extra>"
                ),
            ),
            row=1,
            col=2,
        )
    else:
        figura.add_annotation(
            text="Sem medições de VOC",
            x=0.5,
            y=0.5,
            xref="x2 domain",
            yref="y2 domain",
            showarrow=False,
            font=dict(color="#6C757D"),
        )

    nivel_agua = sondagem.get("nivel_agua_estatico")
    if nivel_agua is not None and not pd.isna(nivel_agua):
        nivel_agua = float(nivel_agua)
        for coluna in (1, 2):
            figura.add_hline(
                y=nivel_agua,
                line_dash="dash",
                line_color="#C0392B",
                line_width=2,
                annotation_text=(
                    f"NA = {nivel_agua:.2f} m" if coluna == 1 else None
                ),
                annotation_position="top left",
                row=1,
                col=coluna,
            )
        figura.add_trace(
            go.Scatter(
                x=[1.08],
                y=[nivel_agua],
                mode="markers",
                name="Nível d'água",
                marker=dict(
                    symbol="triangle-down",
                    size=14,
                    color="#C0392B",
                    line=dict(color="#FFFFFF", width=1),
                ),
                hovertemplate=f"NA: {nivel_agua:.2f} m<extra></extra>",
            ),
            row=1,
            col=1,
        )

    profundidade_total = float(sondagem["profundidade_total"])
    figura.update_xaxes(
        range=[0, 1.16],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        title_text="Litologia",
        row=1,
        col=1,
    )
    figura.update_xaxes(
        title_text="Concentração (mg/L ou ppm)",
        rangemode="tozero",
        showgrid=True,
        gridcolor="#E5E7E9",
        row=1,
        col=2,
    )
    figura.update_yaxes(
        title_text="Profundidade (m)",
        range=[profundidade_total, 0],
        autorange=False,
        showgrid=True,
        gridcolor="#E5E7E9",
        row=1,
        col=1,
    )
    figura.update_yaxes(
        range=[profundidade_total, 0],
        autorange=False,
        showgrid=True,
        gridcolor="#E5E7E9",
        row=1,
        col=2,
    )
    figura.update_layout(
        title=(
            f"Perfil litológico — {sondagem['nome_furo']} | "
            f"Projeto: {sondagem['projeto_nome']}"
        ),
        height=max(680, int(profundidade_total * 18)),
        barmode="overlay",
        bargap=0,
        template="plotly_white",
        hovermode="closest",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.08,
            xanchor="left",
            x=0,
        ),
        margin=dict(l=70, r=40, t=130, b=60),
    )
    return figura


def distancia_haversine_metros(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    """Calcula a distância geodésica aproximada entre dois pontos."""
    raio_terra = 6_371_008.8
    latitude_1_rad = math.radians(float(latitude_1))
    latitude_2_rad = math.radians(float(latitude_2))
    delta_latitude = math.radians(float(latitude_2) - float(latitude_1))
    delta_longitude = math.radians(float(longitude_2) - float(longitude_1))

    termo = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1_rad)
        * math.cos(latitude_2_rad)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * raio_terra * math.asin(math.sqrt(termo))


def calcular_distancias_acumuladas(
    sondagens: list[dict[str, Any]],
) -> list[float]:
    """Calcula distâncias acumuladas na ordem escolhida para a seção."""
    if not sondagens:
        return []

    distancias = [0.0]
    for indice in range(1, len(sondagens)):
        anterior = sondagens[indice - 1]
        atual = sondagens[indice]
        trecho = distancia_haversine_metros(
            anterior["latitude"],
            anterior["longitude"],
            atual["latitude"],
            atual["longitude"],
        )
        if trecho <= 0.01:
            raise ValueError(
                "Há sondagens consecutivas com coordenadas coincidentes; "
                "não é possível construir um eixo de distância confiável."
            )
        distancias.append(distancias[-1] + trecho)
    return distancias


def _camadas_por_classificacao(
    camadas: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    agrupadas: dict[str, list[dict[str, Any]]] = {}
    for _, camada in camadas.sort_values("profundidade_inicial").iterrows():
        classificacao = str(camada["classificacao"])
        agrupadas.setdefault(classificacao, []).append(camada.to_dict())
    return agrupadas


def _adicionar_circulos_silte_secao(
    figura: go.Figure,
    x_interpolado: np.ndarray,
    cota_topo: np.ndarray,
    cota_base: np.ndarray,
) -> None:
    """Adiciona círculos dentro de um corpo interpolado de silte."""
    x_circulos: list[float] = []
    y_circulos: list[float] = []
    for posicao in range(2, len(x_interpolado) - 1, 4):
        topo_local = float(cota_topo[posicao])
        base_local = float(cota_base[posicao])
        for fracao in (0.25, 0.55, 0.82):
            x_circulos.append(float(x_interpolado[posicao]))
            y_circulos.append(topo_local + (base_local - topo_local) * fracao)

    figura.add_trace(
        go.Scatter(
            x=x_circulos,
            y=y_circulos,
            mode="markers",
            marker=dict(
                symbol="circle-open",
                size=5,
                color="#111111",
                line=dict(width=1),
            ),
            showlegend=False,
            hoverinfo="skip",
        )
    )


def criar_secao_hidroestratigrafica(
    dados_sondagens: list[dict[str, Any]],
) -> go.Figure:
    """Cria seção transversal conectando somente classes litológicas equivalentes."""
    if len(dados_sondagens) < 2:
        raise ValueError("Selecione pelo menos duas sondagens para gerar a seção.")

    if any(item["camadas"].empty for item in dados_sondagens):
        raise ValueError("Todas as sondagens selecionadas devem possuir camadas.")

    sondagens = [item["sondagem"] for item in dados_sondagens]
    distancias = calcular_distancias_acumuladas(sondagens)
    figura = go.Figure()
    classes_na_legenda: set[str] = set()

    for indice in range(len(dados_sondagens) - 1):
        item_esquerda = dados_sondagens[indice]
        item_direita = dados_sondagens[indice + 1]
        grupos_esquerda = _camadas_por_classificacao(item_esquerda["camadas"])
        grupos_direita = _camadas_por_classificacao(item_direita["camadas"])
        classes_comuns = sorted(set(grupos_esquerda) & set(grupos_direita))

        x_esquerda = distancias[indice]
        x_direita = distancias[indice + 1]
        x_interpolado = np.linspace(x_esquerda, x_direita, 40)

        for classificacao in classes_comuns:
            camadas_esquerda = grupos_esquerda[classificacao]
            camadas_direita = grupos_direita[classificacao]
            quantidade_pares = min(len(camadas_esquerda), len(camadas_direita))
            estilo = PADROES_ABNT[classificacao]

            for ocorrencia in range(quantidade_pares):
                camada_esquerda = camadas_esquerda[ocorrencia]
                camada_direita = camadas_direita[ocorrencia]
                interpolador_topo = interp1d(
                    [x_esquerda, x_direita],
                    [camada_esquerda["cota_topo"], camada_direita["cota_topo"]],
                    kind="linear",
                    assume_sorted=True,
                )
                interpolador_base = interp1d(
                    [x_esquerda, x_direita],
                    [camada_esquerda["cota_base"], camada_direita["cota_base"]],
                    kind="linear",
                    assume_sorted=True,
                )
                cota_topo = interpolador_topo(x_interpolado)
                cota_base = interpolador_base(x_interpolado)
                x_poligono = np.concatenate([x_interpolado, x_interpolado[::-1]])
                y_poligono = np.concatenate([cota_topo, cota_base[::-1]])
                mostrar_legenda = classificacao not in classes_na_legenda
                classes_na_legenda.add(classificacao)

                figura.add_trace(
                    go.Scatter(
                        x=x_poligono,
                        y=y_poligono,
                        mode="lines",
                        line=dict(color="#2C3E50", width=0.8),
                        fill="toself",
                        fillcolor=estilo["cor"],
                        fillpattern=dict(
                            shape=_padrao_nativo_plotly(estilo["padrao"]),
                            fillmode="overlay",
                            fgcolor="#111111",
                            bgcolor=estilo["cor"],
                            solidity=0.14,
                        ),
                        name=classificacao,
                        legendgroup=classificacao,
                        showlegend=mostrar_legenda,
                        customdata=np.column_stack(
                            [
                                np.full(len(x_poligono), ocorrencia + 1),
                                np.full(
                                    len(x_poligono),
                                    item_esquerda["sondagem"]["nome_furo"],
                                    dtype=object,
                                ),
                                np.full(
                                    len(x_poligono),
                                    item_direita["sondagem"]["nome_furo"],
                                    dtype=object,
                                ),
                            ]
                        ),
                        hovertemplate=(
                            f"<b>{classificacao}</b><br>"
                            "Ocorrência: %{customdata[0]}<br>"
                            "Trecho: %{customdata[1]} → %{customdata[2]}<br>"
                            "Distância: %{x:.1f} m<br>"
                            "Cota: %{y:.2f} m<extra></extra>"
                        ),
                    )
                )

                if estilo["padrao"] == "o":
                    _adicionar_circulos_silte_secao(
                        figura,
                        x_interpolado,
                        cota_topo,
                        cota_base,
                    )

    altitudes = [float(sondagem["altitude"]) for sondagem in sondagens]
    figura.add_trace(
        go.Scatter(
            x=distancias,
            y=altitudes,
            mode="lines+markers+text",
            name="Superfície do terreno",
            line=dict(color="#196F3D", width=3),
            marker=dict(size=9, symbol="diamond"),
            text=[sondagem["nome_furo"] for sondagem in sondagens],
            textposition="top center",
            hovertemplate=(
                "%{text}<br>Distância: %{x:.1f} m<br>"
                "Altitude: %{y:.2f} m<extra></extra>"
            ),
        )
    )

    distancias_na: list[float] = []
    cotas_na: list[float] = []
    for distancia, sondagem in zip(distancias, sondagens):
        nivel_agua = sondagem.get("nivel_agua_estatico")
        if nivel_agua is not None and not pd.isna(nivel_agua):
            distancias_na.append(distancia)
            cotas_na.append(float(sondagem["altitude"]) - float(nivel_agua))

    if len(distancias_na) >= 2:
        interpolador_na = interp1d(
            distancias_na,
            cotas_na,
            kind="linear",
            assume_sorted=True,
        )
        x_na = np.linspace(distancias_na[0], distancias_na[-1], 160)
        figura.add_trace(
            go.Scatter(
                x=x_na,
                y=interpolador_na(x_na),
                mode="lines",
                name="Nível d'água",
                line=dict(color="#C0392B", width=2.5, dash="dash"),
                hovertemplate=(
                    "Distância: %{x:.1f} m<br>Cota do NA: %{y:.2f} m<extra></extra>"
                ),
            )
        )
        figura.add_trace(
            go.Scatter(
                x=distancias_na,
                y=cotas_na,
                mode="markers",
                name="NA nas sondagens",
                marker=dict(
                    color="#C0392B",
                    size=11,
                    symbol="triangle-down",
                ),
                showlegend=False,
                hovertemplate="Cota do NA: %{y:.2f} m<extra></extra>",
            )
        )
    elif len(distancias_na) == 1:
        figura.add_trace(
            go.Scatter(
                x=distancias_na,
                y=cotas_na,
                mode="markers",
                name="Nível d'água",
                marker=dict(
                    color="#C0392B",
                    size=11,
                    symbol="triangle-down",
                ),
                hovertemplate="Cota do NA: %{y:.2f} m<extra></extra>",
            )
        )

    menor_cota = min(
        float(camada["cota_base"])
        for item in dados_sondagens
        for _, camada in item["camadas"].iterrows()
    )
    amplitude_x = max(distancias[-1], 1.0)
    largura_furo = max(amplitude_x * 0.004, 0.8)

    for distancia, item in zip(distancias, dados_sondagens):
        sondagem = item["sondagem"]
        figura.add_shape(
            type="line",
            x0=distancia,
            x1=distancia,
            y0=menor_cota,
            y1=float(sondagem["altitude"]),
            line=dict(color="#34495E", width=1, dash="dot"),
            layer="above",
        )

        for _, camada in item["camadas"].iterrows():
            estilo = PADROES_ABNT.get(
                str(camada["classificacao"]),
                {"cor": "#D5D8DC", "padrao": ""},
            )
            cota_topo_local = float(camada["cota_topo"])
            cota_base_local = float(camada["cota_base"])
            figura.add_trace(
                go.Scatter(
                    x=[
                        distancia - largura_furo,
                        distancia + largura_furo,
                        distancia + largura_furo,
                        distancia - largura_furo,
                    ],
                    y=[
                        cota_topo_local,
                        cota_topo_local,
                        cota_base_local,
                        cota_base_local,
                    ],
                    mode="lines",
                    line=dict(color="#17202A", width=1),
                    fill="toself",
                    fillcolor=estilo["cor"],
                    fillpattern=dict(
                        shape=_padrao_nativo_plotly(estilo["padrao"]),
                        fillmode="overlay",
                        fgcolor="#111111",
                        bgcolor=estilo["cor"],
                        solidity=0.16,
                    ),
                    showlegend=False,
                    hovertemplate=(
                        f"<b>{sondagem['nome_furo']}</b><br>"
                        f"{camada['classificacao']}<br>"
                        f"Cota do topo: {cota_topo_local:.2f} m<br>"
                        f"Cota da base: {cota_base_local:.2f} m"
                        "<extra></extra>"
                    ),
                )
            )

            if estilo["padrao"] == "o":
                figura.add_trace(
                    go.Scatter(
                        x=[
                            distancia - largura_furo * 0.45,
                            distancia + largura_furo * 0.45,
                            distancia - largura_furo * 0.45,
                            distancia + largura_furo * 0.45,
                        ],
                        y=[
                            cota_topo_local + (cota_base_local - cota_topo_local) * 0.30,
                            cota_topo_local + (cota_base_local - cota_topo_local) * 0.30,
                            cota_topo_local + (cota_base_local - cota_topo_local) * 0.70,
                            cota_topo_local + (cota_base_local - cota_topo_local) * 0.70,
                        ],
                        mode="markers",
                        marker=dict(
                            symbol="circle-open",
                            size=5,
                            color="#111111",
                            line=dict(width=1),
                        ),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                )

    figura.update_layout(
        title="Perfil hidroestratigráfico — seção transversal",
        xaxis_title="Distância acumulada ao longo da seção (m)",
        yaxis_title="Cota absoluta (m)",
        template="plotly_white",
        hovermode="closest",
        height=760,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.04,
            xanchor="left",
            x=0,
        ),
        margin=dict(l=80, r=40, t=120, b=70),
    )
    figura.update_xaxes(
        range=[-amplitude_x * 0.025, distancias[-1] + amplitude_x * 0.025],
        showgrid=True,
        gridcolor="#E5E7E9",
    )
    figura.update_yaxes(
        range=[menor_cota - 2, max(altitudes) + 4],
        showgrid=True,
        gridcolor="#E5E7E9",
        zeroline=False,
    )
    return figura
