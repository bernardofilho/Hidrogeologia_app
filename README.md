# Descrição Litológica de Sondagens — versão web

Aplicativo Streamlit para cadastro e visualização de sondagens hidrogeológicas, com SQLite, Pandas, Plotly e Folium, exibido diretamente em um componente HTML do Streamlit.

## Opção 1 — publicar no Streamlit Community Cloud

1. Crie um repositório no GitHub e envie todos os arquivos desta pasta, mantendo `.streamlit/config.toml`.
2. Acesse o Streamlit Community Cloud e escolha **Create app**.
3. Selecione o repositório, a branch e use `streamlit_app.py` como arquivo de entrada.
4. Em **Advanced settings**, escolha Python 3.12.
5. Clique em **Deploy**.

Nesse modo, cada sessão do navegador recebe um banco SQLite separado. Use o painel lateral para baixar um backup `.db` e restaurá-lo em outra sessão. Isso evita que usuários de uma implantação pública vejam ou alterem dados uns dos outros.

## Opção 2 — executar no navegador com Docker e dados persistentes

```bash
docker compose up --build
```

Depois, abra:

```text
http://localhost:8501
```

O volume Docker `dados_hidro` preserva o banco entre reinicializações do contêiner.

## Opção 3 — executar localmente sem Docker

```bash
python -m venv .venv
```

Linux ou macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Instale e execute:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

O navegador abrirá em `http://localhost:8501`. Nesse modo, o banco fica em `data/hidrogeologia.db`.

## Arquivos principais

- `streamlit_app.py`: entrada para hospedagem pública, com banco privado por sessão.
- `app.py`: aplicação completa e entrada para execução local ou Docker.
- `db_manager.py`: SQLite, validações, importação, backup e restauração.
- `visualization.py`: mapa, perfil litológico e seção hidroestratigráfica.
- `exemplo.csv`: dados fictícios para teste.
