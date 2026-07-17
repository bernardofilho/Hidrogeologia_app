# Diário de Sondagem e Poços de Monitoramento — versão 2.0

Aplicativo web em **Python + Streamlit** para registrar sondagens hidrogeológicas, descrever intervalos litológicos, acompanhar o nível d'água, documentar a instalação de poços de monitoramento e gerar relatórios técnicos em **Word e Excel**.

## Principais recursos

- fluxo orientado por etapas, adequado ao trabalho de campo;
- projetos, responsáveis e dados do contratante;
- sondagens planejadas, em execução e concluídas;
- coordenadas por GPS do celular, copiar e colar ou digitação manual;
- CRS padrão **SIRGAS 2000 / UTM 23S — EPSG:31983**;
- suporte a outras zonas UTM, EPSG:4674, WGS84 e outros códigos EPSG;
- preservação das coordenadas originais e transformação para o mapa;
- diário litológico contínuo, sem lacunas ou sobreposições;
- classificação da zona vadosa, trecho saturado e transição pelo NA;
- histórico de leituras de nível d'água;
- pontos de coleta e medições de VOC;
- perfil litológico com texturas e tabela descritiva sem sobreposição;
- perfil hidroestratigráfico em seção transversal;
- cadastro do perfil construtivo do poço de monitoramento;
- tubo cego, seção filtrante, sedimentador, pré-filtro, bentonita e cimentação;
- validação do filtro, pré-filtro, selos e profundidades;
- registro do desenvolvimento do poço e leituras cronológicas;
- gráfico de turbidez, vazão e NA durante o desenvolvimento;
- fotografias pela câmera do celular ou por upload;
- relatório técnico editável em Word;
- anexo estruturado em Excel;
- exportação de perfis em PNG;
- importação em lote por CSV;
- arquivo de teste com 48 sondagens;
- backup e restauração do banco SQLite.

## Fluxo de uso

1. **Projeto e locação** — identifique o projeto, selecione o CRS e registre a posição.
2. **Diário de sondagem** — inicie a execução e descreva cada intervalo na ordem encontrada.
3. **Encerramento da perfuração** — confira a continuidade e publique o perfil final.
4. **Instalação do poço** — registre os materiais e os intervalos construtivos.
5. **Desenvolvimento e fotos** — documente o procedimento e organize as imagens.
6. **Perfis e relatórios** — gere PNG, Word e Excel.
7. **Mapa e seção** — confira as posições e correlacione os furos concluídos.
8. **Importação em lote** — carregue dados existentes por CSV.

## Coordenadas

### GPS do celular

O botão **Usar GPS deste dispositivo** utiliza a geolocalização do navegador. Em celulares, autorize a localização precisa quando solicitado. A posição capturada em WGS84 é transformada automaticamente para o CRS selecionado.

A captura funciona melhor em uma URL HTTPS, como a fornecida pelo Streamlit Community Cloud.

### Copiar e colar

São aceitos, entre outros, estes formatos:

```text
330717,31 ; 7385947,28
X=330717.31  Y=7385947.28
-23.626338, -46.656487
latitude=-23.626338 longitude=-46.656487
```

O aplicativo permite informar a ordem dos valores e detecta pares geográficos comuns. Sempre confira a prévia no mapa antes de salvar.

### Entrada manual

Os campos de X/Y ou longitude/latitude são textos para facilitar colar valores diretamente de planilhas, mensagens, equipamentos topográficos e aplicativos móveis. Ponto e vírgula decimal são aceitos.

## Publicação no GitHub e Streamlit Community Cloud

1. Faça um backup do banco atual pelo painel lateral do aplicativo.
2. Substitua os arquivos da raiz do repositório pelos arquivos deste pacote.
3. Mantenha também a pasta oculta `.streamlit`.
4. Confirme o commit na branch utilizada pelo Streamlit, normalmente `main`.
5. No Streamlit Community Cloud, use como arquivo principal:

```text
streamlit_app.py
```

6. Aguarde a reinstalação das dependências do `requirements.txt`.
7. Abra o aplicativo e restaure o backup SQLite, quando necessário.

Estrutura esperada na raiz do repositório:

```text
app.py
db_manager.py
visualization.py
gps_component.py
reporting.py
streamlit_app.py
requirements.txt
exemplo.csv
sondagens_congonhas_importacao.csv
README.md
CHANGELOG.md
.streamlit/config.toml
```

## Persistência no Streamlit Cloud

`streamlit_app.py` inicia o banco no modo privado por sessão. Isso evita que visitantes compartilhem os mesmos dados, porém o armazenamento da sessão não deve ser tratado como permanente.

Rotina recomendada:

1. restaurar o backup ao iniciar um novo trabalho;
2. trabalhar normalmente;
3. baixar o backup SQLite antes de fechar a sessão ou publicar uma atualização.

Para uma implantação multiusuário permanente, a evolução recomendada é substituir o SQLite temporário por PostgreSQL e armazenamento externo para imagens.

## Importação CSV

O arquivo deve conter as colunas litológicas:

```text
projeto,sondagem_nome,altitude,nivel_agua,profundidade_inicial,profundidade_final,descricao,classificacao,tipo_aquifero
```

E um destes esquemas de coordenadas:

```text
crs_epsg,coordenada_x,coordenada_y
```

ou:

```text
latitude,longitude
```

O pacote inclui:

- `exemplo.csv` — conjunto pequeno;
- `sondagens_congonhas_importacao.csv` — conjunto de teste com 48 sondagens.

As fichas usadas para produzir o conjunto de 48 sondagens eram documentos digitalizados. Os dados devem ser conferidos pelo responsável técnico antes de utilização oficial.

## Relatórios

### Word

O arquivo `.docx` reúne:

- identificação do projeto e da sondagem;
- localização, CRS, origem e precisão da coordenada;
- dados de execução;
- perfil litológico e tabela descritiva;
- amostras, VOC e histórico do NA;
- perfil construtivo e materiais do poço;
- desenvolvimento e gráfico de acompanhamento;
- registro fotográfico;
- observações e nota de validação técnica.

### Excel

O arquivo `.xlsx` contém as abas:

- `Resumo`;
- `Litologia`;
- `Amostras_VOC_NA`;
- `Construcao_Poco`;
- `Desenvolvimento`;
- `Fotos`.

## Execução local opcional

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

Instalação e execução:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

Acesse `http://localhost:8501`.

## Docker opcional

```bash
docker compose up --build
```

Acesse `http://localhost:8501`. O volume `dados_hidro` preserva o banco entre reinicializações do contêiner.

## Observações técnicas

- As coordenadas são armazenadas no CRS informado e também normalizadas para SIRGAS 2000 geográfico para o mapa.
- A zona vadosa calculada a partir do NA pressupõe que a leitura represente o nível freático. Em aquíferos confinados, a carga piezométrica não é necessariamente o limite físico de saturação.
- A litologia não deve ser convertida automaticamente em parâmetros hidráulicos sem interpretação e dados de campo.
- A simbologia, as descrições e o relatório devem ser revisados e assinados por profissional habilitado antes da emissão oficial.
