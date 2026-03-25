## Projeto de Redes de Colaborações

**Aluno**: Beatriz Rogers Tripoli Barbosa  
**Orientadora**: Profª Drª Sahudy Montenegro González  
**Instituição**: Universidade Federal de São Carlos (UFSCar) - Graduação em Ciências da Computação

### Objetivo

Criar um banco de dados estruturado a partir de currículos Lattes de servidores da UFSCar para análise de redes de colaboração voltada ao Observatório Mulheres da UFSCar.

## Estrutura do Projeto

### Fontes de Dados

- **CSV de Servidores UFSCar**: Arquivo contendo informações dos servidores da universidade com seus IDs Lattes
- **Currículos Lattes**: Arquivos HTML brutos obtidos através do extrator Lattes desenvolvido pelo Dr. Jesus P. Mena-Chalco. Os arquivos utilizados são os brutos, não processados pelo extrator

### Pasta de Exemplo

Localizada em `Exemplo de Currículo/`, contém um exemplo completo do fluxo de processamento:

- **`9826346918182685`**: arquivo HTML bruto (cache do extrator do Dr. Jesus P. Mena-Chalco)
- **`9826346918182685.json`**: arquivo resultante após o parser (`parserBSLattes.py`) e a transformação para JSON (`extract_to_json.py`)

### Arquivos Executáveis

#### `extract_servidores_csv.py`
Extrai dados do CSV de servidores da UFSCar, gerando uma lista de servidores que possuem currículo Lattes registrado.

**Entrada**: Arquivo CSV (codificado em UTF-8) com colunas `lattes` (ID Lattes) e `nome` (nome completo), separadas por ponto-e-vírgula

**Saída**: Lista de dicionários com as chaves:
- `idLattes`: identificador único do currículo Lattes  
- `nomeCompleto`: nome do servidor

**Uso**:
```bash
python extract_servidores_csv.py --input <csv-file> [--output out.list]
```

#### `parserBSLattes.py`
Parser para extrair informações estruturadas dos currículos Lattes em formato HTML. Extrai metadados e dados estruturados como áreas de atuação e linhas de pesquisa, entre outros.

#### `extract_to_json.py`  
Utiliza o parser `parserBSLattes.py` para converter arquivos HTML de currículos Lattes em formato JSON estruturado.

**Entrada**: Arquivo HTML Lattes (codificado em UTF-8), opcionalmente um CSV de servidores

**Saída**: Arquivo JSON com:
- Metadados do currículo
- Listas estruturadas extraídas pelo parser
- Campo `sexo` (se CSV for fornecido)

**Uso**:
```bash
python extract_to_json.py --input <html-file> [--output out.json] [--csv <csv-file>]
```

#### `filling_idlattes.py`
Preenche ou valida informações de IDs Lattes em estruturas de dados.

### Scripts MongoDB

Localizado em `Mongo Scripts/`:

- **`normalize_mongo.py`**: Normaliza dados no MongoDB
- **`update_documents.py`**: Atualiza documentos na base de dados

## Trabalhos Escritos

Estudos e produções redigidas para fundamentação teórica do projeto, localizados em `Trabalhos Escritos/`:

- **Apresentação sobre Pré-processamento em PLN.pdf**: Estudo sobre técnicas de pré-processamento em Processamento de Linguagem Natural
- **Base para Entrevista com Andrea.pdf**: Material preparado para entrevista sobre aspectos do projeto
- **Resumo do Artigo - A Review and Analysis of Recommendation Systems in Collaboration Networks.pdf**: Resumo de artigo relevante sobre sistemas de recomendação em redes de colaboração

## Fluxo de Processamento

1. Extrair informações dos servidores do CSV (`extract_servidores_csv.py`)
2. Obter arquivos brutos de currículos Lattes (do extrator do Dr. Jesus P. Mena-Chalco)
3. Converter HTML Lattes para JSON (`extract_to_json.py` com `parserBSLattes.py`)
4. Processar e normalizar dados no MongoDB (scripts em `Mongo Scripts/`)
