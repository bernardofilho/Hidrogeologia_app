from __future__ import annotations

import io
import os
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image, ImageOps

import db_manager as db
import gps_component
import reporting
import visualization as viz

st.set_page_config(
    page_title="Diario de Sondagem Hidrogeologica",
    page_icon="\U0001f4a7",
    layout="wide",
    initial_sidebar_state="expanded",
)

DIRETORIO_APLICACAO = Path(__file__).resolve().parent
CAMINHO_EXEMPLO = DIRETORIO_APLICACAO / "exemplo.csv"
CAMINHO_EXEMPLO_CONGONHAS = DIRETORIO_APLICACAO / "sondagens_congonhas_importacao.csv"
MODO_BANCO = os.getenv("HIDRO_DB_MODE", "local").strip().lower()
VERSAO_APP = "2.0.0"

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
        .cartao-painel {background: #FFFFFF; border: 1px solid #D5DBDB;
                       border-radius: 0.75rem; padding: 1rem; min-height: 7.2rem;
                       box-shadow: 0 1px 2px rgba(0,0,0,0.04);}
        .cartao-painel strong {color: #1F4E78;}
        .proxima-acao {background: #EAF2F8; border: 1px solid #AED6F1;
                      border-radius: 0.65rem; padding: 0.85rem 1rem;}
        .coordenada-ok {background: #EAFAF1; border-left: 4px solid #28B463;
                        padding: 0.7rem 0.9rem; border-radius: 0.3rem;}
        .coordenada-erro {background: #FDEDEC; border-left: 4px solid #CB4335;
                          padding: 0.7rem 0.9rem; border-radius: 0.3rem;}
        @media (max-width: 768px) {
            .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
            .stMetric {padding: 0.45rem;}
        }
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
        f"{resumo['camadas']} camada(s), {resumo['pocos']} poço(s), "
        f"{resumo['desenvolvimentos']} desenvolvimento(s) e {resumo['fotos']} foto(s)."
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
    chave_opcao = f"{prefixo}_opcao_crs"
    argumentos_selectbox: dict[str, Any] = {
        "options": opcoes,
        "format_func": lambda valor: (
            CRS_PREDEFINIDOS[int(valor)]
            if isinstance(valor, int)
            else str(valor)
        ),
        "key": chave_opcao,
    }
    if chave_opcao not in st.session_state:
        argumentos_selectbox["index"] = indice
    opcao = st.selectbox(
        "Sistema de coordenadas de entrada *",
        **argumentos_selectbox,
    )
    if opcao == "Outro EPSG":
        chave_epsg_outro = f"{prefixo}_epsg_outro"
        argumentos_numero: dict[str, Any] = {
            "min_value": 1,
            "step": 1,
            "key": chave_epsg_outro,
        }
        if chave_epsg_outro not in st.session_state:
            argumentos_numero["value"] = int(epsg_padrao)
        epsg = int(st.number_input("Código EPSG *", **argumentos_numero))
    else:
        epsg = int(opcao)
    metadados = db.obter_metadados_crs(epsg)
    st.caption(
        f"EPSG:{epsg} - {metadados['nome']} | unidade: {metadados['unidade']}"
    )
    return epsg


def _numero_colado(texto: str) -> float:
    """Converte ponto ou vírgula decimal em número."""
    valor = str(texto or "").strip().replace(" ", "")
    if not valor:
        raise ValueError("valor vazio")
    if "," in valor and "." in valor:
        # Quando os dois separadores aparecem, o último é tratado como decimal.
        if valor.rfind(",") > valor.rfind("."):
            valor = valor.replace(".", "").replace(",", ".")
        else:
            valor = valor.replace(",", "")
    else:
        valor = valor.replace(",", ".")
    return float(valor)


def interpretar_coordenadas_coladas(texto: str) -> tuple[float, float]:
    """Interpreta pares de coordenadas colados em diferentes formatos."""
    import re

    conteudo = str(texto or "").strip()
    if not conteudo:
        raise db.ErroValidacao("Cole as duas coordenadas antes de interpretar.")

    padrao_x = re.search(
        r"(?:^|\b)(?:x|e|este|easting|lon|longitude)\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)",
        conteudo,
        flags=re.IGNORECASE,
    )
    padrao_y = re.search(
        r"(?:^|\b)(?:y|n|norte|northing|lat|latitude)\s*[:=]?\s*([-+]?\d+(?:[.,]\d+)?)",
        conteudo,
        flags=re.IGNORECASE,
    )
    if padrao_x and padrao_y:
        return _numero_colado(padrao_x.group(1)), _numero_colado(padrao_y.group(1))

    numeros = re.findall(r"[-+]?\d+(?:[.,]\d+)?", conteudo)
    if len(numeros) != 2:
        raise db.ErroValidacao(
            "Não foi possível identificar exatamente duas coordenadas. "
            "Use, por exemplo: 330717,31 ; 7385947,28."
        )
    return _numero_colado(numeros[0]), _numero_colado(numeros[1])


def _formatar_coordenada(valor: float, geografico: bool) -> str:
    """Formata a coordenada sem notação científica."""
    return f"{float(valor):.8f}" if geografico else f"{float(valor):.3f}"


def sincronizar_campos_coordenadas(prefixo: str, epsg: int) -> None:
    """Inicializa os campos vazios e registra o CRS corrente."""
    chave_epsg = f"{prefixo}_epsg_anterior"
    if chave_epsg not in st.session_state:
        st.session_state[chave_epsg] = int(epsg)
        st.session_state.setdefault(f"{prefixo}_coord_x", "")
        st.session_state.setdefault(f"{prefixo}_coord_y", "")
        st.session_state.setdefault(f"{prefixo}_origem", "Digitação manual")
        st.session_state.setdefault(f"{prefixo}_precisao_gps", None)
        st.session_state.setdefault(f"{prefixo}_data_gps", None)
    elif int(st.session_state[chave_epsg]) != int(epsg):
        st.session_state[chave_epsg] = int(epsg)
        st.session_state[f"{prefixo}_coord_x"] = ""
        st.session_state[f"{prefixo}_coord_y"] = ""
        st.session_state[f"{prefixo}_origem"] = "Digitação manual"
        st.session_state[f"{prefixo}_precisao_gps"] = None
        st.session_state[f"{prefixo}_data_gps"] = None


def renderizar_ferramentas_coordenadas(prefixo: str, epsg: int) -> None:
    """Oferece GPS do celular, copiar e colar e digitação individual."""
    import re

    sincronizar_campos_coordenadas(prefixo, epsg)
    metadados = db.obter_metadados_crs(epsg)
    aba_gps, aba_colar, aba_manual = st.tabs(
        ["GPS do celular", "Copiar e colar", "Digitação manual"]
    )

    with aba_gps:
        st.caption(
            "A localização capturada pelo navegador é convertida automaticamente "
            "para o sistema de coordenadas selecionado. Em celulares, autorize a "
            "localização precisa quando o navegador solicitar."
        )
        posicao, erro = gps_component.capturar_gps(f"gps_real_{prefixo}")
        if posicao:
            marcador = str(
                posicao.get("timestamp")
                or f"{posicao.get('latitude')}|{posicao.get('longitude')}"
            )
            if st.session_state.get(f"{prefixo}_ultimo_evento_gps") != marcador:
                try:
                    x, y = db.transformar_coordenadas(
                        float(posicao["longitude"]),
                        float(posicao["latitude"]),
                        4326,
                        epsg,
                    )
                    st.session_state[f"{prefixo}_coord_x"] = _formatar_coordenada(
                        x, bool(metadados["geografico"])
                    )
                    st.session_state[f"{prefixo}_coord_y"] = _formatar_coordenada(
                        y, bool(metadados["geografico"])
                    )
                    st.session_state[f"{prefixo}_origem"] = "GPS do dispositivo"
                    st.session_state[f"{prefixo}_precisao_gps"] = posicao.get("accuracy")
                    st.session_state[f"{prefixo}_data_gps"] = posicao.get("timestamp")
                    st.session_state[f"{prefixo}_ultimo_evento_gps"] = marcador
                    registrar_mensagem(
                        "sucesso",
                        "GPS capturado e convertido para o CRS selecionado.",
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Não foi possível converter a posição do GPS: {exc}")
        if erro:
            st.warning(str(erro.get("message") or "Falha ao capturar o GPS."))

    with aba_colar:
        st.caption(
            "Cole as duas coordenadas de uma planilha, mensagem, equipamento ou aplicativo. "
            "Ponto e vírgula decimal são aceitos."
        )
        ordem = st.radio(
            "Ordem dos valores quando não houver rótulos",
            options=[
                "Detectar automaticamente",
                "X / longitude primeiro",
                "Y / latitude primeiro",
            ],
            horizontal=True,
            key=f"{prefixo}_ordem_colada",
        )
        texto_colado = st.text_area(
            "Cole as coordenadas",
            placeholder=(
                "Exemplos:\n330717,31 ; 7385947,28\n"
                "X=330717.31  Y=7385947.28\n"
                "-23.626338, -46.656487"
            ),
            height=105,
            key=f"{prefixo}_texto_colado",
        )
        if st.button(
            "Interpretar e preencher",
            width="stretch",
            key=f"{prefixo}_interpretar_colado",
        ):
            try:
                primeiro, segundo = interpretar_coordenadas_coladas(texto_colado)
                possui_rotulos = bool(
                    re.search(
                        r"(?:^|\b)(?:x|e|este|easting|lon|longitude|y|n|norte|northing|lat|latitude)\s*[:=]",
                        texto_colado,
                        flags=re.IGNORECASE,
                    )
                )

                x, y = primeiro, segundo
                if not possui_rotulos:
                    if ordem == "Y / latitude primeiro":
                        x, y = segundo, primeiro
                    elif ordem == "Detectar automaticamente":
                        # Para pares geográficos, prioriza o formato comum latitude, longitude.
                        parece_lat_lon = (
                            abs(primeiro) <= 90
                            and abs(segundo) <= 180
                            and (
                                abs(segundo) > 90
                                or (-35 <= primeiro <= 7 and -76 <= segundo <= -30)
                            )
                        )
                        parece_lon_lat = (
                            abs(primeiro) <= 180
                            and abs(segundo) <= 90
                            and (-76 <= primeiro <= -30 and -35 <= segundo <= 7)
                        )
                        if parece_lat_lon and not parece_lon_lat:
                            x, y = segundo, primeiro

                parece_geografico = abs(x) <= 180 and abs(y) <= 90
                origem = "Copiar e colar"
                if not bool(metadados["geografico"]) and parece_geografico:
                    x, y = db.transformar_coordenadas(x, y, 4326, epsg)
                    origem = "Copiar e colar (GPS/WGS84 detectado)"

                st.session_state[f"{prefixo}_coord_x"] = _formatar_coordenada(
                    x, bool(metadados["geografico"])
                )
                st.session_state[f"{prefixo}_coord_y"] = _formatar_coordenada(
                    y, bool(metadados["geografico"])
                )
                st.session_state[f"{prefixo}_origem"] = origem
                st.session_state[f"{prefixo}_precisao_gps"] = None
                st.session_state[f"{prefixo}_data_gps"] = None
                registrar_mensagem(
                    "sucesso",
                    "Coordenadas interpretadas, convertidas quando necessário e preenchidas.",
                )
                st.rerun()
            except db.ErroValidacao as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Não foi possível interpretar as coordenadas: {exc}")

    with aba_manual:
        st.write(
            "Preencha os dois campos abaixo. Eles aceitam valores copiados "
            "individualmente de planilhas, aplicativos de topografia ou GPS."
        )


def renderizar_campos_coordenadas(
    prefixo: str,
    epsg: int,
) -> tuple[float | None, float | None]:
    """Monta campos de texto para coordenadas e mostra a conversão para o mapa."""
    sincronizar_campos_coordenadas(prefixo, epsg)
    metadados = db.obter_metadados_crs(epsg)
    coluna_x, coluna_y = st.columns(2)
    with coluna_x:
        texto_x = st.text_input(
            f"{metadados['rotulo_x']} *",
            key=f"{prefixo}_coord_x",
            placeholder="Cole ou digite o valor",
        )
    with coluna_y:
        texto_y = st.text_input(
            f"{metadados['rotulo_y']} *",
            key=f"{prefixo}_coord_y",
            placeholder="Cole ou digite o valor",
        )

    try:
        x = _numero_colado(texto_x)
        y = _numero_colado(texto_y)
        latitude, longitude = db.converter_para_sirgas2000(x, y, epsg)
        st.markdown(
            "<div class='coordenada-ok'><b>Coordenadas válidas.</b> "
            f"Prévia para o mapa: {latitude:.8f}, {longitude:.8f}. "
            f"Origem: {st.session_state.get(f'{prefixo}_origem', 'Manual')}.</div>",
            unsafe_allow_html=True,
        )
        st.map(pd.DataFrame({"lat": [latitude], "lon": [longitude]}), height=180)
        return float(x), float(y)
    except Exception:
        st.markdown(
            "<div class='coordenada-erro'><b>Informe as duas coordenadas.</b> "
            "A prévia do mapa aparecerá depois que os valores forem válidos.</div>",
            unsafe_allow_html=True,
        )
        return None, None


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
    """Renderiza projetos, locação e revisão de coordenadas."""
    renderizar_cabecalho_etapa(
        1,
        "Projeto e locação",
        "Cadastre o projeto, identifique o furo e confirme a localização antes de iniciar a perfuração.",
    )
    st.info(
        "Fluxo recomendado: selecione o CRS, obtenha a posição pelo GPS do celular "
        "ou cole as coordenadas, confira a prévia no mapa e somente então salve a sondagem."
    )

    coluna_projeto, coluna_sondagem = st.columns([0.92, 1.55], gap="large")

    with coluna_projeto:
        st.markdown("#### 1. Identificação do projeto")
        with st.form("formulario_novo_projeto", clear_on_submit=True):
            nome = st.text_input("Nome do projeto *", placeholder="Ex.: Monitoramento ambiental - Área A")
            cliente = st.text_input("Cliente / contratante")
            localizacao = st.text_input("Município / local")
            descricao = st.text_area("Descrição e objetivo", height=90)
            responsavel = st.text_input("Responsável técnico")
            registro = st.text_input("Registro profissional", placeholder="Ex.: CREA/CRQ/CRBio")
            enviar = st.form_submit_button("Criar projeto", type="primary", width="stretch")
        if enviar:
            try:
                db.criar_projeto(
                    nome,
                    descricao,
                    CAMINHO_BANCO,
                    cliente=cliente,
                    localizacao=localizacao,
                    responsavel_tecnico=responsavel,
                    registro_profissional=registro,
                )
                registrar_mensagem("sucesso", "Projeto criado com sucesso.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

        projetos = db.listar_projetos(CAMINHO_BANCO)
        if not projetos.empty:
            with st.expander("Revisar dados de um projeto"):
                mapa_projetos_edicao = {
                    int(linha["id"]): str(linha["nome"])
                    for _, linha in projetos.iterrows()
                }
                projeto_edicao_id = st.selectbox(
                    "Projeto",
                    options=list(mapa_projetos_edicao),
                    format_func=lambda valor: mapa_projetos_edicao[valor],
                    key="projeto_edicao_id",
                )
                projeto_atual = db.obter_projeto(projeto_edicao_id, CAMINHO_BANCO) or {}
                with st.form(f"form_editar_projeto_{projeto_edicao_id}"):
                    descricao_edicao = st.text_area(
                        "Descrição e objetivo",
                        value=str(projeto_atual.get("descricao") or ""),
                        height=80,
                    )
                    cliente_edicao = st.text_input(
                        "Cliente / contratante",
                        value=str(projeto_atual.get("cliente") or ""),
                    )
                    localizacao_edicao = st.text_input(
                        "Município / local",
                        value=str(projeto_atual.get("localizacao") or ""),
                    )
                    responsavel_edicao = st.text_input(
                        "Responsável técnico",
                        value=str(projeto_atual.get("responsavel_tecnico") or ""),
                    )
                    registro_edicao = st.text_input(
                        "Registro profissional",
                        value=str(projeto_atual.get("registro_profissional") or ""),
                    )
                    salvar_projeto = st.form_submit_button(
                        "Salvar dados do projeto", width="stretch"
                    )
                if salvar_projeto:
                    try:
                        db.atualizar_projeto(
                            projeto_edicao_id,
                            descricao_edicao,
                            cliente_edicao,
                            localizacao_edicao,
                            responsavel_edicao,
                            registro_edicao,
                            CAMINHO_BANCO,
                        )
                        registrar_mensagem("sucesso", "Dados do projeto atualizados.")
                        st.rerun()
                    except db.ErroValidacao as erro:
                        st.error(str(erro))

    projetos = db.listar_projetos(CAMINHO_BANCO)
    with coluna_sondagem:
        st.markdown("#### 2. Planejamento e coordenadas do furo")
        if projetos.empty:
            st.warning("Crie um projeto antes de planejar a primeira sondagem.")
        else:
            epsg = selecionar_crs("nova_sondagem", 31983)
            renderizar_ferramentas_coordenadas("nova_sondagem", epsg)
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
                    "Identificação do furo *",
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
                with st.expander("Informações operacionais iniciais (opcional)"):
                    metodo = st.text_input("Método de perfuração", placeholder="Ex.: trado oco / rotativa")
                    equipamento = st.text_input("Equipamento")
                    empresa = st.text_input("Empresa executora")
                    responsavel_campo = st.text_input("Responsável de campo")
                enviar_sondagem = st.form_submit_button(
                    "Salvar sondagem planejada",
                    type="primary",
                    width="stretch",
                )

            if enviar_sondagem:
                if x is None or y is None:
                    st.error("Informe e valide as duas coordenadas antes de salvar a sondagem.")
                else:
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
                            origem_coordenada=st.session_state.get(
                                "nova_sondagem_origem", "Digitação manual"
                            ),
                            precisao_gps_m=st.session_state.get(
                                "nova_sondagem_precisao_gps"
                            ),
                            data_captura_gps=st.session_state.get(
                                "nova_sondagem_data_gps"
                            ),
                            metodo_perfuracao=metodo,
                            equipamento=equipamento,
                            empresa_executora=empresa,
                            responsavel_campo=responsavel_campo,
                        )
                        for chave in list(st.session_state):
                            if chave.startswith("nova_sondagem_coord_") or chave.startswith("nova_sondagem_texto_"):
                                st.session_state.pop(chave, None)
                        registrar_mensagem(
                            "sucesso",
                            "Sondagem planejada. A próxima etapa é iniciar o diário de campo.",
                        )
                        st.rerun()
                    except db.ErroValidacao as erro:
                        st.error(str(erro))

    st.divider()
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    st.markdown("#### Sondagens cadastradas")
    if sondagens.empty:
        st.info("Nenhuma sondagem cadastrada.")
        return

    st.dataframe(
        tabela_sondagens_resumida(sondagens),
        width="stretch",
        hide_index=True,
    )

    with st.expander("Revisar coordenadas e origem da posição"):
        rotulos = opcoes_sondagens(sondagens)
        sondagem_id = st.selectbox(
            "Sondagem para revisar",
            options=list(rotulos),
            format_func=lambda valor: rotulos[valor],
            key="seletor_revisao_coordenadas",
        )
        sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
        if not sondagem:
            return

        prefixo = f"coord_edicao_{sondagem_id}"
        epsg_atual = int(sondagem.get("crs_entrada") or 4674)
        chave_carregada = f"{prefixo}_sondagem_carregada"
        if st.session_state.get(chave_carregada) != sondagem_id:
            st.session_state[chave_carregada] = sondagem_id
            st.session_state[f"{prefixo}_opcao_crs"] = (
                epsg_atual if epsg_atual in CRS_PREDEFINIDOS else "Outro EPSG"
            )
            if epsg_atual not in CRS_PREDEFINIDOS:
                st.session_state[f"{prefixo}_epsg_outro"] = epsg_atual
            st.session_state[f"{prefixo}_epsg_anterior"] = epsg_atual
            metadados_atual = db.obter_metadados_crs(epsg_atual)
            st.session_state[f"{prefixo}_coord_x"] = _formatar_coordenada(
                float(sondagem["coordenada_x"]), bool(metadados_atual["geografico"])
            )
            st.session_state[f"{prefixo}_coord_y"] = _formatar_coordenada(
                float(sondagem["coordenada_y"]), bool(metadados_atual["geografico"])
            )
            st.session_state[f"{prefixo}_origem"] = str(
                sondagem.get("origem_coordenada") or "Cadastro anterior"
            )
            st.session_state[f"{prefixo}_precisao_gps"] = sondagem.get("precisao_gps_m")
            st.session_state[f"{prefixo}_data_gps"] = sondagem.get("data_captura_gps")

        epsg_destino = selecionar_crs(prefixo, epsg_atual)
        epsg_anterior = int(st.session_state.get(f"{prefixo}_epsg_anterior", epsg_atual))
        if epsg_anterior != epsg_destino:
            x_destino, y_destino = db.converter_de_sirgas2000(
                sondagem["latitude"],
                sondagem["longitude"],
                epsg_destino,
            )
            metadados_destino = db.obter_metadados_crs(epsg_destino)
            st.session_state[f"{prefixo}_coord_x"] = _formatar_coordenada(
                x_destino, bool(metadados_destino["geografico"])
            )
            st.session_state[f"{prefixo}_coord_y"] = _formatar_coordenada(
                y_destino, bool(metadados_destino["geografico"])
            )
            st.session_state[f"{prefixo}_epsg_anterior"] = epsg_destino
            st.session_state[f"{prefixo}_origem"] = "Transformação do cadastro"

        renderizar_ferramentas_coordenadas(prefixo, epsg_destino)
        x_novo, y_novo = renderizar_campos_coordenadas(prefixo, epsg_destino)
        st.caption(
            f"Origem atual: {st.session_state.get(f'{prefixo}_origem', '-')} | "
            f"Precisão GPS: {st.session_state.get(f'{prefixo}_precisao_gps') or '-'} m"
        )
        if st.button(
            "Salvar coordenadas revisadas",
            type="primary",
            width="stretch",
            key=f"salvar_coord_{sondagem_id}",
        ):
            if x_novo is None or y_novo is None:
                st.error("Informe coordenadas válidas antes de salvar.")
            else:
                try:
                    db.atualizar_coordenadas_sondagem(
                        sondagem_id,
                        x_novo,
                        y_novo,
                        epsg_destino,
                        CAMINHO_BANCO,
                        origem_coordenada=st.session_state.get(
                            f"{prefixo}_origem", "Digitação manual"
                        ),
                        precisao_gps_m=st.session_state.get(
                            f"{prefixo}_precisao_gps"
                        ),
                        data_captura_gps=st.session_state.get(
                            f"{prefixo}_data_gps"
                        ),
                    )
                    registrar_mensagem(
                        "sucesso", "Coordenadas e CRS atualizados com sucesso."
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
    """Renderiza amostras, VOC e histórico de nível d'água."""
    sondagem_id = int(sondagem["id"])
    profundidade_atual = float(sondagem["profundidade_atual"] or 0)
    aba_amostras, aba_voc, aba_na = st.tabs(
        ["Amostras", "VOC", "Nível d'água"]
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
                enviar = st.form_submit_button("Registrar coleta", width="stretch")
            if enviar:
                try:
                    db.adicionar_coleta(sondagem_id, profundidade, CAMINHO_BANCO)
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
                    "Profundidade da medição (m)",
                    min_value=0.0,
                    max_value=profundidade_atual,
                    value=profundidade_atual,
                    step=0.1,
                    format="%.3f",
                )
                concentracao = st.number_input(
                    "Concentração (mg/L ou ppm)",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.6f",
                )
                enviar = st.form_submit_button("Registrar VOC", width="stretch")
            if enviar:
                try:
                    db.adicionar_voc(
                        sondagem_id, profundidade, concentracao, CAMINHO_BANCO
                    )
                    registrar_mensagem("sucesso", "Medição de VOC registrada.")
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
                "Medição para remover",
                options=list(mapa),
                format_func=lambda valor: mapa[valor],
                key=f"remover_voc_{sondagem_id}",
            )
            if st.button(
                "Remover medição",
                width="stretch",
                key=f"botao_remover_voc_{sondagem_id}",
            ):
                db.remover_voc(remover, CAMINHO_BANCO)
                st.rerun()

    with aba_na:
        st.write(
            "Registre cada leitura com data, horário e condição. Marque uma leitura "
            "como NA estático para usá-la no perfil, na zona vadosa e nos relatórios."
        )
        if profundidade_atual <= 0:
            st.info("Inicie a perfuração antes de registrar o nível d'água.")
        else:
            with st.form(f"form_leitura_na_{sondagem_id}", clear_on_submit=True):
                col_data, col_hora, col_tipo = st.columns(3)
                with col_data:
                    data_leitura = st.date_input("Data", value=date.today())
                with col_hora:
                    hora_leitura = st.time_input(
                        "Horário", value=datetime.now().time().replace(second=0, microsecond=0)
                    )
                with col_tipo:
                    tipo_leitura = st.selectbox(
                        "Tipo de leitura", options=db.TIPOS_LEITURA_NA_VALIDOS
                    )
                profundidade_na = st.number_input(
                    "Profundidade do nível d'água abaixo do terreno (m)",
                    min_value=0.0,
                    max_value=profundidade_atual,
                    value=min(5.0, profundidade_atual),
                    step=0.01,
                    format="%.3f",
                )
                usar_estatico = st.checkbox(
                    "Usar esta leitura como NA estático oficial da sondagem",
                    value="Estático" in tipo_leitura,
                )
                observacoes_na = st.text_area(
                    "Observações", placeholder="Ex.: leitura após 24 h de estabilização."
                )
                salvar_na = st.form_submit_button(
                    "Registrar leitura de NA", type="primary", width="stretch"
                )
            if salvar_na:
                try:
                    db.adicionar_leitura_nivel_agua(
                        sondagem_id=sondagem_id,
                        profundidade_m=profundidade_na,
                        tipo=tipo_leitura,
                        data_hora=datetime.combine(data_leitura, hora_leitura),
                        observacoes=observacoes_na,
                        usar_como_estatico=usar_estatico,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Leitura de nível d'água registrada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

        leituras = db.listar_leituras_nivel_agua(sondagem_id, CAMINHO_BANCO)
        if leituras.empty:
            st.caption("Nenhuma leitura histórica registrada.")
        else:
            tabela = leituras.copy()
            tabela["usar_como_estatico"] = tabela["usar_como_estatico"].map(
                {True: "Sim", False: "Não"}
            )
            st.dataframe(tabela, width="stretch", hide_index=True)
            mapa_leituras = {
                int(linha["id"]): (
                    f"{linha['data_hora']} | {float(linha['profundidade_m']):.3f} m | {linha['tipo']}"
                )
                for _, linha in leituras.iterrows()
            }
            leitura_remover = st.selectbox(
                "Leitura para remover",
                options=list(mapa_leituras),
                format_func=lambda valor: mapa_leituras[valor],
                key=f"leitura_na_remover_{sondagem_id}",
            )
            col_remover, col_limpar = st.columns(2)
            with col_remover:
                if st.button(
                    "Remover leitura selecionada",
                    width="stretch",
                    key=f"remover_leitura_na_{sondagem_id}",
                ):
                    db.remover_leitura_nivel_agua(leitura_remover, CAMINHO_BANCO)
                    st.rerun()
            with col_limpar:
                if st.button(
                    "Limpar NA estático do perfil",
                    width="stretch",
                    key=f"limpar_na_estatico_{sondagem_id}",
                ):
                    db.atualizar_nivel_agua(sondagem_id, None, CAMINHO_BANCO)
                    registrar_mensagem("sucesso", "NA estático removido do perfil.")
                    st.rerun()


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

    with st.expander("Equipe, método e observações da execução"):
        with st.form(f"form_execucao_{sondagem_id}"):
            col_a, col_b = st.columns(2)
            with col_a:
                metodo_execucao = st.text_input(
                    "Método de perfuração",
                    value=str(sondagem.get("metodo_perfuracao") or ""),
                    placeholder="Ex.: trado oco, rotativa, percussiva",
                )
                equipamento_execucao = st.text_input(
                    "Equipamento",
                    value=str(sondagem.get("equipamento") or ""),
                )
                empresa_execucao = st.text_input(
                    "Empresa executora",
                    value=str(sondagem.get("empresa_executora") or ""),
                )
            with col_b:
                responsavel_execucao = st.text_input(
                    "Responsável de campo",
                    value=str(sondagem.get("responsavel_campo") or ""),
                )
                observacoes_execucao = st.text_area(
                    "Ocorrências e observações gerais",
                    value=str(sondagem.get("observacoes_gerais") or ""),
                    height=115,
                    placeholder="Ex.: recusa, perda de circulação, alteração de método, chuva.",
                )
            salvar_execucao = st.form_submit_button(
                "Salvar informações operacionais", width="stretch"
            )
        if salvar_execucao:
            try:
                db.atualizar_dados_execucao(
                    sondagem_id,
                    metodo_execucao,
                    equipamento_execucao,
                    empresa_execucao,
                    responsavel_execucao,
                    observacoes_execucao,
                    CAMINHO_BANCO,
                )
                registrar_mensagem("sucesso", "Informações de execução atualizadas.")
                st.rerun()
            except db.ErroValidacao as erro:
                st.error(str(erro))

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
        7,
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
    """Renderiza importação CSV com pré-validação e arquivos de teste."""
    renderizar_cabecalho_etapa(
        8,
        "Importação em lote",
        "Importe perfis já concluídos, confira o relatório por sondagem e mantenha um backup antes de grandes cargas.",
    )
    st.info(
        "Cada sondagem é validada de forma independente. Camadas descontínuas, sobrepostas "
        "ou com classificação fora do domínio são rejeitadas sem comprometer os demais furos."
    )

    with st.expander("Formato esperado do CSV", expanded=False):
        st.write("Colunas litológicas obrigatórias:")
        st.code(",".join(db.COLUNAS_CSV_BASE), language="text")
        st.write("Use um dos esquemas de coordenadas:")
        st.code(
            "crs_epsg,coordenada_x,coordenada_y\nOU\nlatitude,longitude",
            language="text",
        )
        st.caption(
            "Latitude/longitude são interpretadas como SIRGAS 2000 geográfico (EPSG:4674). "
            "Para UTM 23S, use EPSG:31983, Este e Norte em metros."
        )

    st.markdown("#### Arquivos prontos para teste")
    colunas_arquivo = st.columns(3)
    if CAMINHO_EXEMPLO.exists():
        with colunas_arquivo[0]:
            st.download_button(
                "Baixar exemplo pequeno",
                data=CAMINHO_EXEMPLO.read_bytes(),
                file_name="exemplo.csv",
                mime="text/csv",
                width="stretch",
            )
        with colunas_arquivo[1]:
            if st.button(
                "Importar exemplo pequeno",
                width="stretch",
                key="importar_exemplo_pequeno",
            ):
                try:
                    dados = pd.read_csv(CAMINHO_EXEMPLO, encoding="utf-8-sig")
                    st.session_state["relatorio_importacao"] = db.importar_dataframe(
                        dados, CAMINHO_BANCO
                    )
                    registrar_mensagem("sucesso", "Exemplo pequeno processado.")
                    st.rerun()
                except Exception as erro:
                    st.error(str(erro))
    if CAMINHO_EXEMPLO_CONGONHAS.exists():
        with colunas_arquivo[2]:
            st.download_button(
                "Baixar teste com 48 sondagens",
                data=CAMINHO_EXEMPLO_CONGONHAS.read_bytes(),
                file_name="sondagens_congonhas_importacao.csv",
                mime="text/csv",
                width="stretch",
            )
        if st.button(
            "Importar as 48 sondagens de teste",
            type="secondary",
            width="stretch",
            key="importar_congonhas_teste",
        ):
            try:
                dados = pd.read_csv(
                    CAMINHO_EXEMPLO_CONGONHAS, encoding="utf-8-sig"
                )
                st.session_state["relatorio_importacao"] = db.importar_dataframe(
                    dados, CAMINHO_BANCO
                )
                registrar_mensagem(
                    "sucesso", "Arquivo de teste com 48 sondagens processado."
                )
                st.rerun()
            except Exception as erro:
                st.error(str(erro))

    st.divider()
    st.markdown("#### Importar seu arquivo")
    arquivo = st.file_uploader(
        "Selecione o CSV",
        type=["csv"],
        accept_multiple_files=False,
        help="O arquivo não é gravado até que você clique em Validar e importar.",
    )
    if arquivo is not None:
        try:
            dataframe = pd.read_csv(
                io.BytesIO(arquivo.getvalue()),
                sep=None,
                engine="python",
                encoding="utf-8-sig",
            )
            st.caption(f"{len(dataframe)} linha(s) e {len(dataframe.columns)} coluna(s).")
            st.dataframe(dataframe.head(100), width="stretch", hide_index=True)
            if st.button(
                "Validar e importar",
                type="primary",
                width="stretch",
            ):
                st.session_state["relatorio_importacao"] = db.importar_dataframe(
                    dataframe, CAMINHO_BANCO
                )
                st.rerun()
        except Exception as erro:
            st.error(f"Falha na leitura do CSV: {erro}")

    relatorio = st.session_state.get("relatorio_importacao")
    if isinstance(relatorio, pd.DataFrame) and not relatorio.empty:
        st.markdown("#### Resultado da importação")
        sucessos = int((relatorio["status"] == "Sucesso").sum())
        erros = int((relatorio["status"] == "Erro").sum())
        col_1, col_2, col_3 = st.columns(3)
        col_1.metric("Sondagens processadas", len(relatorio))
        col_2.metric("Sucessos", sucessos)
        col_3.metric("Erros", erros)
        if erros:
            st.warning("Revise as linhas com erro antes de utilizar os dados tecnicamente.")
        else:
            st.success("Todos os perfis foram importados sem erros de estrutura.")
        st.dataframe(relatorio, width="stretch", hide_index=True)
        st.download_button(
            "Baixar relatório de importação",
            data=relatorio.to_csv(index=False).encode("utf-8-sig"),
            file_name="relatorio_importacao.csv",
            mime="text/csv",
            width="stretch",
        )


def _numero_opcional_texto(valor: Any, nome: str) -> float | None:
    """Converte campo textual opcional em número, aceitando vírgula decimal."""
    if valor is None or not str(valor).strip():
        return None
    try:
        return _numero_colado(str(valor))
    except (TypeError, ValueError) as erro:
        raise db.ErroValidacao(f"O campo '{nome}' deve ser numérico ou ficar vazio.") from erro


def _nome_arquivo_seguro(texto: str) -> str:
    """Cria um nome de arquivo simples para downloads."""
    import re
    nome = re.sub(r"[^A-Za-z0-9_-]+", "_", str(texto or "sondagem")).strip("_")
    return nome or "sondagem"


def _preparar_imagem_upload(arquivo: Any) -> tuple[bytes, int, int, str, str]:
    """Corrige orientação, reduz resolução e converte uma foto para JPEG."""
    if arquivo is None:
        raise db.ErroValidacao("Selecione uma fotografia.")
    conteudo = arquivo.getvalue() if hasattr(arquivo, "getvalue") else bytes(arquivo)
    if not conteudo:
        raise db.ErroValidacao("A fotografia está vazia.")
    try:
        with Image.open(io.BytesIO(conteudo)) as imagem_aberta:
            imagem = ImageOps.exif_transpose(imagem_aberta).convert("RGB")
            imagem.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            destino = io.BytesIO()
            imagem.save(destino, format="JPEG", quality=86, optimize=True)
            bytes_saida = destino.getvalue()
            largura, altura = imagem.size
    except Exception as erro:
        raise db.ErroValidacao(f"Não foi possível processar a imagem: {erro}") from erro
    nome_original = str(getattr(arquivo, "name", "foto.jpg") or "foto.jpg")
    nome_saida = f"{Path(nome_original).stem}.jpg"
    return bytes_saida, int(largura), int(altura), "image/jpeg", nome_saida


def _figura_para_png(figura: Any, largura: int, altura: int) -> bytes | None:
    """Converte uma figura Plotly em PNG sem interromper a página em caso de falha."""
    try:
        return figura.to_image(
            format="png",
            width=int(largura),
            height=int(altura),
            scale=2,
        )
    except Exception as erro:
        st.warning(f"A imagem PNG não pôde ser gerada neste ambiente: {erro}")
        return None


def renderizar_painel_geral() -> None:
    """Apresenta o andamento geral e a próxima ação de cada sondagem."""
    st.subheader("Visão geral do trabalho")
    st.caption(
        "Acompanhe o fluxo desde a locação até o relatório final. Os dados incompletos "
        "continuam salvos no diário e podem ser retomados depois."
    )
    resumo = db.obter_resumo_banco(CAMINHO_BANCO)
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    planejadas = int((sondagens["status"] == db.STATUS_PLANEJADA).sum()) if not sondagens.empty else 0
    execucao = int((sondagens["status"] == db.STATUS_EXECUCAO).sum()) if not sondagens.empty else 0
    concluidas = int((sondagens["status"] == db.STATUS_CONCLUIDA).sum()) if not sondagens.empty else 0

    metricas = st.columns(6)
    metricas[0].metric("Projetos", resumo["projetos"])
    metricas[1].metric("Planejadas", planejadas)
    metricas[2].metric("Em execução", execucao)
    metricas[3].metric("Concluídas", concluidas)
    metricas[4].metric("Poços instalados", resumo["pocos"])
    metricas[5].metric("Fotografias", resumo["fotos"])

    if sondagens.empty:
        st.markdown(
            "<div class='proxima-acao'><b>Próxima ação:</b> abra <i>Projeto e locação</i>, "
            "cadastre o projeto e planeje o primeiro furo.</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown("#### Andamento das sondagens")
    linhas: list[dict[str, Any]] = []
    for _, linha in sondagens.iterrows():
        status = str(linha["status"])
        atual = float(linha.get("profundidade_atual") or 0)
        planejada = float(linha.get("profundidade_planejada") or 0)
        if status == db.STATUS_PLANEJADA:
            proxima = "Iniciar o diário de sondagem"
        elif status == db.STATUS_EXECUCAO:
            proxima = (
                "Encerrar e revisar o perfil"
                if atual >= planejada - db.TOLERANCIA_PROFUNDIDADE
                else "Registrar o próximo intervalo"
            )
        else:
            poco = db.obter_poco_monitoramento(int(linha["id"]), CAMINHO_BANCO)
            desenvolvimento = db.obter_desenvolvimento(int(linha["id"]), CAMINHO_BANCO)
            if not poco:
                proxima = "Cadastrar o perfil construtivo do poço"
            elif not desenvolvimento:
                proxima = "Registrar o desenvolvimento"
            else:
                proxima = "Gerar relatório Word ou Excel"
        linhas.append(
            {
                "projeto": linha["projeto_nome"],
                "sondagem": linha["nome_furo"],
                "status": rotulo_status(status),
                "executada_m": atual,
                "planejada_m": planejada,
                "NA_m": linha.get("nivel_agua_estatico"),
                "próxima_ação": proxima,
            }
        )
    st.dataframe(pd.DataFrame(linhas), width="stretch", hide_index=True)

    st.markdown("#### Fluxo recomendado")
    colunas = st.columns(4)
    etapas = [
        ("1. Localizar", "Projeto, CRS, GPS ou coordenadas coladas."),
        ("2. Executar", "Intervalos litológicos, amostras, VOC e leituras de NA."),
        ("3. Instalar", "Tubos, filtro, pré-filtro, selos e proteção superficial."),
        ("4. Entregar", "Desenvolvimento, fotos, perfis e relatório técnico."),
    ]
    for coluna, (titulo, descricao) in zip(colunas, etapas):
        coluna.markdown(
            f"<div class='cartao-painel'><strong>{titulo}</strong><br>{descricao}</div>",
            unsafe_allow_html=True,
        )


def renderizar_aba_instalacao_poco() -> None:
    """Cadastra o perfil construtivo do poço de monitoramento."""
    renderizar_cabecalho_etapa(
        4,
        "Instalação do poço de monitoramento",
        "Registre os dados gerais e, em seguida, os intervalos de cada componente construtivo.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    concluidas = sondagens[sondagens["status"] == db.STATUS_CONCLUIDA].copy()
    if concluidas.empty:
        st.info("Conclua uma sondagem antes de cadastrar a instalação do poço.")
        return

    rotulos = opcoes_sondagens(concluidas)
    sondagem_id = st.selectbox(
        "Sondagem / poço",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_instalacao_poco",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    poco = db.obter_poco_monitoramento(sondagem_id, CAMINHO_BANCO) or {}
    profundidade_limite = float(sondagem["profundidade_total"])

    st.markdown("#### Dados gerais da instalação")
    with st.form(f"form_poco_{sondagem_id}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            data_instalacao = st.date_input(
                "Data de instalação",
                value=(
                    pd.to_datetime(poco.get("data_instalacao")).date()
                    if poco.get("data_instalacao")
                    else date.today()
                ),
            )
            profundidade_poco = st.number_input(
                "Profundidade final do poço (m) *",
                min_value=0.1,
                max_value=profundidade_limite,
                value=float(poco.get("profundidade_poco") or profundidade_limite),
                step=0.1,
                format="%.3f",
            )
            diametro_perfuracao = st.number_input(
                "Diâmetro da perfuração (mm)",
                min_value=0.0,
                value=float(poco.get("diametro_perfuracao_mm") or 150.0),
                step=1.0,
            )
        with c2:
            diametro_revestimento = st.number_input(
                "Diâmetro nominal do revestimento (mm)",
                min_value=0.0,
                value=float(poco.get("diametro_revestimento_mm") or 50.0),
                step=1.0,
            )
            material_revestimento = st.text_input(
                "Material do revestimento",
                value=str(poco.get("material_revestimento") or "PVC geomecânico"),
            )
            fabricante = st.text_input(
                "Fabricante / modelo",
                value=str(poco.get("fabricante_modelo") or ""),
            )
        with c3:
            altura_boca = st.number_input(
                "Altura da boca do tubo sobre o terreno (m)",
                value=float(poco.get("altura_boca_tubo_m") or 0.0),
                step=0.01,
                format="%.3f",
            )
            cota_boca_padrao = float(sondagem["altitude"]) + altura_boca
            cota_boca = st.number_input(
                "Cota da boca do tubo (m)",
                value=float(poco.get("cota_boca_tubo") or cota_boca_padrao),
                step=0.01,
                format="%.3f",
            )
            camara_calcada = st.checkbox(
                "Possui câmara de calçada",
                value=bool(poco.get("camara_calcada", True)),
            )
        protecao = st.text_input(
            "Proteção superficial",
            value=str(poco.get("tipo_protecao_superficial") or "Câmara de calçada"),
        )
        tampa = st.text_input("Tampa / fechamento", value=str(poco.get("tampa") or ""))
        responsavel = st.text_input(
            "Responsável pela instalação",
            value=str(poco.get("responsavel_instalacao") or ""),
        )
        observacoes = st.text_area(
            "Observações construtivas",
            value=str(poco.get("observacoes") or ""),
            height=90,
        )
        salvar_poco = st.form_submit_button(
            "Salvar dados gerais do poço", type="primary", width="stretch"
        )
    if salvar_poco:
        try:
            db.salvar_poco_monitoramento(
                sondagem_id=sondagem_id,
                profundidade_poco=profundidade_poco,
                data_instalacao=data_instalacao,
                diametro_perfuracao_mm=diametro_perfuracao,
                diametro_revestimento_mm=diametro_revestimento,
                material_revestimento=material_revestimento,
                fabricante_modelo=fabricante,
                cota_boca_tubo=cota_boca,
                altura_boca_tubo_m=altura_boca,
                tipo_protecao_superficial=protecao,
                camara_calcada=camara_calcada,
                tampa=tampa,
                responsavel_instalacao=responsavel,
                observacoes=observacoes,
                caminho_banco=CAMINHO_BANCO,
            )
            registrar_mensagem("sucesso", "Dados gerais do poço salvos.")
            st.rerun()
        except db.ErroValidacao as erro:
            st.error(str(erro))

    poco = db.obter_poco_monitoramento(sondagem_id, CAMINHO_BANCO)
    if not poco:
        st.info("Salve os dados gerais para liberar o cadastro dos intervalos construtivos.")
        return

    st.divider()
    st.markdown("#### Componentes por intervalo")
    st.caption(
        "Cadastre tubo cego, seção filtrante, pré-filtro, selo de bentonita, cimentação "
        "e sedimentador. O aplicativo verifica sobreposições incompatíveis."
    )
    with st.form(f"form_intervalo_construtivo_{sondagem_id}", clear_on_submit=True):
        col_1, col_2, col_3 = st.columns(3)
        with col_1:
            componente = st.selectbox(
                "Componente *", options=db.COMPONENTES_CONSTRUTIVOS_VALIDOS
            )
            inicio = st.number_input(
                "Profundidade inicial (m) *",
                min_value=0.0,
                max_value=float(poco["profundidade_poco"]),
                value=0.0,
                step=0.1,
                format="%.3f",
            )
            final = st.number_input(
                "Profundidade final (m) *",
                min_value=0.001,
                max_value=float(poco["profundidade_poco"]),
                value=min(1.0, float(poco["profundidade_poco"])),
                step=0.1,
                format="%.3f",
            )
        with col_2:
            material = st.text_input("Material", placeholder="Ex.: PVC, areia selecionada, bentonita")
            especificacao = st.text_area(
                "Especificação", height=86, placeholder="Ex.: tubo DN 50, ranhura contínua"
            )
            granulometria = st.text_input("Granulometria / faixa do material")
        with col_3:
            diametro_texto = st.text_input("Diâmetro do componente (mm)")
            ranhura_texto = st.text_input("Abertura da ranhura (mm)")
            st.caption("Deixe os campos dimensionais vazios quando não se aplicarem.")
        adicionar_intervalo = st.form_submit_button(
            "Adicionar componente", type="primary", width="stretch"
        )
    if adicionar_intervalo:
        try:
            db.adicionar_intervalo_construtivo(
                sondagem_id=sondagem_id,
                componente=componente,
                profundidade_inicial=inicio,
                profundidade_final=final,
                material=material,
                especificacao=especificacao,
                diametro_mm=_numero_opcional_texto(diametro_texto, "diâmetro"),
                abertura_ranhura_mm=_numero_opcional_texto(ranhura_texto, "ranhura"),
                granulometria=granulometria,
                caminho_banco=CAMINHO_BANCO,
            )
            registrar_mensagem("sucesso", "Componente construtivo adicionado.")
            st.rerun()
        except db.ErroValidacao as erro:
            st.error(str(erro))

    intervalos = db.listar_intervalos_construtivos(sondagem_id, CAMINHO_BANCO)
    if intervalos.empty:
        st.warning("Ainda não há componentes construtivos cadastrados.")
    else:
        st.dataframe(intervalos, width="stretch", hide_index=True)
        mapa_intervalos = {
            int(linha["id"]): (
                f"{linha['componente']} | {float(linha['profundidade_inicial']):.2f}-"
                f"{float(linha['profundidade_final']):.2f} m"
            )
            for _, linha in intervalos.iterrows()
        }
        intervalo_remover = st.selectbox(
            "Componente para remover",
            options=list(mapa_intervalos),
            format_func=lambda valor: mapa_intervalos[valor],
            key=f"remover_componente_{sondagem_id}",
        )
        if st.button(
            "Remover componente selecionado",
            width="stretch",
            key=f"botao_remover_componente_{sondagem_id}",
        ):
            db.remover_intervalo_construtivo(intervalo_remover, CAMINHO_BANCO)
            st.rerun()

    valido, erros, avisos, metricas = db.validar_perfil_construtivo(
        sondagem_id, CAMINHO_BANCO
    )
    col_status, col_filtro = st.columns(2)
    col_status.metric("Validação construtiva", "Válido" if valido else "Pendente")
    col_filtro.metric(
        "Comprimento filtrante",
        f"{float(metricas.get('comprimento_filtro', 0)):.2f} m",
    )
    for erro in erros:
        st.error(erro)
    for aviso in avisos:
        st.warning(aviso)

    camadas = db.listar_camadas(sondagem_id, CAMINHO_BANCO)
    if not camadas.empty:
        try:
            figura = viz.criar_perfil_construtivo(sondagem, camadas, poco, intervalos)
            st.plotly_chart(figura, width="stretch", config={"displaylogo": False})
            png = _figura_para_png(figura, 2000, int(figura.layout.height or 1200))
            if png:
                st.download_button(
                    "Baixar perfil construtivo em PNG",
                    data=png,
                    file_name=f"perfil_construtivo_{_nome_arquivo_seguro(sondagem['nome_furo'])}.png",
                    mime="image/png",
                    width="stretch",
                )
        except Exception as erro:
            st.error(f"Não foi possível montar o perfil construtivo: {erro}")


def renderizar_galeria_fotos(sondagem: dict[str, Any]) -> None:
    """Permite fotografar, enviar, consultar e remover imagens da sondagem."""
    sondagem_id = int(sondagem["id"])
    st.write(
        "As imagens são reduzidas e gravadas no backup SQLite desta sessão. "
        "Use legendas objetivas para que apareçam corretamente no relatório."
    )
    origem_foto = st.radio(
        "Origem da imagem",
        ["Câmera do celular", "Arquivos do dispositivo"],
        horizontal=True,
        key=f"origem_foto_{sondagem_id}",
    )
    arquivos: list[Any] = []
    if origem_foto == "Câmera do celular":
        foto_camera = st.camera_input(
            "Fotografar agora", key=f"camera_sondagem_{sondagem_id}"
        )
        if foto_camera is not None:
            arquivos = [foto_camera]
    else:
        arquivos_upload = st.file_uploader(
            "Selecionar fotografias",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key=f"upload_fotos_{sondagem_id}",
        )
        arquivos = list(arquivos_upload or [])

    categoria = st.selectbox(
        "Categoria",
        options=db.CATEGORIAS_FOTOS_VALIDAS,
        key=f"categoria_foto_{sondagem_id}",
    )
    associar_profundidade = st.checkbox(
        "Associar uma profundidade",
        key=f"associar_profundidade_foto_{sondagem_id}",
    )
    profundidade_foto = None
    if associar_profundidade:
        profundidade_foto = st.number_input(
            "Profundidade da fotografia (m)",
            min_value=0.0,
            max_value=float(sondagem["profundidade_total"]),
            value=0.0,
            step=0.1,
            format="%.3f",
            key=f"profundidade_foto_{sondagem_id}",
        )
    legenda = st.text_area(
        "Legenda",
        placeholder="Ex.: instalação da seção filtrante entre 12 e 18 m.",
        key=f"legenda_foto_{sondagem_id}",
    )
    if st.button(
        f"Salvar {len(arquivos)} fotografia(s)",
        type="primary",
        width="stretch",
        disabled=not arquivos,
        key=f"salvar_fotos_{sondagem_id}",
    ):
        try:
            for arquivo in arquivos:
                conteudo, largura, altura, mime, nome = _preparar_imagem_upload(arquivo)
                db.adicionar_foto_sondagem(
                    sondagem_id=sondagem_id,
                    categoria=categoria,
                    nome_arquivo=nome,
                    mime_type=mime,
                    conteudo=conteudo,
                    legenda=legenda,
                    profundidade_m=profundidade_foto,
                    largura_px=largura,
                    altura_px=altura,
                    caminho_banco=CAMINHO_BANCO,
                )
            registrar_mensagem("sucesso", f"{len(arquivos)} fotografia(s) salva(s).")
            st.rerun()
        except db.ErroValidacao as erro:
            st.error(str(erro))

    fotos = db.listar_fotos_sondagem(sondagem_id, CAMINHO_BANCO)
    st.markdown(f"#### Galeria ({len(fotos)})")
    if not fotos:
        st.caption("Nenhuma fotografia cadastrada.")
        return
    colunas = st.columns(3)
    for indice, foto_meta in enumerate(fotos):
        foto = db.obter_foto_sondagem(int(foto_meta["id"]), CAMINHO_BANCO)
        if not foto:
            continue
        with colunas[indice % 3]:
            st.image(bytes(foto["conteudo"]), width="stretch")
            profundidade = foto.get("profundidade_m")
            texto_prof = "" if profundidade is None else f" | {float(profundidade):.2f} m"
            st.caption(
                f"**{foto['categoria']}**{texto_prof}\n\n"
                f"{foto.get('legenda') or 'Sem legenda'}"
            )
            if st.button(
                "Remover",
                key=f"remover_foto_{foto['id']}",
                width="stretch",
            ):
                db.remover_foto_sondagem(int(foto["id"]), CAMINHO_BANCO)
                st.rerun()


def renderizar_aba_desenvolvimento_fotos() -> None:
    """Registra desenvolvimento, leituras cronológicas e documentação fotográfica."""
    renderizar_cabecalho_etapa(
        5,
        "Desenvolvimento e documentação",
        "Registre o procedimento de desenvolvimento e organize as fotografias do furo e do poço.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    concluidas = sondagens[sondagens["status"] == db.STATUS_CONCLUIDA].copy()
    if concluidas.empty:
        st.info("Conclua uma sondagem antes de registrar desenvolvimento e fotografias.")
        return
    rotulos = opcoes_sondagens(concluidas)
    sondagem_id = st.selectbox(
        "Sondagem / poço",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_desenvolvimento_fotos",
    )
    sondagem = db.obter_sondagem(sondagem_id, CAMINHO_BANCO)
    aba_desenvolvimento, aba_fotos = st.tabs(["Desenvolvimento do poço", "Fotografias"])

    with aba_desenvolvimento:
        desenvolvimento = db.obter_desenvolvimento(sondagem_id, CAMINHO_BANCO) or {}
        realizado = st.radio(
            "O desenvolvimento foi realizado?",
            options=[True, False],
            format_func=lambda valor: "Sim" if valor else "Não",
            horizontal=True,
            index=0 if desenvolvimento.get("realizado", True) else 1,
            key=f"realizado_{sondagem_id}",
        )
        if realizado:
            with st.form(f"form_desenvolvimento_{sondagem_id}"):
                c1, c2, c3 = st.columns(3)
                with c1:
                    data_desenvolvimento = st.date_input(
                        "Data",
                        value=(
                            pd.to_datetime(desenvolvimento.get("data")).date()
                            if desenvolvimento.get("data")
                            else date.today()
                        ),
                    )
                    metodo = st.selectbox(
                        "Método *",
                        options=db.METODOS_DESENVOLVIMENTO_VALIDOS,
                        index=(
                            db.METODOS_DESENVOLVIMENTO_VALIDOS.index(desenvolvimento["metodo"])
                            if desenvolvimento.get("metodo") in db.METODOS_DESENVOLVIMENTO_VALIDOS
                            else 0
                        ),
                    )
                    duracao = st.text_input(
                        "Duração total (min)", value=str(desenvolvimento.get("duracao_min") or "")
                    )
                    profundidade_equipamento = st.text_input(
                        "Profundidade do equipamento (m)",
                        value=str(desenvolvimento.get("profundidade_equipamento_m") or ""),
                    )
                with c2:
                    na_antes = st.text_input("NA antes (m)", value=str(desenvolvimento.get("na_antes_m") or ""))
                    na_depois = st.text_input("NA depois (m)", value=str(desenvolvimento.get("na_depois_m") or ""))
                    vazao = st.text_input("Vazão (L/min)", value=str(desenvolvimento.get("vazao_l_min") or ""))
                    volume = st.text_input(
                        "Volume retirado (L)", value=str(desenvolvimento.get("volume_retirado_l") or "")
                    )
                with c3:
                    turbidez_inicial = st.text_input(
                        "Turbidez inicial (NTU)", value=str(desenvolvimento.get("turbidez_inicial_ntu") or "")
                    )
                    turbidez_final = st.text_input(
                        "Turbidez final (NTU)", value=str(desenvolvimento.get("turbidez_final_ntu") or "")
                    )
                    ph_final = st.text_input("pH final", value=str(desenvolvimento.get("ph_final") or ""))
                    condutividade = st.text_input(
                        "Condutividade final (µS/cm)",
                        value=str(desenvolvimento.get("condutividade_final_us_cm") or ""),
                    )
                    temperatura = st.text_input(
                        "Temperatura final (°C)", value=str(desenvolvimento.get("temperatura_final_c") or "")
                    )
                responsavel = st.text_input(
                    "Responsável", value=str(desenvolvimento.get("responsavel") or "")
                )
                observacoes = st.text_area(
                    "Observações", value=str(desenvolvimento.get("observacoes") or ""), height=90
                )
                salvar = st.form_submit_button(
                    "Salvar desenvolvimento", type="primary", width="stretch"
                )
            if salvar:
                try:
                    db.salvar_desenvolvimento(
                        sondagem_id=sondagem_id,
                        realizado=True,
                        data_desenvolvimento=data_desenvolvimento,
                        metodo=metodo,
                        duracao_min=_numero_opcional_texto(duracao, "duração"),
                        profundidade_equipamento_m=_numero_opcional_texto(
                            profundidade_equipamento, "profundidade do equipamento"
                        ),
                        na_antes_m=_numero_opcional_texto(na_antes, "NA antes"),
                        na_depois_m=_numero_opcional_texto(na_depois, "NA depois"),
                        vazao_l_min=_numero_opcional_texto(vazao, "vazão"),
                        volume_retirado_l=_numero_opcional_texto(volume, "volume"),
                        turbidez_inicial_ntu=_numero_opcional_texto(
                            turbidez_inicial, "turbidez inicial"
                        ),
                        turbidez_final_ntu=_numero_opcional_texto(
                            turbidez_final, "turbidez final"
                        ),
                        ph_final=_numero_opcional_texto(ph_final, "pH"),
                        condutividade_final_us_cm=_numero_opcional_texto(
                            condutividade, "condutividade"
                        ),
                        temperatura_final_c=_numero_opcional_texto(
                            temperatura, "temperatura"
                        ),
                        responsavel=responsavel,
                        observacoes=observacoes,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Desenvolvimento salvo.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))
        else:
            with st.form(f"form_nao_desenvolvido_{sondagem_id}"):
                motivo = st.text_area(
                    "Motivo da não realização *",
                    value=str(desenvolvimento.get("motivo_nao_realizado") or ""),
                )
                observacoes = st.text_area(
                    "Observações", value=str(desenvolvimento.get("observacoes") or "")
                )
                salvar = st.form_submit_button(
                    "Registrar não realização", type="primary", width="stretch"
                )
            if salvar:
                try:
                    db.salvar_desenvolvimento(
                        sondagem_id=sondagem_id,
                        realizado=False,
                        motivo_nao_realizado=motivo,
                        observacoes=observacoes,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Situação do desenvolvimento registrada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

        desenvolvimento = db.obter_desenvolvimento(sondagem_id, CAMINHO_BANCO)
        if desenvolvimento and desenvolvimento.get("realizado"):
            st.markdown("#### Leituras ao longo do desenvolvimento")
            with st.form(f"form_leitura_desenvolvimento_{sondagem_id}", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    tempo = st.number_input("Tempo acumulado (min) *", min_value=0.0, value=0.0, step=5.0)
                    nivel = st.text_input("NA (m)")
                with c2:
                    vazao_leitura = st.text_input("Vazão (L/min)")
                    turbidez = st.text_input("Turbidez (NTU)")
                with c3:
                    ph = st.text_input("pH")
                    condutividade_leitura = st.text_input("Condutividade (µS/cm)")
                with c4:
                    temperatura_leitura = st.text_input("Temperatura (°C)")
                    observacao_leitura = st.text_input("Observação")
                adicionar = st.form_submit_button("Adicionar leitura", width="stretch")
            if adicionar:
                try:
                    db.adicionar_leitura_desenvolvimento(
                        sondagem_id=sondagem_id,
                        tempo_min=tempo,
                        nivel_agua_m=_numero_opcional_texto(nivel, "NA"),
                        vazao_l_min=_numero_opcional_texto(vazao_leitura, "vazão"),
                        turbidez_ntu=_numero_opcional_texto(turbidez, "turbidez"),
                        ph=_numero_opcional_texto(ph, "pH"),
                        condutividade_us_cm=_numero_opcional_texto(
                            condutividade_leitura, "condutividade"
                        ),
                        temperatura_c=_numero_opcional_texto(
                            temperatura_leitura, "temperatura"
                        ),
                        observacoes=observacao_leitura,
                        caminho_banco=CAMINHO_BANCO,
                    )
                    registrar_mensagem("sucesso", "Leitura de desenvolvimento adicionada.")
                    st.rerun()
                except db.ErroValidacao as erro:
                    st.error(str(erro))

            leituras = db.listar_leituras_desenvolvimento(sondagem_id, CAMINHO_BANCO)
            st.dataframe(leituras, width="stretch", hide_index=True)
            if not leituras.empty:
                figura = viz.criar_grafico_desenvolvimento(leituras)
                st.plotly_chart(figura, width="stretch", config={"displaylogo": False})
                mapa_leituras = {
                    int(linha["id"]): f"{float(linha['tempo_min']):.1f} min | ID {int(linha['id'])}"
                    for _, linha in leituras.iterrows()
                }
                leitura_id = st.selectbox(
                    "Leitura para remover",
                    options=list(mapa_leituras),
                    format_func=lambda valor: mapa_leituras[valor],
                    key=f"remover_leitura_desenvolvimento_{sondagem_id}",
                )
                if st.button(
                    "Remover leitura",
                    width="stretch",
                    key=f"botao_remover_leitura_desenvolvimento_{sondagem_id}",
                ):
                    db.remover_leitura_desenvolvimento(leitura_id, CAMINHO_BANCO)
                    st.rerun()

    with aba_fotos:
        renderizar_galeria_fotos(sondagem)


def renderizar_aba_perfis_relatorios() -> None:
    """Centraliza perfis, exportações e relatórios Word/Excel."""
    renderizar_cabecalho_etapa(
        6,
        "Perfis e relatórios",
        "Confira as figuras e gere um relatório Word editável ou uma pasta de dados Excel.",
    )
    sondagens = db.listar_sondagens(caminho_banco=CAMINHO_BANCO)
    concluidas = sondagens[sondagens["status"] == db.STATUS_CONCLUIDA].copy()
    if concluidas.empty:
        st.info("Conclua uma sondagem para liberar os perfis e relatórios finais.")
        return
    rotulos = opcoes_sondagens(concluidas)
    sondagem_id = st.selectbox(
        "Sondagem",
        options=list(rotulos),
        format_func=lambda valor: rotulos[valor],
        key="seletor_relatorio",
    )
    dados = db.obter_dados_completos_sondagem(sondagem_id, CAMINHO_BANCO)
    sondagem = dados["sondagem"]
    camadas = dados["camadas"]
    coletas = dados["coletas"]
    voc = dados["voc"]

    perfil = viz.criar_perfil_litologico(sondagem, camadas, coletas, voc)
    st.markdown("#### Perfil litológico e curva de VOC")
    st.plotly_chart(perfil, width="stretch", config={"displaylogo": False})

    perfil_construtivo = None
    if dados.get("poco"):
        st.markdown("#### Perfil construtivo do poço")
        perfil_construtivo = viz.criar_perfil_construtivo(
            sondagem,
            camadas,
            dados["poco"],
            dados["intervalos_construtivos"],
        )
        st.plotly_chart(
            perfil_construtivo, width="stretch", config={"displaylogo": False}
        )
    else:
        st.info(
            "O relatório pode ser gerado sem perfil construtivo. Cadastre a instalação "
            "para incluir a imagem e os materiais do poço."
        )

    st.divider()
    st.markdown("#### Gerar arquivos")
    incluir_fotos = st.checkbox(
        "Incluir fotografias no Word e no Excel",
        value=True,
        key=f"incluir_fotos_relatorio_{sondagem_id}",
    )
    limite_fotos = st.slider(
        "Número máximo de fotografias no relatório",
        min_value=1,
        max_value=40,
        value=20,
        disabled=not incluir_fotos,
        key=f"limite_fotos_relatorio_{sondagem_id}",
    )
    st.caption(
        "O Word é indicado para edição e emissão do relatório. O Excel reúne os dados "
        "tabulares, validações e imagens como anexo técnico."
    )
    if st.button(
        "Preparar Word e Excel",
        type="primary",
        width="stretch",
        key=f"gerar_relatorios_{sondagem_id}",
    ):
        with st.spinner("Montando figuras e relatórios..."):
            altura_perfil = int(perfil.layout.height or 1400)
            imagem_perfil = _figura_para_png(perfil, 2100, altura_perfil)
            imagem_construcao = None
            if perfil_construtivo is not None:
                imagem_construcao = _figura_para_png(
                    perfil_construtivo,
                    2100,
                    int(perfil_construtivo.layout.height or 1400),
                )
            imagem_desenvolvimento = None
            if not dados["leituras_desenvolvimento"].empty:
                figura_desenvolvimento = viz.criar_grafico_desenvolvimento(
                    dados["leituras_desenvolvimento"]
                )
                imagem_desenvolvimento = _figura_para_png(
                    figura_desenvolvimento, 1800, 760
                )
            try:
                st.session_state[f"docx_relatorio_{sondagem_id}"] = reporting.gerar_relatorio_word(
                    dados,
                    imagem_perfil=imagem_perfil,
                    imagem_construcao=imagem_construcao,
                    imagem_desenvolvimento=imagem_desenvolvimento,
                    incluir_fotos=incluir_fotos,
                    limite_fotos=limite_fotos,
                )
                st.session_state[f"xlsx_relatorio_{sondagem_id}"] = reporting.gerar_relatorio_excel(
                    dados,
                    imagem_perfil=imagem_perfil,
                    imagem_construcao=imagem_construcao,
                    imagem_desenvolvimento=imagem_desenvolvimento,
                    incluir_fotos=incluir_fotos,
                    limite_fotos=limite_fotos,
                )
                st.session_state[f"png_perfil_{sondagem_id}"] = imagem_perfil
                st.session_state[f"png_construcao_{sondagem_id}"] = imagem_construcao
                registrar_mensagem("sucesso", "Relatórios preparados para download.")
                st.rerun()
            except Exception as erro:
                st.error(f"Não foi possível gerar os relatórios: {erro}")

    nome_base = _nome_arquivo_seguro(str(sondagem["nome_furo"]))
    word = st.session_state.get(f"docx_relatorio_{sondagem_id}")
    excel = st.session_state.get(f"xlsx_relatorio_{sondagem_id}")
    if word or excel:
        c_word, c_excel = st.columns(2)
        with c_word:
            if word:
                st.download_button(
                    "Baixar relatório Word (.docx)",
                    data=word,
                    file_name=f"relatorio_{nome_base}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    width="stretch",
                )
        with c_excel:
            if excel:
                st.download_button(
                    "Baixar relatório Excel (.xlsx)",
                    data=excel,
                    file_name=f"relatorio_{nome_base}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
        c_png_1, c_png_2 = st.columns(2)
        with c_png_1:
            png_perfil = st.session_state.get(f"png_perfil_{sondagem_id}")
            if png_perfil:
                st.download_button(
                    "Baixar perfil litológico em PNG",
                    data=png_perfil,
                    file_name=f"perfil_litologico_{nome_base}.png",
                    mime="image/png",
                    width="stretch",
                )
        with c_png_2:
            png_construcao = st.session_state.get(f"png_construcao_{sondagem_id}")
            if png_construcao:
                st.download_button(
                    "Baixar perfil construtivo em PNG",
                    data=png_construcao,
                    file_name=f"perfil_construtivo_{nome_base}.png",
                    mime="image/png",
                    width="stretch",
                )

def executar_aplicativo() -> None:
    """Monta a interface completa em navegação sequencial e amigável."""
    st.title("💧 Diário de Sondagem e Poços de Monitoramento")
    st.caption(
        "Locação por GPS ou coordenadas manuais, diário de campo, litologia, zona vadosa, "
        "perfil construtivo, desenvolvimento, fotografias e relatórios Word/Excel."
    )
    exibir_mensagem_pendente()

    paginas = [
        "Visão geral",
        "1. Projeto e locação",
        "2. Diário de sondagem",
        "3. Encerramento da perfuração",
        "4. Instalação do poço",
        "5. Desenvolvimento e fotos",
        "6. Perfis e relatórios",
        "7. Mapa e seção",
        "8. Importação em lote",
    ]

    with st.sidebar:
        st.header("Navegação")
        st.caption(f"Versão {VERSAO_APP}")
        pagina = st.radio(
            "Etapa de trabalho",
            options=paginas,
            label_visibility="collapsed",
            key="pagina_atual",
        )
        st.divider()
        st.caption("Sistema de referência padrão")
        st.markdown("**SIRGAS 2000 / UTM 23S — EPSG:31983**")
        st.caption(
            "Também são aceitos GPS/WGS84, SIRGAS 2000 geográfico, outras zonas UTM "
            "e qualquer código EPSG reconhecido."
        )
        with st.expander("Banco, backup e restauração", expanded=False):
            renderizar_controles_banco()
        st.caption(
            "Antes de um novo deploy no Streamlit Cloud, baixe o backup SQLite. "
            "Os dados da implantação pública ficam associados à sessão do navegador."
        )

    if pagina == "Visão geral":
        renderizar_painel_geral()
    elif pagina == "1. Projeto e locação":
        renderizar_aba_locacao()
    elif pagina == "2. Diário de sondagem":
        renderizar_aba_diario_campo()
    elif pagina == "3. Encerramento da perfuração":
        renderizar_aba_encerramento()
    elif pagina == "4. Instalação do poço":
        renderizar_aba_instalacao_poco()
    elif pagina == "5. Desenvolvimento e fotos":
        renderizar_aba_desenvolvimento_fotos()
    elif pagina == "6. Perfis e relatórios":
        renderizar_aba_perfis_relatorios()
    elif pagina == "7. Mapa e seção":
        renderizar_aba_mapa_secao()
    elif pagina == "8. Importação em lote":
        renderizar_aba_importacao()


if __name__ == "__main__":
    executar_aplicativo()
