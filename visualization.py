from __future__ import annotations

import html
import math
import textwrap
from typing import Any

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pyproj import CRS, Geod
from scipy.interpolate import interp1d

PADROES_ABNT = {
    "Argila": {"cor": "#8B4513", "padrao": "/"},
    "Areia Fina": {"cor": "#F4D03F", "padrao": "."},
    "Areia Grossa": {"cor": "#F5B041", "padrao": "x"},
    "Cascalho": {"cor": "#A9A9A9", "padrao": "+"},
    "Rocha S\u00e3": {"cor": "#2C3E50", "padrao": "|"},
    "Rocha Alterada": {"cor": "#7F8C8D", "padrao": "-"},
    "Silte": {"cor": "#BFC9CA", "padrao": "o"},
}

PADROES_NATIVOS_PLOTLY = {"", "/", "\\", "x", "-", "|", "+", "."}

CORES_ZONAS_HIDRICAS = {
    "Zona vadosa": "#F9E79F",
    "Zona saturada": "#AED6F1",
    "Indeterminada": "#E5E7E9",
}

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

GEOD_SIRGAS = Geod(ellps="GRS80")


def _padrao_nativo_plotly(padrao: str) -> str:
    """Retorna apenas padroes aceitos nativamente pelo Plotly."""
    return padrao if padrao in PADROES_NATIVOS_PLOTLY else ""


def _quebrar_texto(texto: Any, largura: int = 62) -> str:
    """Quebra texto longo para melhorar a leitura na imagem estatica."""
    conteudo = "" if texto is None else str(texto).strip()
    if not conteudo:
        return "-"
    return "<br>".join(
        textwrap.wrap(
            conteudo,
            width=largura,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


def _adicionar_circulos_silte_perfil(
    figura: go.Figure,
    profundidade_inicial: float,
    profundidade_final: float,
    coluna: int,
) -> None:
    """Simula o padrao circular de silte no perfil individual."""
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
        col=coluna,
    )


def criar_mapa_sondagens(sondagens: pd.DataFrame) -> folium.Map:
    """Cria mapa Folium a partir das coordenadas canonicas EPSG:4674."""
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
        zoom_start=7 if len(sondagens) > 1 else 13,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    projetos = list(dict.fromkeys(sondagens["projeto_id"].tolist()))
    cores_por_projeto = {
        projeto_id: CORES_MARCADORES[indice % len(CORES_MARCADORES)]
        for indice, projeto_id in enumerate(projetos)
    }

    limites: list[list[float]] = []
    for _, sondagem in sondagens.iterrows():
        latitude = float(sondagem["latitude"])
        longitude = float(sondagem["longitude"])
        limites.append([latitude, longitude])

        nivel_agua = sondagem.get("nivel_agua_estatico")
        texto_nivel = (
            "Nao informado"
            if nivel_agua is None or pd.isna(nivel_agua)
            else f"{float(nivel_agua):.2f} m"
        )
        epsg = int(sondagem.get("crs_entrada") or 4674)
        x = float(sondagem.get("coordenada_x") or longitude)
        y = float(sondagem.get("coordenada_y") or latitude)
        status = html.escape(str(sondagem.get("status") or ""))

        conteudo = (
            f"<b>{html.escape(str(sondagem['nome_furo']))}</b><br>"
            f"Projeto: {html.escape(str(sondagem['projeto_nome']))}<br>"
            f"Status: {status}<br>"
            f"CRS original: EPSG:{epsg}<br>"
            f"X: {x:.3f}<br>Y: {y:.3f}<br>"
            f"SIRGAS 2000 geografico: {latitude:.8f}, {longitude:.8f}<br>"
            f"Profundidade executada: {float(sondagem['profundidade_atual']):.2f} m<br>"
            f"Profundidade final/meta: {float(sondagem['profundidade_total']):.2f} m<br>"
            f"NA: {texto_nivel}<br>"
            f"Altitude: {float(sondagem['altitude']):.2f} m"
        )
        folium.Marker(
            location=[latitude, longitude],
            tooltip=f"{sondagem['nome_furo']} - {sondagem['projeto_nome']}",
            popup=folium.Popup(conteudo, max_width=380),
            icon=folium.Icon(
                color=cores_por_projeto[sondagem["projeto_id"]],
                icon="tint",
                prefix="fa",
            ),
        ).add_to(mapa)

    if len(limites) > 1:
        mapa.fit_bounds(limites, padding=(35, 35))
    folium.LayerControl(collapsed=True).add_to(mapa)
    return mapa


def _adicionar_linha_na_perfil(
    figura: go.Figure,
    nivel_agua: float,
) -> None:
    """Desenha o NA sem usar anotacoes automaticas que podem sobrepor textos."""
    referencias = [
        ("x domain", "y"),
        ("x2 domain", "y2"),
        ("x3 domain", "y3"),
    ]
    for xref, yref in referencias:
        figura.add_shape(
            type="line",
            x0=0,
            x1=1,
            y0=nivel_agua,
            y1=nivel_agua,
            xref=xref,
            yref=yref,
            line=dict(color="#C0392B", width=2, dash="dash"),
            layer="above",
        )

    figura.add_annotation(
        x=0.02,
        y=nivel_agua,
        xref="x2 domain",
        yref="y2",
        text=f"NA = {nivel_agua:.2f} m",
        showarrow=False,
        xanchor="left",
        yanchor="bottom",
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="#C0392B",
        borderwidth=1,
        font=dict(size=11, color="#922B21"),
    )


def criar_perfil_litologico(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    coletas: pd.DataFrame,
    voc: pd.DataFrame,
) -> go.Figure:
    """Monta perfil, curva de VOC e tabela de descricoes sem sobreposicao."""
    profundidade_total = float(sondagem["profundidade_total"])
    if profundidade_total <= 0 and not camadas.empty:
        profundidade_total = float(camadas["profundidade_final"].max())
    profundidade_total = max(profundidade_total, 0.1)

    nivel_bruto = sondagem.get("nivel_agua_estatico")
    nivel_agua = (
        None
        if nivel_bruto is None or pd.isna(nivel_bruto)
        else min(max(float(nivel_bruto), 0.0), profundidade_total)
    )

    quantidade_camadas = max(len(camadas), 1)
    altura_grafico = max(560, min(1180, int(profundidade_total * 24)))
    altura_tabela = max(220, min(920, 78 + quantidade_camadas * 62))
    altura_total = altura_grafico + altura_tabela + 120
    fracao_grafico = altura_grafico / (altura_grafico + altura_tabela)

    figura = make_subplots(
        rows=2,
        cols=3,
        specs=[
            [{"type": "xy"}, {"type": "xy"}, {"type": "xy"}],
            [{"type": "table", "colspan": 3}, None, None],
        ],
        shared_yaxes=True,
        horizontal_spacing=0.055,
        vertical_spacing=0.075,
        column_widths=[0.16, 0.34, 0.50],
        row_heights=[fracao_grafico, 1 - fracao_grafico],
        subplot_titles=(
            "Condi\u00e7\u00e3o h\u00eddrica",
            "Coluna litol\u00f3gica",
            "Concentra\u00e7\u00e3o de VOC",
        ),
    )

    if nivel_agua is None:
        figura.add_trace(
            go.Bar(
                x=[1.0],
                y=[profundidade_total / 2],
                width=[profundidade_total],
                base=0,
                orientation="h",
                name="Condi\u00e7\u00e3o h\u00eddrica indeterminada",
                marker_color=CORES_ZONAS_HIDRICAS["Indeterminada"],
                marker_line_color="#7B7D7D",
                marker_line_width=1,
                hovertemplate=(
                    "NA nao informado<br>"
                    f"Intervalo: 0,00-{profundidade_total:.2f} m"
                    "<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )
        figura.add_annotation(
            x=0.5,
            y=profundidade_total / 2,
            text="NA nao<br>informado",
            showarrow=False,
            font=dict(size=11, color="#566573"),
            row=1,
            col=1,
        )
    else:
        espessura_vadosa = nivel_agua
        espessura_saturada = max(0.0, profundidade_total - nivel_agua)
        if espessura_vadosa > 1e-9:
            figura.add_trace(
                go.Bar(
                    x=[1.0],
                    y=[espessura_vadosa / 2],
                    width=[espessura_vadosa],
                    base=0,
                    orientation="h",
                    name="Zona vadosa",
                    legendgroup="condicao_hidrica",
                    marker_color=CORES_ZONAS_HIDRICAS["Zona vadosa"],
                    marker_line_color="#B7950B",
                    marker_line_width=1,
                    hovertemplate=(
                        "<b>Zona vadosa</b><br>"
                        f"Intervalo: 0,00-{nivel_agua:.2f} m<br>"
                        f"Espessura: {espessura_vadosa:.2f} m"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )
            if espessura_vadosa >= max(0.8, profundidade_total * 0.08):
                figura.add_annotation(
                    x=0.5,
                    y=espessura_vadosa / 2,
                    text="Zona<br>vadosa",
                    showarrow=False,
                    font=dict(size=11, color="#6E4C1E"),
                    row=1,
                    col=1,
                )

        if espessura_saturada > 1e-9:
            figura.add_trace(
                go.Bar(
                    x=[1.0],
                    y=[nivel_agua + espessura_saturada / 2],
                    width=[espessura_saturada],
                    base=0,
                    orientation="h",
                    name="Trecho saturado",
                    legendgroup="condicao_hidrica",
                    marker_color=CORES_ZONAS_HIDRICAS["Zona saturada"],
                    marker_line_color="#2E86C1",
                    marker_line_width=1,
                    hovertemplate=(
                        "<b>Trecho saturado</b><br>"
                        f"Intervalo: {nivel_agua:.2f}-{profundidade_total:.2f} m<br>"
                        f"Espessura: {espessura_saturada:.2f} m"
                        "<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )
            if espessura_saturada >= max(0.8, profundidade_total * 0.08):
                figura.add_annotation(
                    x=0.5,
                    y=nivel_agua + espessura_saturada / 2,
                    text="Trecho<br>saturado",
                    showarrow=False,
                    font=dict(size=11, color="#154360"),
                    row=1,
                    col=1,
                )

    camadas_ordenadas = camadas.sort_values("profundidade_inicial").copy()
    classificacoes_na_legenda: set[str] = set()
    for _, camada in camadas_ordenadas.iterrows():
        inicio = float(camada["profundidade_inicial"])
        final = float(camada["profundidade_final"])
        espessura = final - inicio
        centro = (inicio + final) / 2
        classificacao = str(camada["classificacao"])
        estilo = PADROES_ABNT.get(
            classificacao,
            {"cor": "#D5D8DC", "padrao": ""},
        )
        mostrar_legenda = classificacao not in classificacoes_na_legenda
        classificacoes_na_legenda.add(classificacao)
        zona = str(camada.get("zona_hidrica") or "Indeterminada")

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
                        inicio,
                        final,
                        camada["descricao_tatil_visual"],
                        camada["tipo_aquifero"],
                        camada["cota_topo"],
                        camada["cota_base"],
                        zona,
                    ]
                ],
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Topo: %{customdata[0]:.2f} m<br>"
                    "Base: %{customdata[1]:.2f} m<br>"
                    "Descri\u00e7\u00e3o: %{customdata[2]}<br>"
                    "Unidade hidroestratigr\u00e1fica: %{customdata[3]}<br>"
                    "Condi\u00e7\u00e3o h\u00eddrica: %{customdata[6]}<br>"
                    "Cota do topo: %{customdata[4]:.2f} m<br>"
                    "Cota da base: %{customdata[5]:.2f} m"
                    "<extra></extra>"
                ),
            ),
            row=1,
            col=2,
        )
        if estilo["padrao"] == "o":
            _adicionar_circulos_silte_perfil(figura, inicio, final, coluna=2)

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
            col=2,
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
                    "Concentra\u00e7\u00e3o: %{x:.4g}<extra></extra>"
                ),
            ),
            row=1,
            col=3,
        )
    else:
        figura.add_annotation(
            text="Sem medicoes de VOC",
            x=0.5,
            y=0.5,
            xref="x3 domain",
            yref="y3 domain",
            showarrow=False,
            font=dict(color="#6C757D"),
        )

    if nivel_agua is not None:
        _adicionar_linha_na_perfil(figura, nivel_agua)
        figura.add_trace(
            go.Scatter(
                x=[1.08],
                y=[nivel_agua],
                mode="markers",
                name="N\u00edvel d'\u00e1gua",
                marker=dict(
                    symbol="triangle-down",
                    size=14,
                    color="#C0392B",
                    line=dict(color="#FFFFFF", width=1),
                ),
                hovertemplate=f"NA: {nivel_agua:.2f} m<extra></extra>",
            ),
            row=1,
            col=2,
        )

    figura.update_xaxes(
        range=[0, 1.0],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        title_text="Satura\u00e7\u00e3o",
        row=1,
        col=1,
    )
    figura.update_xaxes(
        range=[0, 1.16],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        title_text="Litologia",
        row=1,
        col=2,
    )
    figura.update_xaxes(
        title_text="Concentra\u00e7\u00e3o (mg/L ou ppm)",
        rangemode="tozero",
        showgrid=True,
        gridcolor="#E5E7E9",
        row=1,
        col=3,
    )
    for coluna in (1, 2, 3):
        figura.update_yaxes(
            title_text="Profundidade (m)" if coluna == 1 else None,
            range=[profundidade_total, 0],
            autorange=False,
            showgrid=True,
            gridcolor="#E5E7E9",
            row=1,
            col=coluna,
        )

    intervalos = [
        f"{float(linha['profundidade_inicial']):.2f}-{float(linha['profundidade_final']):.2f} m"
        for _, linha in camadas_ordenadas.iterrows()
    ]
    classificacoes = [str(valor) for valor in camadas_ordenadas["classificacao"].tolist()]
    descricoes = [
        _quebrar_texto(valor, largura=72)
        for valor in camadas_ordenadas["descricao_tatil_visual"].tolist()
    ]
    unidades = [
        _quebrar_texto(valor, largura=28)
        for valor in camadas_ordenadas["tipo_aquifero"].tolist()
    ]
    zonas = [
        _quebrar_texto(valor, largura=28)
        for valor in camadas_ordenadas.get(
            "zona_hidrica",
            pd.Series(["Indeterminada"] * len(camadas_ordenadas)),
        ).tolist()
    ]
    preenchimentos = [
        "#F8F9F9" if indice % 2 == 0 else "#EEF2F3"
        for indice in range(len(camadas_ordenadas))
    ]

    figura.add_trace(
        go.Table(
            columnwidth=[0.13, 0.15, 0.37, 0.17, 0.18],
            header=dict(
                values=[
                    "<b>Intervalo</b>",
                    "<b>Litologia</b>",
                    "<b>Descri\u00e7\u00e3o t\u00e1til-visual</b>",
                    "<b>Unidade hidroestratigr\u00e1fica</b>",
                    "<b>Condi\u00e7\u00e3o h\u00eddrica</b>",
                ],
                fill_color="#D6EAF8",
                line_color="#AAB7B8",
                align=["center", "left", "left", "left", "left"],
                font=dict(size=11, color="#1B2631"),
                height=32,
            ),
            cells=dict(
                values=[intervalos, classificacoes, descricoes, unidades, zonas],
                fill_color=[preenchimentos] * 5,
                line_color="#D5D8DC",
                align=["center", "left", "left", "left", "left"],
                font=dict(size=10, color="#1B2631"),
                height=58,
            ),
        ),
        row=2,
        col=1,
    )

    status = str(sondagem.get("status") or "")
    epsg = int(sondagem.get("crs_entrada") or 4674)
    figura.update_layout(
        title=dict(
            text=(
                f"Perfil litol\u00f3gico e condi\u00e7\u00e3o h\u00eddrica - {sondagem['nome_furo']}"
                f"<br><sup>Projeto: {sondagem['projeto_nome']} | "
                f"Status: {status} | CRS de entrada: EPSG:{epsg}</sup>"
            ),
            x=0.5,
            xanchor="center",
            y=0.965,
            yanchor="top",
            font=dict(size=20),
        ),
        height=altura_total,
        barmode="overlay",
        bargap=0,
        template="plotly_white",
        hovermode="closest",
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.96,
            xanchor="left",
            x=1.01,
            bgcolor="rgba(255,255,255,0.86)",
            bordercolor="#D5D8DC",
            borderwidth=1,
            font=dict(size=10),
        ),
        margin=dict(l=72, r=235, t=145, b=35),
    )
    return figura


def distancia_entre_sondagens_metros(
    sondagem_1: dict[str, Any],
    sondagem_2: dict[str, Any],
) -> float:
    """Calcula distancia em CRS projetado comum ou geodesica SIRGAS 2000."""
    epsg_1 = sondagem_1.get("crs_entrada")
    epsg_2 = sondagem_2.get("crs_entrada")
    if epsg_1 is not None and epsg_2 is not None and int(epsg_1) == int(epsg_2):
        try:
            crs = CRS.from_epsg(int(epsg_1))
            if crs.is_projected:
                x1 = float(sondagem_1["coordenada_x"])
                y1 = float(sondagem_1["coordenada_y"])
                x2 = float(sondagem_2["coordenada_x"])
                y2 = float(sondagem_2["coordenada_y"])
                return math.hypot(x2 - x1, y2 - y1)
        except (TypeError, ValueError):
            pass

    longitude_1 = float(sondagem_1["longitude"])
    latitude_1 = float(sondagem_1["latitude"])
    longitude_2 = float(sondagem_2["longitude"])
    latitude_2 = float(sondagem_2["latitude"])
    _, _, distancia = GEOD_SIRGAS.inv(
        longitude_1,
        latitude_1,
        longitude_2,
        latitude_2,
    )
    return abs(float(distancia))


def calcular_distancias_acumuladas(
    sondagens: list[dict[str, Any]],
) -> list[float]:
    """Calcula distancias acumuladas na ordem escolhida para a secao."""
    if not sondagens:
        return []
    distancias = [0.0]
    for indice in range(1, len(sondagens)):
        trecho = distancia_entre_sondagens_metros(
            sondagens[indice - 1],
            sondagens[indice],
        )
        if trecho <= 0.01:
            raise ValueError(
                "Ha sondagens consecutivas com coordenadas coincidentes."
            )
        distancias.append(distancias[-1] + trecho)
    return distancias


def _camadas_por_classificacao(
    camadas: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    """Agrupa ocorrencias de uma classificacao na ordem vertical."""
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
    """Adiciona circulos dentro de um corpo interpolado de silte."""
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
    """Cria secao conectando somente classes litologicas equivalentes."""
    if len(dados_sondagens) < 2:
        raise ValueError("Selecione pelo menos duas sondagens para gerar a secao.")
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
                            "Ocorr\u00eancia: %{customdata[0]}<br>"
                            "Trecho: %{customdata[1]} -> %{customdata[2]}<br>"
                            "Dist\u00e2ncia: %{x:.1f} m<br>"
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

    zona_vadosa_na_legenda = False
    for indice in range(len(sondagens) - 1):
        esquerda = sondagens[indice]
        direita = sondagens[indice + 1]
        na_esquerda = esquerda.get("nivel_agua_estatico")
        na_direita = direita.get("nivel_agua_estatico")
        if (
            na_esquerda is None
            or pd.isna(na_esquerda)
            or na_direita is None
            or pd.isna(na_direita)
        ):
            continue

        x_esquerda = distancias[indice]
        x_direita = distancias[indice + 1]
        x_interpolado = np.linspace(x_esquerda, x_direita, 80)
        superficie = interp1d(
            [x_esquerda, x_direita],
            [float(esquerda["altitude"]), float(direita["altitude"])],
            kind="linear",
            assume_sorted=True,
        )(x_interpolado)
        cota_na = interp1d(
            [x_esquerda, x_direita],
            [
                float(esquerda["altitude"]) - float(na_esquerda),
                float(direita["altitude"]) - float(na_direita),
            ],
            kind="linear",
            assume_sorted=True,
        )(x_interpolado)
        x_poligono = np.concatenate([x_interpolado, x_interpolado[::-1]])
        y_poligono = np.concatenate([superficie, cota_na[::-1]])

        figura.add_trace(
            go.Scatter(
                x=x_poligono,
                y=y_poligono,
                mode="lines",
                line=dict(color="rgba(183,149,11,0.55)", width=0.7),
                fill="toself",
                fillcolor="rgba(249,231,159,0.34)",
                name="Zona vadosa",
                legendgroup="condicao_hidrica",
                showlegend=not zona_vadosa_na_legenda,
                hovertemplate=(
                    "<b>Zona vadosa</b><br>"
                    "Dist\u00e2ncia: %{x:.1f} m<br>"
                    "Cota: %{y:.2f} m<extra></extra>"
                ),
            )
        )
        zona_vadosa_na_legenda = True

    figura.add_trace(
        go.Scatter(
            x=distancias,
            y=altitudes,
            mode="lines+markers+text",
            name="Superf\u00edcie do terreno",
            line=dict(color="#196F3D", width=3),
            marker=dict(size=9, symbol="diamond"),
            text=[sondagem["nome_furo"] for sondagem in sondagens],
            textposition="top center",
            hovertemplate=(
                "%{text}<br>Distancia: %{x:.1f} m<br>"
                "Altitude: %{y:.2f} m<extra></extra>"
            ),
        )
    )

    distancias_na: list[float] = []
    cotas_na: list[float] = []
    for distancia, sondagem in zip(distancias, sondagens):
        nivel = sondagem.get("nivel_agua_estatico")
        if nivel is not None and not pd.isna(nivel):
            distancias_na.append(distancia)
            cotas_na.append(float(sondagem["altitude"]) - float(nivel))

    if len(distancias_na) >= 2:
        x_na = np.linspace(distancias_na[0], distancias_na[-1], 160)
        y_na = interp1d(
            distancias_na,
            cotas_na,
            kind="linear",
            assume_sorted=True,
        )(x_na)
        figura.add_trace(
            go.Scatter(
                x=x_na,
                y=y_na,
                mode="lines",
                name="N\u00edvel d'\u00e1gua",
                line=dict(color="#C0392B", width=2.5, dash="dash"),
                hovertemplate=(
                    "Dist\u00e2ncia: %{x:.1f} m<br>"
                    "Cota do NA: %{y:.2f} m<extra></extra>"
                ),
            )
        )
        figura.add_trace(
            go.Scatter(
                x=distancias_na,
                y=cotas_na,
                mode="markers",
                name="NA nas sondagens",
                marker=dict(color="#C0392B", size=11, symbol="triangle-down"),
                showlegend=False,
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
        figura.add_shape(
            type="rect",
            x0=distancia - largura_furo / 2,
            x1=distancia + largura_furo / 2,
            y0=float(sondagem["altitude"]) - float(sondagem["profundidade_total"]),
            y1=float(sondagem["altitude"]),
            line=dict(color="#17202A", width=1),
            fillcolor="rgba(255,255,255,0.08)",
            layer="above",
        )

    figura.update_layout(
        title=dict(
            text="Perfil hidroestratigr\u00e1fico - se\u00e7\u00e3o transversal",
            x=0.5,
            xanchor="center",
            y=0.98,
            yanchor="top",
        ),
        xaxis_title="Dist\u00e2ncia acumulada (m)",
        yaxis_title="Cota absoluta (m)",
        template="plotly_white",
        hovermode="closest",
        height=860,
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.96,
            xanchor="left",
            x=1.01,
            bgcolor="rgba(255,255,255,0.86)",
            bordercolor="#D5D8DC",
            borderwidth=1,
            font=dict(size=10),
        ),
        margin=dict(l=80, r=220, t=90, b=70),
    )
    figura.update_xaxes(showgrid=True, gridcolor="#E5E7E9")
    figura.update_yaxes(showgrid=True, gridcolor="#E5E7E9")
    return figura


CORES_COMPONENTES_CONSTRUTIVOS = {
    "Tubo cego": {"cor": "#ECF0F1", "linha": "#1F618D", "padrao": ""},
    "Seção filtrante": {"cor": "#85C1E9", "linha": "#1F618D", "padrao": "-"},
    "Pré-filtro": {"cor": "#F7DC6F", "linha": "#B7950B", "padrao": "."},
    "Selo de bentonita": {"cor": "#82E0AA", "linha": "#1E8449", "padrao": "x"},
    "Cimentação": {"cor": "#B3B6B7", "linha": "#626567", "padrao": "+"},
    "Fundo / sedimentador": {"cor": "#5D6D7E", "linha": "#273746", "padrao": "|"},
}


def _adicionar_linha_horizontal_dominos(
    figura: go.Figure,
    profundidade: float,
    referencias: list[tuple[str, str]],
    cor: str,
    tracejado: str = "dash",
    largura: float = 2.0,
) -> None:
    """Desenha a mesma linha horizontal em diferentes subgráficos."""
    for xref, yref in referencias:
        figura.add_shape(
            type="line",
            x0=0,
            x1=1,
            y0=profundidade,
            y1=profundidade,
            xref=xref,
            yref=yref,
            line=dict(color=cor, width=largura, dash=tracejado),
            layer="above",
        )


def criar_perfil_construtivo(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    poco: dict[str, Any] | None,
    intervalos: pd.DataFrame,
) -> go.Figure:
    """Gera perfil litológico e construtivo do poço em uma única imagem."""
    profundidade_sondagem = float(sondagem.get("profundidade_total") or 0)
    profundidade_poco = (
        float(poco.get("profundidade_poco") or 0) if poco else profundidade_sondagem
    )
    profundidade_total = max(profundidade_sondagem, profundidade_poco, 0.1)
    quantidade_intervalos = max(len(intervalos), 1)
    altura_grafico = max(620, min(1180, int(profundidade_total * 27)))
    altura_tabela = max(230, min(900, 95 + quantidade_intervalos * 52))
    altura_total = altura_grafico + altura_tabela + 130
    fracao_grafico = altura_grafico / (altura_grafico + altura_tabela)

    figura = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "xy"}, {"type": "xy"}],
            [{"type": "table", "colspan": 2}, None],
        ],
        horizontal_spacing=0.09,
        vertical_spacing=0.08,
        column_widths=[0.42, 0.58],
        row_heights=[fracao_grafico, 1 - fracao_grafico],
        subplot_titles=("Coluna litológica", "Perfil construtivo do poço"),
    )

    classificacoes_na_legenda: set[str] = set()
    if camadas.empty:
        figura.add_annotation(
            text="Perfil litológico não cadastrado",
            x=0.5,
            y=0.5,
            xref="x domain",
            yref="y domain",
            showarrow=False,
        )
    else:
        for _, camada in camadas.sort_values("profundidade_inicial").iterrows():
            inicio = float(camada["profundidade_inicial"])
            final = float(camada["profundidade_final"])
            espessura = final - inicio
            centro = (inicio + final) / 2
            classificacao = str(camada["classificacao"])
            estilo = PADROES_ABNT.get(
                classificacao,
                {"cor": "#D5D8DC", "padrao": ""},
            )
            mostrar = classificacao not in classificacoes_na_legenda
            classificacoes_na_legenda.add(classificacao)
            figura.add_trace(
                go.Bar(
                    x=[1.0],
                    y=[centro],
                    width=[espessura],
                    base=0,
                    orientation="h",
                    name=classificacao,
                    legendgroup=f"litologia_{classificacao}",
                    showlegend=mostrar,
                    marker_color=estilo["cor"],
                    marker_line_color="#1F1F1F",
                    marker_line_width=1,
                    marker_pattern_shape=_padrao_nativo_plotly(estilo["padrao"]),
                    marker_pattern_fillmode="overlay",
                    marker_pattern_fgcolor="#111111",
                    marker_pattern_solidity=0.18,
                    customdata=[
                        [
                            inicio,
                            final,
                            camada.get("descricao_tatil_visual", ""),
                            camada.get("tipo_aquifero", ""),
                        ]
                    ],
                    hovertemplate=(
                        "<b>%{fullData.name}</b><br>"
                        "Intervalo: %{customdata[0]:.2f}-%{customdata[1]:.2f} m<br>"
                        "Descrição: %{customdata[2]}<br>"
                        "Unidade: %{customdata[3]}<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )
            if estilo["padrao"] == "o":
                _adicionar_circulos_silte_perfil(figura, inicio, final, coluna=1)

    componentes_na_legenda: set[str] = set()
    if poco is None:
        figura.add_annotation(
            text="Dados construtivos não cadastrados",
            x=0.5,
            y=0.5,
            xref="x2 domain",
            yref="y2 domain",
            showarrow=False,
            font=dict(color="#6C757D"),
        )
    else:
        # Materiais do espaço anular são desenhados primeiro, atrás da coluna do poço.
        ordem_componentes = [
            "Cimentação",
            "Selo de bentonita",
            "Pré-filtro",
            "Tubo cego",
            "Seção filtrante",
            "Fundo / sedimentador",
        ]
        intervalos_ordenados = intervalos.copy()
        if not intervalos_ordenados.empty:
            intervalos_ordenados["_ordem"] = intervalos_ordenados["componente"].map(
                {nome: indice for indice, nome in enumerate(ordem_componentes)}
            ).fillna(99)
            intervalos_ordenados = intervalos_ordenados.sort_values(
                ["_ordem", "profundidade_inicial"]
            )

        for _, intervalo in intervalos_ordenados.iterrows():
            componente = str(intervalo["componente"])
            inicio = float(intervalo["profundidade_inicial"])
            final = float(intervalo["profundidade_final"])
            espessura = final - inicio
            centro = (inicio + final) / 2
            estilo = CORES_COMPONENTES_CONSTRUTIVOS.get(
                componente,
                {"cor": "#D5D8DC", "linha": "#626567", "padrao": ""},
            )
            tubular = componente in {
                "Tubo cego",
                "Seção filtrante",
                "Fundo / sedimentador",
            }
            largura_barra = 0.24 if tubular else 0.74
            centro_x = 0.50
            mostrar = componente not in componentes_na_legenda
            componentes_na_legenda.add(componente)
            figura.add_trace(
                go.Bar(
                    x=[largura_barra],
                    y=[centro],
                    width=[espessura],
                    base=centro_x - largura_barra / 2,
                    orientation="h",
                    name=componente,
                    legendgroup=f"construcao_{componente}",
                    showlegend=mostrar,
                    marker_color=estilo["cor"],
                    marker_line_color=estilo["linha"],
                    marker_line_width=1.4,
                    marker_pattern_shape=_padrao_nativo_plotly(estilo["padrao"]),
                    marker_pattern_fillmode="overlay",
                    marker_pattern_fgcolor="#273746",
                    marker_pattern_solidity=0.16,
                    customdata=[
                        [
                            inicio,
                            final,
                            intervalo.get("material", ""),
                            intervalo.get("especificacao", ""),
                            intervalo.get("diametro_mm"),
                            intervalo.get("abertura_ranhura_mm"),
                            intervalo.get("granulometria", ""),
                        ]
                    ],
                    hovertemplate=(
                        "<b>%{fullData.name}</b><br>"
                        "Intervalo: %{customdata[0]:.2f}-%{customdata[1]:.2f} m<br>"
                        "Material: %{customdata[2]}<br>"
                        "Especificação: %{customdata[3]}<br>"
                        "Diâmetro: %{customdata[4]} mm<br>"
                        "Ranhura: %{customdata[5]} mm<br>"
                        "Granulometria: %{customdata[6]}<extra></extra>"
                    ),
                ),
                row=1,
                col=2,
            )

        # Linha central ajuda a identificar o eixo do revestimento.
        figura.add_shape(
            type="line",
            x0=0.5,
            x1=0.5,
            y0=0,
            y1=profundidade_poco,
            xref="x2",
            yref="y2",
            line=dict(color="#17202A", width=1, dash="dot"),
            layer="above",
        )

        if bool(poco.get("camara_calcada")):
            figura.add_shape(
                type="rect",
                x0=0.20,
                x1=0.80,
                y0=-0.55,
                y1=-0.03,
                xref="x2",
                yref="y2",
                fillcolor="#7B7D7D",
                line=dict(color="#424949", width=1.4),
            )
            figura.add_annotation(
                x=0.5,
                y=-0.29,
                xref="x2",
                yref="y2",
                text="Câmara de calçada",
                showarrow=False,
                font=dict(size=10, color="#FFFFFF"),
            )

        figura.add_annotation(
            x=0.98,
            y=0,
            xref="x2",
            yref="y2",
            text=(
                f"Prof. do poço: {profundidade_poco:.2f} m<br>"
                f"Ø revestimento: {poco.get('diametro_revestimento_mm') or '-'} mm"
            ),
            showarrow=False,
            xanchor="right",
            yanchor="bottom",
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="#AAB7B8",
            borderwidth=1,
            font=dict(size=10),
        )

    nivel_bruto = sondagem.get("nivel_agua_estatico")
    if nivel_bruto is not None and not pd.isna(nivel_bruto):
        nivel = min(max(float(nivel_bruto), 0.0), profundidade_total)
        _adicionar_linha_horizontal_dominos(
            figura,
            nivel,
            [("x domain", "y"), ("x2 domain", "y2")],
            cor="#C0392B",
            tracejado="dash",
            largura=2,
        )
        figura.add_annotation(
            x=0.02,
            y=nivel,
            xref="x2 domain",
            yref="y2",
            text=f"NA = {nivel:.2f} m",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            bgcolor="rgba(255,255,255,0.90)",
            bordercolor="#C0392B",
            borderwidth=1,
            font=dict(size=10, color="#922B21"),
        )

    # Superfície do terreno.
    _adicionar_linha_horizontal_dominos(
        figura,
        0.0,
        [("x domain", "y"), ("x2 domain", "y2")],
        cor="#784212",
        tracejado="solid",
        largura=2.5,
    )

    figura.update_xaxes(
        range=[0, 1.0],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        title_text="Litologia",
        row=1,
        col=1,
    )
    figura.update_xaxes(
        range=[0, 1.0],
        showticklabels=False,
        showgrid=False,
        zeroline=False,
        fixedrange=True,
        title_text="Elementos construtivos",
        row=1,
        col=2,
    )
    for coluna in (1, 2):
        figura.update_yaxes(
            range=[profundidade_total + 0.35, -0.75],
            autorange=False,
            title_text="Profundidade (m)" if coluna == 1 else None,
            showgrid=True,
            gridcolor="#E5E7E9",
            row=1,
            col=coluna,
        )

    if intervalos.empty:
        valores_tabela = [["-"], ["-"], ["-"], ["-"], ["-"]]
        preenchimentos = ["#F8F9F9"]
    else:
        ordenados_tabela = intervalos.sort_values(
            ["profundidade_inicial", "componente"]
        )
        valores_tabela = [
            ordenados_tabela["componente"].astype(str).tolist(),
            [
                f"{float(linha['profundidade_inicial']):.2f}-{float(linha['profundidade_final']):.2f} m"
                for _, linha in ordenados_tabela.iterrows()
            ],
            [_quebrar_texto(valor, 34) for valor in ordenados_tabela["material"].tolist()],
            [_quebrar_texto(valor, 46) for valor in ordenados_tabela["especificacao"].tolist()],
            [
                (
                    f"Ø {_texto_plotly(linha.get('diametro_mm'))} mm | "
                    f"ranhura {_texto_plotly(linha.get('abertura_ranhura_mm'))} mm | "
                    f"{_quebrar_texto(linha.get('granulometria'), 24)}"
                )
                for _, linha in ordenados_tabela.iterrows()
            ],
        ]
        preenchimentos = [
            "#F8F9F9" if indice % 2 == 0 else "#EEF2F3"
            for indice in range(len(ordenados_tabela))
        ]

    figura.add_trace(
        go.Table(
            columnwidth=[0.18, 0.14, 0.21, 0.27, 0.20],
            header=dict(
                values=[
                    "<b>Componente</b>",
                    "<b>Intervalo</b>",
                    "<b>Material</b>",
                    "<b>Especificação</b>",
                    "<b>Dimensões / granulometria</b>",
                ],
                fill_color="#D6EAF8",
                line_color="#AAB7B8",
                align=["left", "center", "left", "left", "left"],
                font=dict(size=10, color="#1B2631"),
                height=32,
            ),
            cells=dict(
                values=valores_tabela,
                fill_color=[preenchimentos] * 5,
                line_color="#D5D8DC",
                align=["left", "center", "left", "left", "left"],
                font=dict(size=9, color="#1B2631"),
                height=47,
            ),
        ),
        row=2,
        col=1,
    )

    epsg = int(sondagem.get("crs_entrada") or 4674)
    figura.update_layout(
        title=dict(
            text=(
                f"Perfil construtivo do poço — {sondagem.get('nome_furo', '')}"
                f"<br><sup>Projeto: {sondagem.get('projeto_nome', '')} | "
                f"CRS de entrada: EPSG:{epsg}</sup>"
            ),
            x=0.5,
            xanchor="center",
            y=0.975,
            yanchor="top",
            font=dict(size=20),
        ),
        height=altura_total,
        barmode="overlay",
        bargap=0,
        template="plotly_white",
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.96,
            xanchor="left",
            x=1.01,
            bgcolor="rgba(255,255,255,0.90)",
            bordercolor="#D5D8DC",
            borderwidth=1,
            font=dict(size=9),
        ),
        margin=dict(l=74, r=250, t=145, b=35),
    )
    return figura


def _texto_plotly(valor: Any) -> str:
    """Formata valores opcionais usados em células da tabela Plotly."""
    if valor is None:
        return "-"
    try:
        if pd.isna(valor):
            return "-"
    except (TypeError, ValueError):
        pass
    try:
        return f"{float(valor):.3g}"
    except (TypeError, ValueError):
        return str(valor)


def criar_grafico_desenvolvimento(leituras: pd.DataFrame) -> go.Figure:
    """Apresenta a evolução de vazão, turbidez e NA durante o desenvolvimento."""
    figura = make_subplots(specs=[[{"secondary_y": True}]])
    if leituras.empty:
        figura.add_annotation(
            text="Sem leituras cronológicas do desenvolvimento",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font=dict(color="#6C757D"),
        )
    else:
        ordenadas = leituras.sort_values("tempo_min")
        if ordenadas["turbidez_ntu"].notna().any():
            figura.add_trace(
                go.Scatter(
                    x=ordenadas["tempo_min"],
                    y=ordenadas["turbidez_ntu"],
                    mode="lines+markers",
                    name="Turbidez (NTU)",
                    line=dict(width=2.2),
                ),
                secondary_y=False,
            )
        if ordenadas["vazao_l_min"].notna().any():
            figura.add_trace(
                go.Scatter(
                    x=ordenadas["tempo_min"],
                    y=ordenadas["vazao_l_min"],
                    mode="lines+markers",
                    name="Vazão (L/min)",
                    line=dict(width=2.2, dash="dot"),
                ),
                secondary_y=False,
            )
        if ordenadas["nivel_agua_m"].notna().any():
            figura.add_trace(
                go.Scatter(
                    x=ordenadas["tempo_min"],
                    y=ordenadas["nivel_agua_m"],
                    mode="lines+markers",
                    name="NA (m)",
                    line=dict(width=2.2, dash="dash"),
                ),
                secondary_y=True,
            )
    figura.update_xaxes(title_text="Tempo acumulado (min)")
    figura.update_yaxes(title_text="Turbidez / vazão", secondary_y=False)
    figura.update_yaxes(
        title_text="Profundidade do NA (m)",
        autorange="reversed",
        secondary_y=True,
    )
    figura.update_layout(
        title=dict(
            text="Evolução do desenvolvimento do poço",
            x=0.5,
            xanchor="center",
            y=0.98,
            yanchor="top",
        ),
        template="plotly_white",
        height=520,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.18,
            xanchor="left",
            x=0,
            bgcolor="rgba(255,255,255,0.85)",
        ),
        margin=dict(l=70, r=80, t=90, b=115),
    )
    return figura
