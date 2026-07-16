from __future__ import annotations

import io
import os
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import db_manager as db
import visualization as viz

st.set_page_config(
    page_title="Diario de Sondagem Hidrogeologica",
    page_icon="\U0001f4a7",
    layout="wide",
    initial_sidebar_state="expanded",
)

DIRETORIO_APLICACAO = Path(__file__).resolve().parent
CAMINHO_EXEMPLO = DIRETORIO_APLICACAO / "exemplo.csv"
MODO_BANCO = os.getenv("HIDRO_DB_MODE", "local").strip().lower()

CRS_PREDEFINIDOS = {
    31978: "SIRGAS 2000 / UTM zona 18S - EPSG:31978",
    31979: "SIRGAS 2000 / UTM zona 19S - EPSG:31979",
    31980: "SIRGAS 2000 / UTM zona 20S - EPSG:31980",
    31981: "SIRGAS 2000 / UTM zona 21S - EPSG:31981",
    31982: "SIRGAS 2000 / UTM zona 22S - EPSG:31982",
    31983: "SIRGAS 2000 / UTM zona 23S - EPSG:31983",
    31984: "SIRGAS 2000 / UTM zona 24S - EPSG:31984",
    31985: "SIRGAS 2000 / UTM zona 25S - EPSG:31985",
    4674: "SIRGAS 2000 geografico - EPSG:4674",
    4326: "WGS 84 geografico / GPS - EPSG:4326",
}

ROTULOS_STATUS = {
    db.STATUS_PLANEJADA: "Planejada",
    db.STATUS_EXECUCAO: "Em execu\u00e7\u00e3o",
    db.STATUS_CONCLUIDA: "Conclu\u00edda",
}


def resolver_caminho_banco() -> Path:
    """Define banco persistente local ou banco privado por sessao web."""
    if MODO_BANCO == "session":
        identificador = st.session_state.setdefault(
            "_identificador_banco",
            uuid.uuid4().hex,
        )
        caminho = (
            Path(tempfile.gettempdir())
            / "hidro_litologia_web"
            / f"hidrogeologia_{identificador}.db"
        )
    else:
        caminho_configurado = os.getenv("HIDRO_DB_PATH", "").strip()
        caminho = (
            Path(caminho_configurado).expanduser()
            if caminho_configurado
            else DIRETORIO_APLICACAO / "data" / "hidrogeologia.db"
        )
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return caminho


CAMINHO_BANCO = resolver_caminho_banco()
db.inicializar_banco(CAMINHO_BANCO)

st.markdown(
    """
    <style>
        .block-container {padding-top: 1.0rem; padding-bottom: 2rem;}
        .stMetric {border: 1px solid #E5E7E9; border-radius: 0.6rem; padding: 0.65rem;}
        .nota-tecnica {background: #F4F6F7; border-left: 4px solid #2471A3;
                       padding: 0.85rem 1rem; border-radius: 0.3rem; margin: 0.5rem 0;}
        .etapa-fluxo {background: linear-gradient(90deg, #EBF5FB, #F8F9F9);
                     border: 1px solid #D6EAF8; border-radius: 0.55rem;
                     padding: 0.8rem 1rem; margin-bottom: 0.8rem;}
        .status-planejada {color: #7D6608; font-weight: 700;}
        .status-execucao {color: #1F618D; font-weight: 700;}
        .status-concluida {color: #196F3D; font-weight: 700;}
    </style>
    """,
    unsafe_allow_html=True,
)


def registrar_mensagem(tipo: str, texto: str) -> None:
    """Guarda uma mensagem para exibicao apos a proxima execucao."""
    st.session_state["mensagem_pendente"] = (tipo, texto)


def exibir_mensagem_pendente() -> None:
    """Exibe e remove a mensagem pendente da sessao."""
    mensagem = st.session_state.pop("mensagem_pendente", None)
    if not mensagem:
        return
    tipo, texto = mensagem
    exibidores = {
        "sucesso": st.success,
        "erro": st.error,
        "aviso": st.warning,
        "info": st.info,
    }
    exibidores.get(tipo, st.info)(texto)


def limpar_estado_dependente_do_banco() -> None:
    """Remove estados que podem apontar para registros do banco anterior."""
    prefixos = (
        "coord_",
        "gps_",
        "relatorio_importacao",
        "png_",
    )
    for chave in list(st.session_state):
        if chave == "relatorio_importacao" or chave.startswith(prefixos):
            st.session_state.pop(chave, None)


def renderizar_controles_banco() -> None:
    """Exibe resumo, backup e restauracao no painel lateral."""
    resumo = db.obter_resumo_banco(CAMINHO_BANCO)
    modo_texto = "Privado por sess\u00e3o" if MODO_BANCO == "session" else "Persistente"
    st.write(f"Banco: **{modo_texto}**")
    st.caption(
        f"{resumo['projetos']} projeto(s), {resumo['sondagens']} sondagem(ns), "
        f"{resumo['camadas']} camada(s) final(is) e "
        f"{resumo['intervalos_campo']} intervalo(s) em campo."
    )
    if MODO_BANCO == "session":
        st.warning(
            "Os dados desta implanta\u00e7\u00e3o ficam na sess\u00e3o do navegador. "
            "Baixe um backup antes de encerrar o trabalho."
        )

    try:
        backup = db.exportar_banco_bytes(CAMINHO_BANCO)
        st.download_button(
            "Baixar backup SQLite",
            data=backup,
            file_name="hidrogeologia_backup.db",
            mime="application/vnd.sqlite3",
            width="stretch",
            key="baixar_backup_banco",
        )
    except Exception as erro:
        st.error(f"N\u00e3o foi poss\u00edvel preparar o backup: {erro}")

    arquivo = st.file_uploader(
        "Restaurar backup SQLite",
        type=["db", "sqlite", "sqlite3"],
        key="arquivo_restauracao_banco",
    )
    confirmar = st.checkbox(
        "Confirmo a substitui\u00e7\u00e3o do banco atual",
        disabled=arquivo is None,
        key="confirmar_restauracao_banco",
    )
    if st.button(
        "Restaurar banco",
        width="stretch",
        disabled=arquivo is None or not confirmar,
        key="botao_restaurar_banco",
    ):
        try:
            db.restaurar_banco_bytes(arquivo.getvalue(), CAMINHO_BANCO)
            limpar_estado_dependente_do_banco()
            registrar_mensagem("sucesso", "Backup restaurado com sucesso.")
            st.rerun()
        except Exception as erro:
            st.error(str(erro))


def rotulo_status(status: str) -> str:
    """Converte o valor tecnico do status em rotulo de interface."""
    return ROTULOS_STATUS.get(str(status), str(status))


def opcoes_sondagens(sondagens: pd.DataFrame) -> dict[int, str]:
    """Monta rotulos unicos para seletores de sondagem."""
    return {
        int(linha["id"]): (
            f"{linha['projeto_nome']} | {linha['nome_furo']} | "
            f"{rotulo_status(linha['status'])} | ID {int(linha['id'])}"
        )
        for _, linha in sondagens.iterrows()
    }


def renderizar_cabecalho_etapa(numero: int, titulo: str, descricao: str) -> None:
    """Exibe uma faixa que contextualiza a etapa do fluxo de campo."""
    st.markdown(
        f"<div class='etapa-fluxo'><b>Etapa {numero} - {titulo}</b><br>{descricao}</div>",
        unsafe_allow_html=True,
    )


def selecionar_crs(prefixo: str, epsg_padrao: int = 31983) -> int:
    """Renderiza seletor de CRS com opcao para qualquer codigo EPSG."""
    opcoes: list[int | str] = list(CRS_PREDEFINIDOS) + ["Outro EPSG"]
    valor_anterior = st.session_state.get(f"{prefixo}_opcao_crs", epsg_padrao)
    if valor_anterior not in opcoes:
        valor_anterior = epsg_padrao
    indice = opcoes.index(valor_anterior)
    opcao = st.selectbox(
        "Sistema de coordenadas de entrada *",
        options=opcoes,
        index=indice,
        format_func=lambda valor: (
            CRS_PREDEFINIDOS[int(valor)]
            if isinstance(valor, int)
            else str(valor)
        ),
        key=f"{prefixo}_opcao_crs",
    )
    if opcao == "Outro EPSG":
        epsg = int(
            st.number_input(
                "C\u00f3digo EPSG *",
                min_value=1,
                value=int(st.session_state.get(f"{prefixo}_epsg_outro", epsg_padrao)),
                step=1,
                key=f"{prefixo}_epsg_outro",
            )
        )
    else:
        epsg = int(opcao)
    metadados = db.obter_metadados_crs(epsg)
    st.caption(
        f"EPSG:{epsg} - {metadados['nome']} | unidade: {metadados['unidade']}"
    )
    return epsg


def definir_coordenadas_simuladas(prefixo: str, epsg: int) -> None:
    """Preenche coordenadas fixas convertidas para o CRS selecionado."""
    x, y = db.converter_de_sirgas2000(
        latitude=-15.793889,
        longitude=-47.882778,
        epsg_destino=epsg,
    )
    st.session_state[f"{prefixo}_coord_x"] = float(x)
    st.session_state[f"{prefixo}_coord_y"] = float(y)
    st.session_state[f"{prefixo}_epsg_anterior"] = int(epsg)


def sincronizar_campos_coordenadas(prefixo: str, epsg: int) -> None:
    """Reinicializa os campos quando o usuario troca o CRS."""
    if st.session_state.get(f"{prefixo}_epsg_anterior") != int(epsg):
        definir_coordenadas_simuladas(prefixo, epsg)


def renderizar_campos_coordenadas(prefixo: str, epsg: int) -> tuple[float, float]:
    """Monta os campos X/Y de acordo com o tipo de CRS."""
    sincronizar_campos_coordenadas(prefixo, epsg)
    metadados = db.obter_metadados_crs(epsg)
    geografico = bool(metadados["geografico"])
    formato = "%.8f" if geografico else "%.3f"
    passo = 0.000001 if geografico else 0.1
    coluna_x, coluna_y = st.columns(2)
    with coluna_x:
        x = st.number_input(
            f"{metadados['rotulo_x']} *",
            step=passo,
            format=formato,
            key=f"{prefixo}_coord_x",
        )
    with coluna_y:
        y = st.number_input(
            f"{metadados['rotulo_y']} *",
            step=passo,
            format=formato,
            key=f"{prefixo}_coord_y",
        )
    try:
        latitude, longitude = db.converter_para_sirgas2000(x, y, epsg)
        st.caption(
            "Convers\u00e3o para o mapa - SIRGAS 2000 geogr\u00e1fico (EPSG:4674): "
            f"lat {latitude:.8f}, lon {longitude:.8f}."
        )
    except db.ErroValidacao as erro:
        st.error(str(erro))
    return float(x), float(y)


def tabela_sondagens_resumida(sondagens: pd.DataFrame) -> pd.DataFrame:
    """Prepara uma tabela legivel com coordenadas e andamento."""
    if sondagens.empty:
        return sondagens
    colunas = [
        "projeto_nome",
        "nome_furo",
        "status",
        "crs_entrada",
        "coordenada_x",
        "coordenada_y",
        "latitude",
        "longitude",
        "altitude",
        "profundidade_planejada",
        "profundidade_atual",
        "nivel_agua_estatico",
    ]
    tabela = sondagens[colunas].copy()
    tabela["status"] = tabela["status"].map(rotulo_status)
    return tabela.rename(
        columns={
            "projeto_nome": "projeto",
            "nome_furo": "sondagem",
            "crs_entrada": "EPSG",
            "coordenada_x": "X",
            "coordenada_y": "Y",
            "profundidade_planejada": "prof_planejada_m",
            "profundidade_atual": "prof_executada_m",
            "nivel_agua_estatico": "NA_m",
        }
    )


def renderizar_aba_locacao() -> None:
    """Renderiza projetos, locacao e revisao de coordenadas."""
    renderizar_cabecalho_etapa(
        1,
        "Projeto e loca\u00e7\u00e3o",
        "Cadastre o projeto, defina o identificador do furo e registre a posi\u00e7\u00e3o antes da perfura\u00e7\u00e3o.",
    )
    coluna_projeto, coluna_sondagem = st.columns([0.85, 1.55], gap="large")

    with coluna_projeto:
        st.markdown("#### Novo projeto")
        with st.form("formulario_novo_projeto", clear_on_submit=True):
            nome = st.text_input("Nome do projeto *")
            descricao = st.text_area("Descri\u00e7\u00e3o", height=110)
            enviar = st.form_submit_button(
                "Criar projeto",
                width="stretch",
            )
        if enviar:
            try:
                db.criar_projeto(nome, descricao, CAMINHO_BANCO)
                registrar_mensagem("sucesso", "Projeto criado com sucesso.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

    projetos = db.listar_projetos(CAMINHO_BANCO)
    with coluna_sondagem:
        st.markdown("#### Planejar nova sondagem")
        if projetos.empty:
            st.warning("Crie um projeto antes de planejar a sondagem.")
        else:
            epsg = selecionar_crs("nova_sondagem", 31983)
            if st.button(
                "Capturar GPS (simula\u00e7\u00e3o)",
                help="A coordenada GPS simulada e convertida automaticamente para o CRS selecionado.",
                key="gps_nova_sondagem",
            ):
                definir_coordenadas_simuladas("nova_sondagem", epsg)
                st.rerun()
            x, y = renderizar_campos_coordenadas("nova_sondagem", epsg)

            mapa_projetos = {
                int(linha["id"]): str(linha["nome"])
                for _, linha in projetos.iterrows()
            }
            with st.form("formulario_nova_sondagem", clear_on_submit=False):
                projeto_id = st.selectbox(
                    "Projeto *",
                    options=list(mapa_projetos),
                    format_func=lambda valor: mapa_projetos[valor],
                )
                nome_furo = st.text_input(
                    "Identifica\u00e7\u00e3o do furo *",
                    placeholder="Ex.: PM-01",
                )
                campo_1, campo_2, campo_3 = st.columns(3)
                with campo_1:
                    altitude = st.number_input(
                        "Cota do terreno / altitude (m) *",
                        value=100.0,
                        step=0.1,
                        format="%.3f",
                    )
                with campo_2:
                    profundidade_planejada = st.number_input(
                        "Profundidade planejada (m) *",
                        min_value=0.1,
                        value=20.0,
                        step=0.5,
                        format="%.3f",
                    )
                with campo_3:
                    data_planejamento = st.date_input(
                        "Data de planejamento *",
                        value=date.today(),
                    )
                enviar_sondagem = st.form_submit_button(
                    "Cadastrar como planejada",
                    width="stretch",
                )

            if enviar_sondagem:
                try:
                    db.criar_sondagem(
                        projeto_id=projeto_id,
                        nome_furo=nome_furo,
                        altitude=altitude,
                        profundidade_planejada=profundidade_planejada,
                        data_sondagem=data_planejamento,
                        crs_entrada=epsg,
                        coordenada_x=x,
                        coordenada_y=y,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem(
                        "sucesso",
                        "Sondagem planejada. A pr\u00f3xima etapa \u00e9 iniciar o di\u00e1rio de campo.",
                    )
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

    st.divider()
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    st.markdown("#### Sondagens cadastradas")
    if sondagens.empty:
        st.info("Nenhuma sondagem cadastrada.")
    else:
        st.dataframe(
            tabela_sondagens_resumida(sondagens),
            width="stretch",
            hide_index=True,
        )

        with st.expander("Revisar ou trocar o sistema de coordenadas"):
            rotulos = opcoes_sondagens(sondagens)
            sondagem_id = st.selectbox(
                "Sondagem para revisar",
                options=list(rotulos),
                format_func=lambda valor: rotulos[valor],
                key="seletor_revisao_coordenadas",
            )
            sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
            if sondagem:
                prefixo = f"coord_edicao_{sondagem_id}"
                epsg_atual = int(sondagem.get("crs_entrada") or 4674)
                epsg_destino = selecionar_crs(prefixo, epsg_atual)
                chave_epsg = f"{prefixo}_epsg_anterior"
                if st.session_state.get(chave_epsg) != epsg_destino:
                    x_destino, y_destino = db.converter_de_sirgas2000(
                        sondagem["latitude"],
                        sondagem["longitude"],
                        epsg_destino,
                    )
                    st.session_state[f"{prefixo}_coord_x"] = float(x_destino)
                    st.session_state[f"{prefixo}_coord_y"] = float(y_destino)
                    st.session_state[chave_epsg] = epsg_destino
                x_novo, y_novo = renderizar_campos_coordenadas(
                    prefixo,
                    epsg_destino,
                )
                if st.button(
                    "Salvar coordenadas revisadas",
                    width="stretch",
                    key=f"salvar_coord_{sondagem_id}",
                ):
                    try:
                        db.atualizar_coordenadas_sondagem(
                            sondagem_id,
                            x_novo,
                            y_novo,
                            epsg_destino,
                            CAMINHO_BANCO,
                        )
                        registrar_mensagem(
                            "sucesso",
                            "Coordenadas e CRS atualizados com sucesso.",
                        )
                        st.rerun()
                    except db.ErroValidacao as erro:
                        st.error(str(erro))


def dataframe_intervalos_campo(
    camadas: pd.DataFrame,
) -> pd.DataFrame:
    """Prepara os intervalos para conferencia no diario de campo."""
    if camadas.empty:
        return camadas
    tabela = camadas.copy()
    tabela.insert(0, "ordem", range(1, len(tabela) + 1))
    return tabela[
        [
            "ordem",
            "profundidade_inicial",
            "profundidade_final",
            "espessura",
            "classificacao",
            "descricao_tatil_visual",
            "tipo_aquifero",
            "zona_hidrica",
            "cota_topo",
            "cota_base",
        ]
    ]


def renderizar_metricas_execucao(sondagem: dict[str, Any]) -> None:
    """Exibe status, meta, profundidade executada e saldo."""
    planejada = float(sondagem["profundidade_planejada"])
    atual = float(sondagem["profundidade_atual"])
    saldo = max(planejada - atual, 0.0)
    colunas = st.columns(5)
    colunas[0].metric("Status", rotulo_status(sondagem["status"]))
    colunas[1].metric("Planejada", f"{planejada:.2f} m")
    colunas[2].metric("Executada", f"{atual:.2f} m")
    colunas[3].metric("Saldo", f"{saldo:.2f} m")
    nivel = sondagem.get("nivel_agua_estatico")
    colunas[4].metric(
        "NA",
        "N\u00e3o medido"
        if nivel is None or pd.isna(nivel)
        else f"{float(nivel):.2f} m",
    )
    fracao = 0.0 if planejada <= 0 else min(max(atual / planejada, 0.0), 1.0)
    st.progress(fracao, text=f"Avan\u00e7o da perfura\u00e7\u00e3o: {fracao * 100:.1f}%")


def renderizar_pontos_campo(sondagem: dict[str, Any]) -> None:
    """Renderiza amostras, VOC e nivel d'agua na sequencia de campo."""
    sondagem_id = int(sondagem["id"])
    profundidade_atual = float(sondagem["profundidade_atual"] or 0)
    aba_amostras, aba_voc, aba_na = st.tabs(
        ["Amostras", "VOC", "N\u00edvel d'\u00e1gua"]
    )

    with aba_amostras:
        if profundidade_atual <= 0:
            st.info("Registre o primeiro intervalo antes de cadastrar uma amostra.")
        else:
            with st.form(f"form_coleta_{sondagem_id}", clear_on_submit=True):
                profundidade = st.number_input(
                    "Profundidade da coleta (m)",
                    min_value=0.0,
                    max_value=profundidade_atual,
                    value=profundidade_atual,
                    step=0.1,
                    format="%.3f",
                )
                enviar = st.form_submit_button(
                    "Registrar coleta",
                    width="stretch",
                )
            if enviar:
                try:
                    db.adicionar_coleta(
                        sondagem_id,
                        profundidade,
                        CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Coleta registrada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

        coletas = db.listar_coletas(sondagem_id, CAMINHO_BANCO)
        st.dataframe(coletas, width="stretch", hide_index=True)
        if not coletas.empty:
            mapa = {
                int(linha["id"]): (
                    f"ID {int(linha['id'])} - {float(linha['profundidade_coleta']):.3f} m"
                )
                for _, linha in coletas.iterrows()
            }
            remover = st.selectbox(
                "Coleta para remover",
                options=list(mapa),
                format_func=lambda valor: mapa[valor],
                key=f"remover_coleta_{sondagem_id}",
            )
            if st.button(
                "Remover coleta",
                width="stretch",
                key=f"botao_remover_coleta_{sondagem_id}",
            ):
                db.remover_coleta(remover, CAMINHO_BANCO)
                st.rerun()

    with aba_voc:
        if profundidade_atual <= 0:
            st.info("Registre o primeiro intervalo antes de cadastrar VOC.")
        else:
            with st.form(f"form_voc_{sondagem_id}", clear_on_submit=True):
                profundidade = st.number_input(
                    "Profundidade da medi\u00e7\u00e3o (m)",
                    min_value=0.0,
                    max_value=profundidade_atual,
                    value=profundidade_atual,
                    step=0.1,
                    format="%.3f",
                )
                concentracao = st.number_input(
                    "Concentra\u00e7\u00e3o (mg/L ou ppm)",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.6f",
                )
                enviar = st.form_submit_button(
                    "Registrar VOC",
                    width="stretch",
                )
            if enviar:
                try:
                    db.adicionar_voc(
                        sondagem_id,
                        profundidade,
                        concentracao,
                        CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Medi\u00e7\u00e3o de VOC registrada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

        medicoes = db.listar_voc(sondagem_id, CAMINHO_BANCO)
        st.dataframe(medicoes, width="stretch", hide_index=True)
        if not medicoes.empty:
            mapa = {
                int(linha["id"]): (
                    f"ID {int(linha['id'])} - {float(linha['profundidade']):.3f} m - "
                    f"{float(linha['concentracao']):.6g}"
                )
                for _, linha in medicoes.iterrows()
            }
            remover = st.selectbox(
                "Medi\u00e7\u00e3o para remover",
                options=list(mapa),
                format_func=lambda valor: mapa[valor],
                key=f"remover_voc_{sondagem_id}",
            )
            if st.button(
                "Remover medi\u00e7\u00e3o",
                width="stretch",
                key=f"botao_remover_voc_{sondagem_id}",
            ):
                db.remover_voc(remover, CAMINHO_BANCO)
                st.rerun()

    with aba_na:
        st.write(
            "Registre o NA depois da leitura de campo. A interpreta\u00e7\u00e3o como "
            "limite da zona vadosa exige que a leitura represente o n\u00edvel fre\u00e1tico."
        )
        if profundidade_atual <= 0:
            st.info("Inicie a perfura\u00e7\u00e3o antes de registrar o NA.")
        else:
            nivel_existente = sondagem.get("nivel_agua_estatico")
            informar = st.checkbox(
                "Informar NA observado",
                value=nivel_existente is not None and not pd.isna(nivel_existente),
                key=f"informar_na_{sondagem_id}",
            )
            nivel = None
            if informar:
                valor_padrao = (
                    float(nivel_existente)
                    if nivel_existente is not None and not pd.isna(nivel_existente)
                    else min(5.0, profundidade_atual)
                )
                nivel = st.number_input(
                    "NA - profundidade abaixo do terreno (m)",
                    min_value=0.0,
                    max_value=profundidade_atual,
                    value=valor_padrao,
                    step=0.1,
                    format="%.3f",
                    key=f"valor_na_{sondagem_id}",
                )
            if st.button(
                "Salvar leitura de NA" if informar else "Remover leitura de NA",
                width="stretch",
                key=f"salvar_na_{sondagem_id}",
            ):
                try:
                    db.atualizar_nivel_agua(
                        sondagem_id,
                        nivel if informar else None,
                        CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Leitura de NA atualizada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))


def renderizar_aba_diario_campo() -> None:
    """Renderiza o fluxo persistente de execucao da sondagem."""
    renderizar_cabecalho_etapa(
        2,
        "Di\u00e1rio de sondagem",
        "Inicie a perfura\u00e7\u00e3o, descreva intervalos na ordem encontrada e registre amostras, VOC e NA conforme o avan\u00e7o.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    if sondagens.empty:
        st.info("Planeje uma sondagem na primeira aba.")
        return

    rotulos = opcoes_sondagens(sondagens)
    sondagem_id = st.selectbox(
        "Sondagem em campo",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_diario_campo",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    if not sondagem:
        st.error("Sondagem n\u00e3o encontrada.")
        return

    renderizar_metricas_execucao(sondagem)
    status = sondagem["status"]
    if status == db.STATUS_PLANEJADA:
        st.info("Pr\u00f3xima a\u00e7\u00e3o: iniciar a sondagem e registrar o primeiro intervalo.")
        if st.button(
            "Iniciar sondagem agora",
            type="primary",
            width="stretch",
        ):
            db.iniciar_sondagem(sondagem_id, date.today(), CAMINHO_BANCO)
            registrar_mensagem("sucesso", "Sondagem iniciada.")
            st.rerun()
    elif status == db.STATUS_CONCLUIDA:
        st.success(
            "Esta sondagem est\u00e1 conclu\u00edda. Use a aba de encerramento para visualizar "
            "ou reabrir o perfil para corre\u00e7\u00e3o."
        )
        return

    with st.expander("Ajustar profundidade planejada"):
        nova_meta = st.number_input(
            "Nova profundidade planejada (m)",
            min_value=max(float(sondagem["profundidade_atual"]), 0.1),
            value=float(sondagem["profundidade_planejada"]),
            step=0.5,
            format="%.3f",
            key=f"nova_meta_{sondagem_id}",
        )
        if st.button(
            "Atualizar meta",
            width="stretch",
            key=f"atualizar_meta_{sondagem_id}",
        ):
            try:
                db.atualizar_profundidade_planejada(
                    sondagem_id,
                    nova_meta,
                    CAMINHO_BANCO,
                )
                registrar_mensagem("sucesso", "Profundidade planejada atualizada.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    rascunho = db.listar_rascunho_camadas(sondagem_id, CAMINHO_BANCO)
    inicio_esperado = (
        0.0 if rascunho.empty else float(rascunho["profundidade_final"].max())
    )
    profundidade_planejada = float(sondagem["profundidade_planejada"])

    st.markdown("#### Descri\u00e7\u00e3o do pr\u00f3ximo intervalo")
    st.caption(
        f"A profundidade inicial \u00e9 autom\u00e1tica: {inicio_esperado:.3f} m. "
        "Isso evita lacunas e sobreposi\u00e7\u00f5es durante a sondagem."
    )
    if inicio_esperado >= profundidade_planejada - db.TOLERANCIA_PROFUNDIDADE:
        st.success(
            "A profundidade planejada foi alcan\u00e7ada. Confira o di\u00e1rio e prossiga para o encerramento."
        )
    else:
        with st.form(f"form_intervalo_{sondagem_id}", clear_on_submit=True):
            col_1, col_2, col_3 = st.columns([0.8, 1.0, 1.6])
            with col_1:
                st.number_input(
                    "Profundidade inicial (m)",
                    value=inicio_esperado,
                    disabled=True,
                    format="%.3f",
                )
                profundidade_final = st.number_input(
                    "Profundidade final (m) *",
                    min_value=float(inicio_esperado + 0.001),
                    max_value=profundidade_planejada,
                    value=min(inicio_esperado + 1.0, profundidade_planejada),
                    step=0.1,
                    format="%.3f",
                )
            with col_2:
                classificacao = st.selectbox(
                    "Classifica\u00e7\u00e3o litol\u00f3gica *",
                    options=db.CLASSIFICACOES_VALIDAS,
                )
                tipo_aquifero = st.selectbox(
                    "Unidade hidroestratigr\u00e1fica *",
                    options=db.TIPOS_AQUIFERO_VALIDOS,
                )
            with col_3:
                descricao = st.text_area(
                    "Descri\u00e7\u00e3o t\u00e1til-visual *",
                    placeholder=(
                        "Ex.: silte cinza-claro, pouco pl\u00e1stico, \u00famido, "
                        "homog\u00eaneo e com areia fina dispersa."
                    ),
                    height=145,
                )
            adicionar = st.form_submit_button(
                "Registrar intervalo no di\u00e1rio",
                type="primary",
                width="stretch",
            )
        if adicionar:
            try:
                db.adicionar_intervalo_campo(
                    sondagem_id=sondagem_id,
                    profundidade_inicial=inicio_esperado,
                    profundidade_final=profundidade_final,
                    descricao_tatil_visual=descricao,
                    classificacao=classificacao,
                    tipo_aquifero=tipo_aquifero,
                    caminho_banco=CAMINHO_BANCO,
                )
                registrar_mensagem(
                    "sucesso",
                    f"Intervalo {inicio_esperado:.3f}-{profundidade_final:.3f} m registrado.",
                )
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

    rascunho = db.listar_rascunho_camadas(sondagem_id, CAMINHO_BANCO)
    st.markdown("#### Di\u00e1rio acumulado")
    if rascunho.empty:
        st.warning("Nenhum intervalo registrado.")
    else:
        st.dataframe(
            dataframe_intervalos_campo(rascunho),
            width="stretch",
            hide_index=True,
        )
        valido, erros, _ = db.validar_rascunho_parcial(
            sondagem_id,
            CAMINHO_BANCO,
        )
        if valido:
            st.success("Di\u00e1rio cont\u00ednuo e sem sobreposi\u00e7\u00e3o.")
        else:
            for erro in erros:
                st.error(erro)

        controle_1, controle_2 = st.columns(2)
        with controle_1:
            if st.button(
                "Remover \u00faltimo intervalo",
                width="stretch",
                key=f"remover_intervalo_{sondagem_id}",
            ):
                try:
                    db.remover_ultimo_intervalo_campo(
                        sondagem_id,
                        CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "\u00daltimo intervalo removido.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))
        with controle_2:
            confirmar_limpeza = st.checkbox(
                "Confirmar limpeza integral",
                key=f"confirmar_limpeza_{sondagem_id}",
            )
            if st.button(
                "Limpar di\u00e1rio",
                width="stretch",
                disabled=not confirmar_limpeza,
                key=f"limpar_diario_{sondagem_id}",
            ):
                try:
                    db.limpar_rascunho_camadas(sondagem_id, CAMINHO_BANCO)
                    registrar_mensagem("sucesso", "Di\u00e1rio limpo.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

    st.divider()
    st.markdown("#### Registros associados ao avan\u00e7o")
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    renderizar_pontos_campo(sondagem)


def montar_exportacao_perfil(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    coletas: pd.DataFrame,
    voc: pd.DataFrame,
) -> pd.DataFrame:
    """Consolida cabecalho, camadas, amostras e VOC para exportacao."""
    base = {
        "projeto": sondagem["projeto_nome"],
        "sondagem": sondagem["nome_furo"],
        "status": rotulo_status(sondagem["status"]),
        "crs_epsg": sondagem["crs_entrada"],
        "coordenada_x": sondagem["coordenada_x"],
        "coordenada_y": sondagem["coordenada_y"],
        "latitude_sirgas2000": sondagem["latitude"],
        "longitude_sirgas2000": sondagem["longitude"],
        "altitude_m": sondagem["altitude"],
        "profundidade_total_m": sondagem["profundidade_total"],
        "nivel_agua_m": sondagem["nivel_agua_estatico"],
    }
    registros: list[dict[str, Any]] = []
    for _, camada in camadas.iterrows():
        registros.append(
            {
                **base,
                "tipo_registro": "CAMADA",
                "profundidade_inicial_m": camada["profundidade_inicial"],
                "profundidade_final_m": camada["profundidade_final"],
                "profundidade_pontual_m": None,
                "descricao": camada["descricao_tatil_visual"],
                "classificacao": camada["classificacao"],
                "tipo_aquifero": camada["tipo_aquifero"],
                "zona_hidrica": camada.get("zona_hidrica"),
                "cota_topo_m": camada["cota_topo"],
                "cota_base_m": camada["cota_base"],
                "concentracao_voc": None,
            }
        )
    for _, coleta in coletas.iterrows():
        registros.append(
            {
                **base,
                "tipo_registro": "COLETA",
                "profundidade_inicial_m": None,
                "profundidade_final_m": None,
                "profundidade_pontual_m": coleta["profundidade_coleta"],
                "descricao": "Ponto de coleta",
                "classificacao": None,
                "tipo_aquifero": None,
                "zona_hidrica": None,
                "cota_topo_m": None,
                "cota_base_m": None,
                "concentracao_voc": None,
            }
        )
    for _, medicao in voc.iterrows():
        registros.append(
            {
                **base,
                "tipo_registro": "VOC",
                "profundidade_inicial_m": None,
                "profundidade_final_m": None,
                "profundidade_pontual_m": medicao["profundidade"],
                "descricao": "Medi\u00e7\u00e3o de VOC",
                "classificacao": None,
                "tipo_aquifero": None,
                "zona_hidrica": None,
                "cota_topo_m": None,
                "cota_base_m": None,
                "concentracao_voc": medicao["concentracao"],
            }
        )
    return pd.DataFrame(registros)


def renderizar_figura_e_exportacoes(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    titulo_previa: bool = False,
) -> None:
    """Exibe o perfil e gera PNG/CSV com tabela de descricoes."""
    sondagem_id = int(sondagem["id"])
    coletas = db.listar_coletas(sondagem_id, CAMINHO_BANCO)
    voc = db.listar_voc(sondagem_id, CAMINHO_BANCO)
    if titulo_previa:
        st.info(
            "Pr\u00e9via do di\u00e1rio de campo. O perfil final s\u00f3 \u00e9 publicado ap\u00f3s o encerramento."
        )
    figura = viz.criar_perfil_litologico(sondagem, camadas, coletas, voc)
    st.plotly_chart(
        figura,
        width="stretch",
        config={"displaylogo": False},
    )
    st.caption(
        "As descri\u00e7\u00f5es completas aparecem em uma tabela abaixo do gr\u00e1fico. "
        "A legenda foi deslocada para a lateral para n\u00e3o sobrepor o t\u00edtulo na imagem PNG."
    )

    coluna_png, coluna_csv = st.columns(2)
    with coluna_png:
        try:
            altura = int(figura.layout.height or 1300)
            imagem = figura.to_image(
                format="png",
                width=2100,
                height=min(max(altura, 1100), 5000),
                scale=2,
            )
            st.download_button(
                "Baixar perfil em PNG",
                data=imagem,
                file_name=f"perfil_{sondagem['nome_furo']}.png",
                mime="image/png",
                width="stretch",
                key=f"download_png_{sondagem_id}_{titulo_previa}",
            )
        except Exception as erro:
            st.warning(
                "A exporta\u00e7\u00e3o PNG requer o Kaleido funcional no ambiente. "
                f"Detalhe: {erro}"
            )
    with coluna_csv:
        exportacao = montar_exportacao_perfil(sondagem, camadas, coletas, voc)
        st.download_button(
            "Baixar dados em CSV",
            data=exportacao.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"dados_{sondagem['nome_furo']}.csv",
            mime="text/csv",
            width="stretch",
            key=f"download_csv_{sondagem_id}_{titulo_previa}",
        )


def renderizar_aba_encerramento() -> None:
    """Renderiza checklist, conclusao, perfil e reabertura."""
    renderizar_cabecalho_etapa(
        3,
        "Confer\u00eancia, encerramento e perfil",
        "Defina a profundidade final, confirme o NA, publique o perfil completo e gere a imagem leg\u00edvel para o relat\u00f3rio.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    if sondagens.empty:
        st.info("Nenhuma sondagem cadastrada.")
        return
    rotulos = opcoes_sondagens(sondagens)
    sondagem_id = st.selectbox(
        "Sondagem para confer\u00eancia",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_encerramento",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    if not sondagem:
        return
    renderizar_metricas_execucao(sondagem)

    if sondagem["status"] != db.STATUS_CONCLUIDA:
        rascunho = db.listar_rascunho_camadas(sondagem_id, CAMINHO_BANCO)
        if rascunho.empty:
            st.warning("O di\u00e1rio ainda n\u00e3o possui intervalos para encerrar.")
            return

        st.markdown("#### Checklist de encerramento")
        profundidade_atual = float(sondagem["profundidade_atual"])
        col_1, col_2, col_3 = st.columns(3)
        with col_1:
            profundidade_final = st.number_input(
                "Profundidade final executada (m) *",
                min_value=0.001,
                value=profundidade_atual,
                step=0.1,
                format="%.3f",
                key=f"prof_final_{sondagem_id}",
            )
        nivel_existente = sondagem.get("nivel_agua_estatico")
        with col_2:
            informar_na = st.checkbox(
                "Informar NA est\u00e1tico",
                value=nivel_existente is not None and not pd.isna(nivel_existente),
                key=f"encerrar_informar_na_{sondagem_id}",
            )
            nivel_final = None
            if informar_na:
                nivel_final = st.number_input(
                    "NA abaixo do terreno (m)",
                    min_value=0.0,
                    max_value=float(profundidade_final),
                    value=(
                        float(nivel_existente)
                        if nivel_existente is not None and not pd.isna(nivel_existente)
                        else min(5.0, float(profundidade_final))
                    ),
                    step=0.1,
                    format="%.3f",
                    key=f"encerrar_na_{sondagem_id}",
                )
        with col_3:
            data_conclusao = st.date_input(
                "Data de conclus\u00e3o *",
                value=date.today(),
                key=f"data_conclusao_{sondagem_id}",
            )

        valido, erros, _ = db.validar_rascunho_para_conclusao(
            sondagem_id,
            profundidade_final,
            CAMINHO_BANCO,
        )
        if informar_na and nivel_final is not None and nivel_final > profundidade_final:
            valido = False
            erros.append("O NA ultrapassa a profundidade final.")
        if valido:
            st.success(
                "Perfil cont\u00ednuo, sem sobreposi\u00e7\u00e3o e cobrindo exatamente a profundidade final."
            )
        else:
            for erro in erros:
                st.error(erro)

        if st.button(
            "Concluir e publicar perfil",
            type="primary",
            width="stretch",
            disabled=not valido,
            key=f"concluir_{sondagem_id}",
        ):
            try:
                db.finalizar_sondagem(
                    sondagem_id=sondagem_id,
                    profundidade_final=profundidade_final,
                    nivel_agua_estatico=nivel_final if informar_na else None,
                    data_conclusao=data_conclusao,
                    caminho_banco=CAMINHO_BANCO,
                )
                registrar_mensagem(
                    "sucesso",
                    "Sondagem conclu\u00edda e perfil final publicado.",
                )
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

        sondagem_previa = dict(sondagem)
        sondagem_previa["profundidade_total"] = max(
            float(profundidade_final),
            float(rascunho["profundidade_final"].max()),
        )
        sondagem_previa["nivel_agua_estatico"] = (
            nivel_final if informar_na else None
        )
        sondagem_previa["status"] = "Previa de campo"
        st.divider()
        renderizar_figura_e_exportacoes(
            sondagem_previa,
            rascunho,
            titulo_previa=True,
        )
    else:
        camadas = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
        if camadas.empty:
            st.warning("A sondagem est\u00e1 conclu\u00edda, mas n\u00e3o possui camadas finais.")
            return
        renderizar_figura_e_exportacoes(sondagem, camadas)
        st.divider()
        with st.expander("Reabrir perfil para corre\u00e7\u00e3o"):
            st.warning(
                "A reabertura copia o perfil final para o di\u00e1rio de campo. "
                "O perfil publicado atual permanece dispon\u00edvel at\u00e9 uma nova conclus\u00e3o."
            )
            confirmar = st.checkbox(
                "Confirmo a reabertura",
                key=f"confirmar_reabertura_{sondagem_id}",
            )
            if st.button(
                "Reabrir sondagem",
                width="stretch",
                disabled=not confirmar,
                key=f"reabrir_{sondagem_id}",
            ):
                try:
                    db.reabrir_sondagem(sondagem_id, CAMINHO_BANCO)
                    registrar_mensagem(
                        "sucesso",
                        "Sondagem reaberta para corre\u00e7\u00e3o no di\u00e1rio de campo.",
                    )
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))


def renderizar_aba_mapa_secao() -> None:
    """Renderiza mapa, tabela espacial e secao transversal."""
    renderizar_cabecalho_etapa(
        4,
        "Mapa e correla\u00e7\u00e3o entre furos",
        "Confira a loca\u00e7\u00e3o em mapa e gere a se\u00e7\u00e3o somente com sondagens conclu\u00eddas.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    mapa = viz.criar_mapa_sondagens(sondagens)
    st.iframe(
        mapa.get_root().render(),
        width="stretch",
        height=540,
    )
    st.caption(
        "O Folium exibe o mapa em Web Mercator. Todas as entradas s\u00e3o transformadas "
        "internamente para SIRGAS 2000 geogr\u00e1fico (EPSG:4674), mantendo tamb\u00e9m o CRS original."
    )
    if not sondagens.empty:
        st.dataframe(
            tabela_sondagens_resumida(sondagens),
            width="stretch",
            hide_index=True,
        )

    st.divider()
    st.markdown("#### Se\u00e7\u00e3o hidroestratigr\u00e1fica")
    concluidas = sondagens[sondagens["status"] == db.STATUS_CONCLUIDA].copy()
    if len(concluidas) < 2:
        st.info("Conclua pelo menos duas sondagens para gerar a se\u00e7\u00e3o.")
        return
    rotulos = opcoes_sondagens(concluidas)
    ids = st.multiselect(
        "Selecione os furos na ordem do eixo da se\u00e7\u00e3o",
        options=list(rotulos),
        default=list(rotulos)[:2],
        format_func=lambda valor: rotulos[valor],
    )
    if len(ids) < 2:
        st.warning("Selecione pelo menos duas sondagens.")
        return

    dados: list[dict[str, Any]] = []
    for sondagem_id in ids:
        sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
        camadas = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
        if not sondagem or camadas.empty:
            st.error(f"A sondagem {rotulos[sondagem_id]} n\u00e3o possui perfil final.")
            return
        dados.append({"sondagem": sondagem, "camadas": camadas})

    try:
        distancias = viz.calcular_distancias_acumuladas(
            [item["sondagem"] for item in dados]
        )
        tabela = pd.DataFrame(
            {
                "ordem": range(1, len(dados) + 1),
                "sondagem": [item["sondagem"]["nome_furo"] for item in dados],
                "EPSG": [item["sondagem"]["crs_entrada"] for item in dados],
                "X": [item["sondagem"]["coordenada_x"] for item in dados],
                "Y": [item["sondagem"]["coordenada_y"] for item in dados],
                "distancia_acumulada_m": distancias,
                "altitude_m": [item["sondagem"]["altitude"] for item in dados],
                "cota_na_m": [item["sondagem"]["cota_nivel_agua"] for item in dados],
            }
        )
        st.dataframe(tabela, width="stretch", hide_index=True)
        figura = viz.criar_secao_hidroestratigrafica(dados)
        st.plotly_chart(
            figura,
            width="stretch",
            config={"displaylogo": False},
        )
        try:
            imagem = figura.to_image(
                format="png",
                width=2100,
                height=1100,
                scale=2,
            )
            st.download_button(
                "Baixar se\u00e7\u00e3o em PNG",
                data=imagem,
                file_name="secao_hidroestratigrafica.png",
                mime="image/png",
            )
        except Exception as erro:
            st.caption(f"Exporta\u00e7\u00e3o PNG indispon\u00edvel: {erro}")
    except Exception as erro:
        st.error(f"N\u00e3o foi poss\u00edvel gerar a se\u00e7\u00e3o: {erro}")


def renderizar_aba_importacao() -> None:
    """Renderiza importacao CSV com dois esquemas de coordenadas."""
    renderizar_cabecalho_etapa(
        5,
        "Importa\u00e7\u00e3o e interc\u00e2mbio",
        "Importe perfis conclu\u00eddos em SIRGAS 2000 / UTM 23S ou em outro CRS identificado pelo c\u00f3digo EPSG.",
    )
    st.write("Colunas litol\u00f3gicas obrigat\u00f3rias:")
    st.code(",".join(db.COLUNAS_CSV_BASE), language="text")
    st.write("Escolha um dos esquemas de coordenadas:")
    st.code(
        "crs_epsg,coordenada_x,coordenada_y\nOU\nlatitude,longitude",
        language="text",
    )
    st.caption(
        "No segundo esquema, latitude/longitude s\u00e3o interpretadas como SIRGAS 2000 geogr\u00e1fico (EPSG:4674)."
    )

    if CAMINHO_EXEMPLO.exists():
        coluna_download, coluna_demo = st.columns(2)
        with coluna_download:
            st.download_button(
                "Baixar exemplo.csv em EPSG:31983",
                data=CAMINHO_EXEMPLO.read_bytes(),
                file_name="exemplo.csv",
                mime="text/csv",
                width="stretch",
            )
        with coluna_demo:
            if st.button(
                "Carregar dados de demonstra\u00e7\u00e3o",
                width="stretch",
            ):
                try:
                    dados = pd.read_csv(CAMINHO_EXEMPLO, encoding="utf-8-sig")
                    st.session_state["relatorio_importacao"] = db.importar_dataframe(
                        dados,
                        CAMINHO_BANCO,
                    )
                    st.success("Dados de demonstra\u00e7\u00e3o processados.")
                except Exception as erro:
                    st.error(str(erro))

    arquivo = st.file_uploader(
        "Selecione o CSV",
        type=["csv"],
        accept_multiple_files=False,
    )
    if arquivo is not None:
        try:
            dataframe = pd.read_csv(
                io.BytesIO(arquivo.getvalue()),
                sep=None,
                engine="python",
                encoding="utf-8-sig",
            )
            st.dataframe(dataframe.head(100), width="stretch", hide_index=True)
            if st.button(
                "Validar e importar",
                type="primary",
                width="stretch",
            ):
                st.session_state["relatorio_importacao"] = db.importar_dataframe(
                    dataframe,
                    CAMINHO_BANCO,
                )
        except Exception as erro:
            st.error(f"Falha na leitura do CSV: {erro}")

    relatorio = st.session_state.get("relatorio_importacao")
    if isinstance(relatorio, pd.DataFrame) and not relatorio.empty:
        sucessos = int((relatorio["status"] == "Sucesso").sum())
        erros = int((relatorio["status"] == "Erro").sum())
        col_1, col_2, col_3 = st.columns(3)
        col_1.metric("Sondagens processadas", len(relatorio))
        col_2.metric("Sucessos", sucessos)
        col_3.metric("Erros", erros)
        st.dataframe(relatorio, width="stretch", hide_index=True)
        st.download_button(
            "Baixar relat\u00f3rio em CSV",
            data=relatorio.to_csv(index=False).encode("utf-8-sig"),
            file_name="relatorio_importacao.csv",
            mime="text/csv",
        )


def executar_aplicativo() -> None:
    """Monta a interface completa no fluxo real de uma sondagem."""
    st.title("\U0001f4a7 Di\u00e1rio de Sondagem Hidrogeol\u00f3gica")
    st.caption(
        "Fluxo de campo, descri\u00e7\u00e3o litol\u00f3gica, zona vadosa, CRS configur\u00e1vel, "
        "mapa, perfil individual e se\u00e7\u00e3o transversal."
    )
    exibir_mensagem_pendente()

    with st.sidebar:
        st.header("Fluxo recomendado")
        st.markdown(
            "1. Projeto e loca\u00e7\u00e3o\n\n"
            "2. In\u00edcio e avan\u00e7o da perfura\u00e7\u00e3o\n\n"
            "3. Intervalos, amostras, VOC e NA\n\n"
            "4. Confer\u00eancia e encerramento\n\n"
            "5. Mapa e correla\u00e7\u00e3o"
        )
        st.info(
            "CRS padr\u00e3o de entrada: **SIRGAS 2000 / UTM zona 23S (EPSG:31983)**. "
            "Outras zonas UTM, EPSG:4674, GPS/WGS84 e c\u00f3digos EPSG personalizados s\u00e3o aceitos."
        )
        renderizar_controles_banco()
        st.caption(
            "A simbologia gr\u00e1fica deve ser revisada pelo respons\u00e1vel t\u00e9cnico "
            "antes da emiss\u00e3o de documento oficial."
        )

    aba_1, aba_2, aba_3, aba_4, aba_5 = st.tabs(
        [
            "1. Projeto e Loca\u00e7\u00e3o",
            "2. Di\u00e1rio de Sondagem",
            "3. Encerramento e Perfil",
            "4. Mapa e Se\u00e7\u00e3o",
            "5. Importa\u00e7\u00e3o",
        ]
    )
    with aba_1:
        renderizar_aba_locacao()
    with aba_2:
        renderizar_aba_diario_campo()
    with aba_3:
        renderizar_aba_encerramento()
    with aba_4:
        renderizar_aba_mapa_secao()
    with aba_5:
        renderizar_aba_importacao()


if __name__ == "__main__":
    executar_aplicativo()
