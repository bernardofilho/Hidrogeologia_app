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
    page_title="Descrição Litológica de Sondagens",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

DIRETORIO_APLICACAO = Path(__file__).resolve().parent
CAMINHO_EXEMPLO = DIRETORIO_APLICACAO / "exemplo.csv"
MODO_BANCO = os.getenv("HIDRO_DB_MODE", "local").strip().lower()


def resolver_caminho_banco() -> Path:
    """Define um banco persistente local ou um banco privado por sessão web."""
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
        .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
        .stMetric {border: 1px solid #E5E7E9; border-radius: 0.6rem; padding: 0.7rem;}
        .nota-tecnica {background: #F4F6F7; border-left: 4px solid #2471A3;
                       padding: 0.8rem 1rem; border-radius: 0.25rem;}
    </style>
    """,
    unsafe_allow_html=True,
)



def limpar_estado_dependente_do_banco() -> None:
    """Remove rascunhos e relatórios que pertenciam ao banco anterior."""
    prefixos = ("rascunho_camadas_",)
    chaves_exatas = {"relatorio_importacao"}
    for chave in list(st.session_state):
        if chave in chaves_exatas or chave.startswith(prefixos):
            st.session_state.pop(chave, None)


def renderizar_controles_banco() -> None:
    """Exibe informações, backup e restauração do banco no painel lateral."""
    resumo = db.obter_resumo_banco(CAMINHO_BANCO)
    modo_texto = "Privado por sessão" if MODO_BANCO == "session" else "Compartilhado"

    st.write(f"Modo do banco: **{modo_texto}**")
    st.caption(
        f"{resumo['projetos']} projeto(s), {resumo['sondagens']} sondagem(ns) e "
        f"{resumo['camadas']} camada(s)."
    )

    if MODO_BANCO == "session":
        st.warning(
            "Nesta implantação web, os dados ficam isolados nesta sessão do navegador. "
            "Baixe um backup antes de fechar ou reiniciar a sessão."
        )
    else:
        st.caption(f"Arquivo ativo: {CAMINHO_BANCO}")

    try:
        backup = db.exportar_banco_bytes(CAMINHO_BANCO)
        st.download_button(
            "⬇️ Baixar backup SQLite",
            data=backup,
            file_name="hidrogeologia_backup.db",
            mime="application/vnd.sqlite3",
            use_container_width=True,
            key="baixar_backup_banco",
        )
    except Exception as erro:
        st.error(f"Não foi possível preparar o backup: {erro}")

    arquivo_backup = st.file_uploader(
        "Restaurar backup SQLite",
        type=["db", "sqlite", "sqlite3"],
        accept_multiple_files=False,
        key="arquivo_restauracao_banco",
        help="A restauração substitui os dados atualmente abertos nesta sessão.",
    )
    confirmar = st.checkbox(
        "Confirmo a substituição do banco atual",
        key="confirmar_restauracao_banco",
        disabled=arquivo_backup is None,
    )
    if st.button(
        "Restaurar banco",
        use_container_width=True,
        disabled=arquivo_backup is None or not confirmar,
        key="botao_restaurar_banco",
    ):
        try:
            db.restaurar_banco_bytes(arquivo_backup.getvalue(), CAMINHO_BANCO)
            limpar_estado_dependente_do_banco()
            registrar_mensagem("sucesso", "Backup restaurado com sucesso.")
            st.rerun()
        except db.ErroValidacao as erro:
            st.error(str(erro))
        except Exception as erro:
            st.error(f"Falha inesperada na restauração: {erro}")


def registrar_mensagem(tipo: str, texto: str) -> None:
    """Guarda uma mensagem curta para apresentação após a próxima execução."""
    st.session_state["mensagem_pendente"] = (tipo, texto)


def exibir_mensagem_pendente() -> None:
    """Exibe e remove a mensagem pendente da sessão."""
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


def opcoes_sondagens(sondagens: pd.DataFrame) -> dict[int, str]:
    """Monta rótulos únicos para seletores de sondagem."""
    return {
        int(linha["id"]): (
            f"{linha['projeto_nome']} | {linha['nome_furo']} | ID {int(linha['id'])}"
        )
        for _, linha in sondagens.iterrows()
    }


def obter_rascunho_camadas(sondagem_id: int) -> list[dict[str, Any]]:
    """Recupera ou inicializa o rascunho litológico da sondagem."""
    chave = f"rascunho_camadas_{sondagem_id}"
    if chave not in st.session_state:
        existentes = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
        st.session_state[chave] = [
            {
                "profundidade_inicial": float(linha["profundidade_inicial"]),
                "profundidade_final": float(linha["profundidade_final"]),
                "descricao_tatil_visual": str(linha["descricao_tatil_visual"]),
                "classificacao": str(linha["classificacao"]),
                "tipo_aquifero": str(linha["tipo_aquifero"]),
            }
            for _, linha in existentes.iterrows()
        ]
    return st.session_state[chave]


def recarregar_rascunho_camadas(sondagem_id: int) -> None:
    """Descarta o rascunho e recarrega o perfil persistido."""
    chave = f"rascunho_camadas_{sondagem_id}"
    st.session_state.pop(chave, None)
    obter_rascunho_camadas(sondagem_id)


def dataframe_rascunho(
    rascunho: list[dict[str, Any]], altitude: float
) -> pd.DataFrame:
    """Cria uma tabela de pré-visualização com espessuras e cotas calculadas."""
    registros: list[dict[str, Any]] = []
    for indice, camada in enumerate(rascunho, start=1):
        inicio = float(camada["profundidade_inicial"])
        final = float(camada["profundidade_final"])
        registros.append(
            {
                "ordem": indice,
                "profundidade_inicial": inicio,
                "profundidade_final": final,
                "espessura": final - inicio,
                "cota_topo": float(altitude) - inicio,
                "cota_base": float(altitude) - final,
                "classificacao": camada["classificacao"],
                "tipo_aquifero": camada["tipo_aquifero"],
                "descricao_tatil_visual": camada["descricao_tatil_visual"],
            }
        )
    return pd.DataFrame(registros)


def montar_exportacao_perfil(
    sondagem: dict[str, Any],
    camadas: pd.DataFrame,
    coletas: pd.DataFrame,
    voc: pd.DataFrame,
) -> pd.DataFrame:
    """Consolida camadas, coletas e VOC em um único arquivo tabular."""
    base = {
        "projeto": sondagem["projeto_nome"],
        "sondagem": sondagem["nome_furo"],
        "latitude_sirgas2000": sondagem["latitude"],
        "longitude_sirgas2000": sondagem["longitude"],
        "altitude_m": sondagem["altitude"],
        "profundidade_total_m": sondagem["profundidade_total"],
        "nivel_agua_m": sondagem["nivel_agua_estatico"],
        "data": sondagem["data"],
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
                "descricao": "Ponto de coleta de amostra",
                "classificacao": None,
                "tipo_aquifero": None,
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
                "descricao": "Medição de VOC",
                "classificacao": None,
                "tipo_aquifero": None,
                "cota_topo_m": None,
                "cota_base_m": None,
                "concentracao_voc": medicao["concentracao"],
            }
        )

    return pd.DataFrame(registros)


def renderizar_aba_projetos_mapa() -> None:
    """Renderiza cadastro de projetos, sondagens e mapa geral."""
    st.subheader("Projetos e georreferenciamento")
    coluna_projeto, coluna_sondagem = st.columns([0.85, 1.55], gap="large")

    with coluna_projeto:
        st.markdown("#### Novo projeto")
        with st.form("formulario_novo_projeto", clear_on_submit=True):
            nome_projeto = st.text_input("Nome do projeto *")
            descricao_projeto = st.text_area("Descrição", height=110)
            enviar_projeto = st.form_submit_button(
                "Criar projeto", use_container_width=True
            )
        if enviar_projeto:
            try:
                db.criar_projeto(
                    nome_projeto,
                    descricao_projeto,
                    CAMINHO_BANCO,
                )
                registrar_mensagem("sucesso", "Projeto criado com sucesso.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

    projetos = db.listar_projetos(CAMINHO_BANCO)
    with coluna_sondagem:
        st.markdown("#### Nova sondagem")
        st.session_state.setdefault("nova_latitude", -15.793889)
        st.session_state.setdefault("nova_longitude", -47.882778)

        if st.button(
            "📍 Capturar GPS (simulação)",
            help="Usa coordenadas fixas para teste. Substitua a atribuição por uma integração de geolocalização no ambiente de produção.",
        ):
            st.session_state["nova_latitude"] = -15.793889
            st.session_state["nova_longitude"] = -47.882778
            st.info(
                "Coordenadas simuladas capturadas: latitude -15.793889, longitude -47.882778."
            )

        if projetos.empty:
            st.warning("Crie um projeto antes de cadastrar a primeira sondagem.")
        else:
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
                nome_furo = st.text_input("Nome do furo *", placeholder="Ex.: PZ-01")
                campo_1, campo_2 = st.columns(2)
                with campo_1:
                    latitude = st.number_input(
                        "Latitude SIRGAS 2000 (graus decimais) *",
                        min_value=-90.0,
                        max_value=90.0,
                        value=float(st.session_state["nova_latitude"]),
                        format="%.8f",
                    )
                    altitude = st.number_input(
                        "Altitude (m) *",
                        value=100.0,
                        format="%.3f",
                    )
                    profundidade_total = st.number_input(
                        "Profundidade total (m) *",
                        min_value=0.01,
                        value=30.0,
                        step=0.5,
                        format="%.3f",
                    )
                with campo_2:
                    longitude = st.number_input(
                        "Longitude SIRGAS 2000 (graus decimais) *",
                        min_value=-180.0,
                        max_value=180.0,
                        value=float(st.session_state["nova_longitude"]),
                        format="%.8f",
                    )
                    informar_na = st.checkbox("Informar nível d'água estático")
                    nivel_agua = None
                    if informar_na:
                        nivel_agua = st.number_input(
                            "NA — profundidade abaixo do terreno (m)",
                            min_value=0.0,
                            max_value=float(profundidade_total),
                            value=min(5.0, float(profundidade_total)),
                            step=0.1,
                            format="%.3f",
                        )
                    data_sondagem = st.date_input("Data *", value=date.today())
                enviar_sondagem = st.form_submit_button(
                    "Cadastrar sondagem", use_container_width=True
                )

            if enviar_sondagem:
                try:
                    db.criar_sondagem(
                        projeto_id=projeto_id,
                        nome_furo=nome_furo,
                        latitude=latitude,
                        longitude=longitude,
                        altitude=altitude,
                        profundidade_total=profundidade_total,
                        nivel_agua_estatico=nivel_agua,
                        data_sondagem=data_sondagem,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Sondagem cadastrada com sucesso.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

    st.divider()
    st.markdown("#### Mapa das sondagens")
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    mapa = viz.criar_mapa_sondagens(sondagens)
    st.iframe(
        mapa.get_root().render(),
        width="stretch",
        height=520,
    )
    st.caption(
        "O Folium exibe o mapa em WGS84/Web Mercator. Para este MVP, as coordenadas SIRGAS 2000 são armazenadas em graus decimais e usadas diretamente na visualização."
    )

    if not sondagens.empty:
        tabela = sondagens[
            [
                "projeto_nome",
                "nome_furo",
                "latitude",
                "longitude",
                "altitude",
                "profundidade_total",
                "nivel_agua_estatico",
                "data",
            ]
        ].rename(
            columns={
                "projeto_nome": "projeto",
                "nome_furo": "sondagem",
                "nivel_agua_estatico": "NA_m",
            }
        )
        st.dataframe(tabela, use_container_width=True, hide_index=True)


def renderizar_aba_cadastro_litologico() -> None:
    """Renderiza o editor transacional de camadas, coletas e VOC."""
    st.subheader("Cadastro litológico, coletas e VOC")
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    if sondagens.empty:
        st.info("Cadastre uma sondagem na primeira aba.")
        return

    rotulos = opcoes_sondagens(sondagens)
    sondagem_id = st.selectbox(
        "Sondagem",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_sondagem_cadastro",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    if not sondagem:
        st.error("Sondagem não encontrada.")
        return

    metrica_1, metrica_2, metrica_3, metrica_4 = st.columns(4)
    metrica_1.metric("Altitude", f"{sondagem['altitude']:.2f} m")
    metrica_2.metric("Profundidade total", f"{sondagem['profundidade_total']:.2f} m")
    texto_na = (
        "Não informado"
        if sondagem["nivel_agua_estatico"] is None
        else f"{sondagem['nivel_agua_estatico']:.2f} m"
    )
    metrica_3.metric("NA", texto_na)
    metrica_4.metric("Datum", "SIRGAS 2000")

    st.markdown(
        '<div class="nota-tecnica">As camadas são montadas em um rascunho. O banco só é alterado ao salvar um perfil completo, contínuo, sem sobreposição e cobrindo exatamente a profundidade total.</div>',
        unsafe_allow_html=True,
    )

    rascunho = obter_rascunho_camadas(sondagem_id)
    profundidade_total = float(sondagem["profundidade_total"])
    altitude = float(sondagem["altitude"])
    profundidade_esperada = (
        float(rascunho[-1]["profundidade_final"]) if rascunho else 0.0
    )

    st.markdown("#### Montagem do perfil")
    if profundidade_esperada < profundidade_total - db.TOLERANCIA_PROFUNDIDADE:
        indice_novo = len(rascunho)
        with st.form(f"formulario_camada_{sondagem_id}_{indice_novo}"):
            coluna_1, coluna_2, coluna_3 = st.columns([1, 1, 1.2])
            with coluna_1:
                profundidade_inicial = st.number_input(
                    "Profundidade inicial (m)",
                    min_value=0.0,
                    max_value=profundidade_total,
                    value=profundidade_esperada,
                    step=0.1,
                    format="%.3f",
                    key=f"inicio_{sondagem_id}_{indice_novo}",
                )
                profundidade_final = st.number_input(
                    "Profundidade final (m)",
                    min_value=0.0,
                    max_value=profundidade_total,
                    value=min(profundidade_esperada + 1.0, profundidade_total),
                    step=0.1,
                    format="%.3f",
                    key=f"final_{sondagem_id}_{indice_novo}",
                )
            with coluna_2:
                classificacao = st.selectbox(
                    "Classificação",
                    options=db.CLASSIFICACOES_VALIDAS,
                    key=f"classificacao_{sondagem_id}_{indice_novo}",
                )
                tipo_aquifero = st.selectbox(
                    "Tipo de aquífero",
                    options=db.TIPOS_AQUIFERO_VALIDOS,
                    key=f"aquifero_{sondagem_id}_{indice_novo}",
                )
            with coluna_3:
                descricao = st.text_area(
                    "Descrição tátil-visual",
                    placeholder="Ex.: argila marrom, plástica, úmida, com baixa presença de areia.",
                    height=130,
                    key=f"descricao_{sondagem_id}_{indice_novo}",
                )
            adicionar = st.form_submit_button(
                "Adicionar camada ao rascunho", use_container_width=True
            )

        if adicionar:
            if abs(profundidade_inicial - profundidade_esperada) > db.TOLERANCIA_PROFUNDIDADE:
                st.error(
                    f"A nova camada deve começar em {profundidade_esperada:.3f} m para manter a continuidade."
                )
            elif profundidade_inicial < 0:
                st.error("Profundidades negativas não são permitidas.")
            elif profundidade_final <= profundidade_inicial:
                st.error("A profundidade final deve ser maior que a inicial.")
            elif not descricao.strip():
                st.error("Informe a descrição tátil-visual.")
            else:
                rascunho.append(
                    {
                        "profundidade_inicial": float(profundidade_inicial),
                        "profundidade_final": float(profundidade_final),
                        "descricao_tatil_visual": descricao.strip(),
                        "classificacao": classificacao,
                        "tipo_aquifero": tipo_aquifero,
                    }
                )
                st.success("Camada adicionada ao rascunho.")
    else:
        st.info(
            "O rascunho já alcança a profundidade total. Remova uma camada ou limpe o rascunho para alterar a estrutura."
        )

    tabela_rascunho = dataframe_rascunho(rascunho, altitude)
    if tabela_rascunho.empty:
        st.warning("O rascunho ainda não possui camadas.")
    else:
        st.dataframe(
            tabela_rascunho,
            use_container_width=True,
            hide_index=True,
            column_config={
                "profundidade_inicial": st.column_config.NumberColumn(format="%.3f m"),
                "profundidade_final": st.column_config.NumberColumn(format="%.3f m"),
                "espessura": st.column_config.NumberColumn(format="%.3f m"),
                "cota_topo": st.column_config.NumberColumn(format="%.3f m"),
                "cota_base": st.column_config.NumberColumn(format="%.3f m"),
            },
        )

    soma_espessuras = (
        float(tabela_rascunho["espessura"].sum())
        if not tabela_rascunho.empty
        else 0.0
    )
    diferenca = profundidade_total - soma_espessuras
    coluna_status_1, coluna_status_2, coluna_status_3 = st.columns(3)
    coluna_status_1.metric("Soma das espessuras", f"{soma_espessuras:.3f} m")
    coluna_status_2.metric("Profundidade-alvo", f"{profundidade_total:.3f} m")
    coluna_status_3.metric("Diferença", f"{diferenca:.3f} m")

    valido, erros, _ = db.validar_perfil_litologico(
        sondagem_id,
        rascunho,
        CAMINHO_BANCO,
    )
    if valido:
        st.success("Perfil válido e pronto para gravação.")
    elif erros:
        with st.expander("Pendências de validação", expanded=False):
            for erro in erros:
                st.write(f"• {erro}")

    controle_1, controle_2, controle_3, controle_4 = st.columns(4)
    with controle_1:
        if st.button(
            "Salvar perfil completo",
            type="primary",
            use_container_width=True,
            disabled=not valido,
        ):
            try:
                db.salvar_perfil_litologico(
                    sondagem_id,
                    rascunho,
                    CAMINHO_BANCO,
                )
                recarregar_rascunho_camadas(sondagem_id)
                registrar_mensagem("sucesso", "Perfil litológico salvo com sucesso.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))
    with controle_2:
        if st.button(
            "Remover última camada",
            use_container_width=True,
            disabled=not bool(rascunho),
        ):
            rascunho.pop()
            st.rerun()
    with controle_3:
        if st.button(
            "Limpar rascunho",
            use_container_width=True,
            disabled=not bool(rascunho),
        ):
            rascunho.clear()
            st.rerun()
    with controle_4:
        if st.button("Recarregar perfil salvo", use_container_width=True):
            recarregar_rascunho_camadas(sondagem_id)
            st.rerun()

    st.divider()
    st.markdown("#### Pontos de coleta e medições de VOC")
    coluna_coleta, coluna_voc = st.columns(2, gap="large")

    with coluna_coleta:
        with st.form(f"formulario_coleta_{sondagem_id}", clear_on_submit=True):
            profundidade_coleta = st.number_input(
                "Profundidade da coleta (m)",
                min_value=0.0,
                max_value=profundidade_total,
                value=0.0,
                step=0.1,
                format="%.3f",
            )
            enviar_coleta = st.form_submit_button(
                "Adicionar coleta", use_container_width=True
            )
        if enviar_coleta:
            try:
                db.adicionar_coleta(
                    sondagem_id,
                    profundidade_coleta,
                    CAMINHO_BANCO,
                )
                registrar_mensagem("sucesso", "Ponto de coleta adicionado.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

        coletas = db.listar_coletas(sondagem_id, CAMINHO_BANCO)
        st.dataframe(coletas, use_container_width=True, hide_index=True)
        if not coletas.empty:
            mapa_coletas = {
                int(linha["id"]): f"ID {int(linha['id'])} — {linha['profundidade_coleta']:.3f} m"
                for _, linha in coletas.iterrows()
            }
            coleta_remover = st.selectbox(
                "Coleta para remover",
                options=list(mapa_coletas),
                format_func=lambda valor: mapa_coletas[valor],
                key=f"remover_coleta_{sondagem_id}",
            )
            if st.button(
                "Remover coleta selecionada",
                use_container_width=True,
                key=f"botao_remover_coleta_{sondagem_id}",
            ):
                db.remover_coleta(coleta_remover, CAMINHO_BANCO)
                st.rerun()

    with coluna_voc:
        with st.form(f"formulario_voc_{sondagem_id}", clear_on_submit=True):
            profundidade_voc = st.number_input(
                "Profundidade da medição (m)",
                min_value=0.0,
                max_value=profundidade_total,
                value=0.0,
                step=0.1,
                format="%.3f",
            )
            concentracao_voc = st.number_input(
                "Concentração de VOC (mg/L ou ppm)",
                min_value=0.0,
                value=0.0,
                step=0.01,
                format="%.6f",
            )
            enviar_voc = st.form_submit_button(
                "Adicionar medição de VOC", use_container_width=True
            )
        if enviar_voc:
            try:
                db.adicionar_voc(
                    sondagem_id,
                    profundidade_voc,
                    concentracao_voc,
                    CAMINHO_BANCO,
                )
                registrar_mensagem("sucesso", "Medição de VOC adicionada.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

        medicoes = db.listar_voc(sondagem_id, CAMINHO_BANCO)
        st.dataframe(medicoes, use_container_width=True, hide_index=True)
        if not medicoes.empty:
            mapa_medicoes = {
                int(linha["id"]): (
                    f"ID {int(linha['id'])} — {linha['profundidade']:.3f} m — "
                    f"{linha['concentracao']:.6g}"
                )
                for _, linha in medicoes.iterrows()
            }
            medicao_remover = st.selectbox(
                "Medição para remover",
                options=list(mapa_medicoes),
                format_func=lambda valor: mapa_medicoes[valor],
                key=f"remover_voc_{sondagem_id}",
            )
            if st.button(
                "Remover medição selecionada",
                use_container_width=True,
                key=f"botao_remover_voc_{sondagem_id}",
            ):
                db.remover_voc(medicao_remover, CAMINHO_BANCO)
                st.rerun()


def renderizar_aba_perfil_individual() -> None:
    """Renderiza o perfil individual e as opções de exportação."""
    st.subheader("Perfil litológico individual")
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    if sondagens.empty:
        st.info("Cadastre uma sondagem antes de gerar o perfil.")
        return

    rotulos = opcoes_sondagens(sondagens)
    sondagem_id = st.selectbox(
        "Sondagem para o perfil",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_perfil_individual",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    camadas = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
    coletas = db.listar_coletas(sondagem_id, CAMINHO_BANCO)
    voc = db.listar_voc(sondagem_id, CAMINHO_BANCO)

    if not sondagem or camadas.empty:
        st.warning("A sondagem selecionada ainda não possui perfil litológico salvo.")
        return

    figura = viz.criar_perfil_litologico(sondagem, camadas, coletas, voc)
    st.plotly_chart(figura, use_container_width=True, config={"displaylogo": False})

    coluna_png, coluna_csv = st.columns(2)
    with coluna_png:
        try:
            altura_imagem = min(max(900, int(sondagem["profundidade_total"] * 24)), 3000)
            imagem_png = figura.to_image(
                format="png",
                width=1700,
                height=altura_imagem,
                scale=2,
            )
            st.download_button(
                "Baixar perfil em PNG",
                data=imagem_png,
                file_name=f"perfil_{sondagem['nome_furo']}.png",
                mime="image/png",
                use_container_width=True,
            )
        except Exception as erro:
            st.warning(
                "A exportação PNG requer o pacote Kaleido funcional no ambiente. "
                f"Detalhe técnico: {erro}"
            )

    with coluna_csv:
        exportacao = montar_exportacao_perfil(sondagem, camadas, coletas, voc)
        st.download_button(
            "Baixar dados em CSV",
            data=exportacao.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"dados_{sondagem['nome_furo']}.csv",
            mime="text/csv",
            use_container_width=True,
        )


def renderizar_aba_secao_transversal() -> None:
    """Renderiza a seção hidroestratigráfica entre múltiplas sondagens."""
    st.subheader("Perfil hidroestratigráfico — seção transversal")
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    if len(sondagens) < 2:
        st.info("Cadastre pelo menos duas sondagens.")
        return

    rotulos = opcoes_sondagens(sondagens)
    padrao = list(rotulos)[:2]
    ids_selecionados = st.multiselect(
        "Selecione duas ou mais sondagens na ordem do eixo da seção",
        options=list(rotulos),
        default=padrao,
        format_func=lambda valor: rotulos[valor],
    )
    st.caption(
        "A distância acumulada é calculada na ordem apresentada pela seleção. As conexões são feitas somente entre camadas da mesma classificação e da mesma ordem de ocorrência."
    )

    if len(ids_selecionados) < 2:
        st.warning("Selecione pelo menos duas sondagens.")
        return

    dados_sondagens: list[dict[str, Any]] = []
    faltantes: list[str] = []
    for sondagem_id in ids_selecionados:
        sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
        camadas = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
        if not sondagem or camadas.empty:
            faltantes.append(rotulos[sondagem_id])
        else:
            dados_sondagens.append({"sondagem": sondagem, "camadas": camadas})

    if faltantes:
        st.error(
            "As seguintes sondagens não possuem perfil completo salvo: "
            + "; ".join(faltantes)
        )
        return

    try:
        distancias = viz.calcular_distancias_acumuladas(
            [item["sondagem"] for item in dados_sondagens]
        )
        tabela_distancias = pd.DataFrame(
            {
                "ordem": range(1, len(dados_sondagens) + 1),
                "sondagem": [
                    item["sondagem"]["nome_furo"] for item in dados_sondagens
                ],
                "distancia_acumulada_m": distancias,
                "altitude_m": [
                    item["sondagem"]["altitude"] for item in dados_sondagens
                ],
                "cota_na_m": [
                    item["sondagem"]["cota_nivel_agua"] for item in dados_sondagens
                ],
            }
        )
        st.dataframe(
            tabela_distancias,
            use_container_width=True,
            hide_index=True,
            column_config={
                "distancia_acumulada_m": st.column_config.NumberColumn(format="%.2f m"),
                "altitude_m": st.column_config.NumberColumn(format="%.2f m"),
                "cota_na_m": st.column_config.NumberColumn(format="%.2f m"),
            },
        )

        figura = viz.criar_secao_hidroestratigrafica(dados_sondagens)
        st.plotly_chart(
            figura,
            use_container_width=True,
            config={"displaylogo": False},
        )

        try:
            imagem_png = figura.to_image(
                format="png",
                width=1900,
                height=1100,
                scale=2,
            )
            st.download_button(
                "Baixar seção em PNG",
                data=imagem_png,
                file_name="secao_hidroestratigrafica.png",
                mime="image/png",
            )
        except Exception as erro:
            st.caption(f"Exportação PNG indisponível neste ambiente: {erro}")
    except Exception as erro:
        st.error(f"Não foi possível gerar a seção: {erro}")


def renderizar_aba_importacao() -> None:
    """Renderiza a importação CSV com validação por sondagem."""
    st.subheader("Importação em lote por CSV")
    st.write(
        "O importador agrupa as linhas por projeto e sondagem, deriva a profundidade total pela maior profundidade final e grava cada perfil de forma transacional."
    )

    if CAMINHO_EXEMPLO.exists():
        coluna_download, coluna_demo = st.columns(2)
        with coluna_download:
            st.download_button(
                "Baixar exemplo.csv",
                data=CAMINHO_EXEMPLO.read_bytes(),
                file_name="exemplo.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with coluna_demo:
            if st.button(
                "Carregar dados de demonstração",
                use_container_width=True,
                key="carregar_dados_demonstracao",
            ):
                try:
                    dataframe_exemplo = pd.read_csv(
                        CAMINHO_EXEMPLO,
                        encoding="utf-8-sig",
                    )
                    relatorio_demo = db.importar_dataframe(
                        dataframe_exemplo,
                        CAMINHO_BANCO,
                    )
                    st.session_state["relatorio_importacao"] = relatorio_demo
                    st.success(
                        "Dados de demonstração processados. Consulte o relatório abaixo."
                    )
                except Exception as erro:
                    st.error(f"Não foi possível carregar o exemplo: {erro}")

    st.code(
        ",".join(db.COLUNAS_CSV_OBRIGATORIAS),
        language="text",
    )
    arquivo = st.file_uploader(
        "Selecione o arquivo CSV",
        type=["csv"],
        accept_multiple_files=False,
    )
    if arquivo is None:
        return

    try:
        conteudo = arquivo.getvalue()
        dataframe = pd.read_csv(
            io.BytesIO(conteudo),
            sep=None,
            engine="python",
            encoding="utf-8-sig",
        )
    except Exception as erro:
        st.error(f"Não foi possível ler o CSV: {erro}")
        return

    st.markdown("#### Pré-visualização")
    st.dataframe(dataframe.head(100), use_container_width=True, hide_index=True)
    st.caption(f"Linhas lidas: {len(dataframe)} | Colunas: {len(dataframe.columns)}")

    if st.button("Validar e importar", type="primary", use_container_width=True):
        try:
            relatorio = db.importar_dataframe(dataframe, CAMINHO_BANCO)
            st.session_state["relatorio_importacao"] = relatorio
        except db.ErroValidacao as erro:
            st.error(str(erro))
        except Exception as erro:
            st.error(f"Falha inesperada na importação: {erro}")

    relatorio = st.session_state.get("relatorio_importacao")
    if isinstance(relatorio, pd.DataFrame) and not relatorio.empty:
        st.markdown("#### Relatório")
        sucessos = int((relatorio["status"] == "Sucesso").sum())
        erros = int((relatorio["status"] == "Erro").sum())
        metrica_1, metrica_2, metrica_3 = st.columns(3)
        metrica_1.metric("Sondagens processadas", len(relatorio))
        metrica_2.metric("Sucessos", sucessos)
        metrica_3.metric("Erros", erros)
        st.dataframe(relatorio, use_container_width=True, hide_index=True)
        st.download_button(
            "Baixar relatório em CSV",
            data=relatorio.to_csv(index=False).encode("utf-8-sig"),
            file_name="relatorio_importacao.csv",
            mime="text/csv",
        )


def executar_aplicativo() -> None:
    """Monta a interface completa do aplicativo Streamlit."""
    st.title("💧 Descrição Litológica de Sondagens")
    st.caption(
        "MVP web para poços de água subterrânea — cadastro, validação estratigráfica, mapa, perfil individual, seção transversal e importação em lote."
    )
    exibir_mensagem_pendente()

    with st.sidebar:
        st.header("Configuração")
        st.write("Referência espacial: **SIRGAS 2000 (EPSG:4674)**")
        st.write("Coordenadas: **graus decimais**")
        renderizar_controles_banco()
        st.info(
            "Os padrões gráficos deste MVP seguem o dicionário solicitado. A emissão de documento técnico oficial deve passar pela revisão do responsável habilitado e pela edição vigente das normas aplicáveis."
        )

    aba_1, aba_2, aba_3, aba_4, aba_5 = st.tabs(
        [
            "1. Projetos e Mapa",
            "2. Cadastro Litológico",
            "3. Perfil Individual",
            "4. Seção Transversal",
            "5. Importação em Lote",
        ]
    )

    with aba_1:
        renderizar_aba_projetos_mapa()
    with aba_2:
        renderizar_aba_cadastro_litologico()
    with aba_3:
        renderizar_aba_perfil_individual()
    with aba_4:
        renderizar_aba_secao_transversal()
    with aba_5:
        renderizar_aba_importacao()


if __name__ == "__main__":
    executar_aplicativo()
