# Diario de Sondagem Hidrogeologica - versao web

Aplicativo Streamlit com SQLite, Pandas, Plotly, Folium, SciPy e PyProj.

## Alteracoes desta versao

- SIRGAS 2000 / UTM zona 23S (EPSG:31983) e o CRS padrao de entrada.
- O usuario pode selecionar as zonas SIRGAS 2000 / UTM 18S a 25S, SIRGAS 2000 geografico, WGS 84 ou qualquer EPSG valido.
- O banco guarda as coordenadas originais e tambem latitude/longitude normalizadas em SIRGAS 2000 geografico (EPSG:4674) para o mapa.
- O fluxo segue a sequencia de uma sondagem: planejamento, inicio, intervalos, amostras/VOC/NA, conferencia e encerramento.
- Intervalos em execucao sao persistidos na tabela `rascunhos_camadas`; o perfil final so e publicado depois da validacao integral.
- A imagem do perfil possui legenda lateral e uma tabela inferior com todas as descricoes, eliminando a sobreposicao entre titulo, legenda e textos.
- A linha de NA e desenhada sem anotacoes automaticas do Plotly, evitando o texto residual `new text`.

## Fluxo de uso

1. Crie o projeto.
2. Planeje a sondagem e informe o CRS e as coordenadas.
3. Inicie a sondagem no Diario de Sondagem.
4. Registre cada intervalo de forma sequencial. A profundidade inicial e preenchida automaticamente.
5. Registre amostras, VOC e NA conforme a profundidade executada.
6. Na aba Encerramento e Perfil, informe a profundidade final e publique o perfil.
7. Gere mapa, perfil individual, CSV, PNG e secao transversal.

## CSV

O arquivo pode usar coordenadas genericas:

```text
crs_epsg,coordenada_x,coordenada_y
```

ou coordenadas geograficas SIRGAS 2000:

```text
latitude,longitude
```

O `exemplo.csv` usa EPSG:31983.

## Executar localmente

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

Abra `http://localhost:8501`.

## Docker

```bash
docker compose up --build
```

Abra `http://localhost:8501`. O volume `dados_hidro` preserva o banco.

## Streamlit Community Cloud

Publique o repositorio e use `streamlit_app.py` como arquivo principal. Nesse modo, cada sessao recebe um banco isolado; use o backup SQLite do painel lateral.
