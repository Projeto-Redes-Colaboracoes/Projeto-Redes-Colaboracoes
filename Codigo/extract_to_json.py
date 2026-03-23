#!/usr/bin/env python3
"""
Pequeno Extrator: usa o parser parserBSLattes para ler um HTML Lattes e escrever JSON.

Uso:
  python extract_to_json.py --input <html-file> [--output out.json] [--csv <csv-file>]

O script lê o HTML em UTF-8, executa o parser e escreve um JSON
com as chaves de metadados e listas extraídas do parser (incluindo
areasAtuacao e linhasPesquisa).
Se um CSV for fornecido, também inclui o campo "sexo".
"""
from pathlib import Path
import json
import argparse
import sys
import csv
from datetime import datetime

import logging

from parserBSLattes import ParserBSLattes


def load_csv_data(csv_path: Path) -> dict:
    """
    Carrega um CSV e cria um mapeamento de ID Lattes para sexo
    Retorna um dicionário {lattes_id: sexo}
    Se o CSV não for encontrado ou não for fornecido, retorna um dicionário vazio
    """
    mapping = {}
    if not csv_path or not csv_path.exists():
        return mapping
    
    with csv_path.open(encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            lattes_id = row.get('lattes', '').strip()
            sexo = row.get('sexo', '').strip()
            if lattes_id and lattes_id != '(null)':
                mapping[lattes_id] = sexo
    return mapping


def build_output(parser: ParserBSLattes, sexo: str = None) -> dict:
    output = {
        'idLattes': getattr(parser, 'idLattes', ''),
        'nomeCompleto': getattr(parser, 'nomeCompleto', ''),
        'sexo': sexo,
        'listaNomesCitacao': getattr(parser, 'listaNomesCitacao', []),
        'areasAtuacao': getattr(parser, 'areasAtuacao', []),
        'linhasPesquisa': getattr(parser, 'linhasPesquisa', []),
        'listaPB': getattr(parser, 'listaPB', []),
        'listaPT': getattr(parser, 'listaPT', []),
        'listaPA': getattr(parser, 'listaPA', []),
        'listaPP': getattr(parser, 'listaPP', []),
        'listaOA': getattr(parser, 'listaOA', []),
        'listaOC': getattr(parser, 'listaOC', []),
    }
    return output


def parse_file(
    inpath: Path,
    debug: bool = False,
    debug_log_path: Path = None,
) -> ParserBSLattes:
    # Sempre lê como UTF-8 (estrito) para garantir texto válido ao parser.
    html = inpath.read_text(encoding='utf-8')
    
    # Cria um parser sem parsear (passa string vazia para evitar auto-parse)
    p = ParserBSLattes(inpath.stem, "")
    
    if debug:
        p.enable_debug(
            enable=True,
            max_snippet=500,
            level=logging.DEBUG,
            log_file=str(debug_log_path) if debug_log_path else None,
        )
    
    # Parseia o HTML com debug habilitado
    p.parse(html)
    
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description='Extract Lattes HTML to JSON using ParserBSLattes')
    ap.add_argument('--input', '-i', required=True, help='Path to Lattes HTML file')
    ap.add_argument('--output', '-o', help='Output JSON path (defaults to input.json)')
    ap.add_argument('--csv', '-c', help='Path to CSV file with additional data (e.g., ServidoresUFSCar_Lattes.csv)')
    ap.add_argument('--debug', '-d', action='store_true', help='Enable debug logging')
    ap.add_argument('--debug-log', help='Debug log file path (default: ./debug/logs/<input>_<timestamp>.log when --debug is used)')
    args = ap.parse_args(argv)

    inpath = Path(args.input)
    if not inpath.exists():
        print(f'Input file not found: {inpath}', file=sys.stderr)
        return 2

    # Carrega CSV se fornecido
    csv_mapping = {}
    if args.csv:
        csv_path = Path(args.csv)
        csv_mapping = load_csv_data(csv_path)

    debug_log_path = None
    if args.debug:
        if args.debug_log:
            debug_log_path = Path(args.debug_log)
        else:
            debug_dir = Path('debug/logs')
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            debug_log_path = debug_dir / f'{inpath.stem}_{timestamp}.log'

        # Garante que o diretório de destino exista para caminho customizado.
        debug_log_path.parent.mkdir(parents=True, exist_ok=True)

    parser = parse_file(inpath, debug=args.debug, debug_log_path=debug_log_path)
    
    # Coleta sexo do CSV se disponivel
    lattes_id = getattr(parser, 'idLattes', '')
    sexo = csv_mapping.get(lattes_id)
    
    out = build_output(parser, sexo=sexo)

    outpath = Path(args.output) if args.output else inpath.with_suffix('.json')
    # Garante que o JSON seja codificado em UTF-8 e preserve caracteres unicode
    outpath.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {outpath}')
    if debug_log_path:
        print(f'Debug log: {debug_log_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
