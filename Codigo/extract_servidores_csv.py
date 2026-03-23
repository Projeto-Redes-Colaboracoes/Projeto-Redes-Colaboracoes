#!/usr/bin/env python3
"""
Pequeno Extrator: extrai dados de um csv de servidores e escreve uma lista baseada
em servidores que possuem um currículo lates (a ser utilizado em conjunto com o extrator
de currículos).

Uso:
  python extract_servidores_csv.py --input <csv-file> [--output out.list]

O script lê o CSV (UTF-8 por padrão) e escreve uma lista de dicionários com as chaves:
lattes, nome.
"""

from pathlib import Path
import csv 
import argparse

def extract_servidores_csv(input_file: Path) -> list:
    servidores = []
    with input_file.open(encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            idLattes = row.get('lattes', '').strip()
            nomeCompleto = row.get('nome', '').strip()
            if idLattes and idLattes != '(null)':
                servidores.append({
                    'idLattes': idLattes,
                    'nomeCompleto': nomeCompleto
                })
    return servidores

def main():
    parser = argparse.ArgumentParser(description='Extrai dados de um csv de servidores e escreve uma lista baseada em servidores que possuem um currículo lates.')
    parser.add_argument('--input', type=Path, required=True, help='Arquivo CSV de entrada')
    parser.add_argument('--output', type=Path, default=None, help='Arquivo de saída (padrão: stdout)')
    args = parser.parse_args()

    servidores = extract_servidores_csv(args.input)

    # Se o arquivo de saida ja existe, leia os IDs existentes para evitar duplicatas
    existing_ids = set()
    if args.output and args.output.exists():
        with args.output.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    # Extrai ID do formato "idLattes , nomeCompleto"
                    parts = line.split(' , ', 1)
                    if parts:
                        existing_ids.add(parts[0].strip())
    
    # Filtra entradas com IDs que ja existem
    if existing_ids:
        servidores = [s for s in servidores if s['idLattes'] not in existing_ids]

    output_data = '\n'.join([f"{s['idLattes']} , {s['nomeCompleto']}" for s in servidores])

    if args.output:
        # Update arquivo se existir, caso contrario cria novo
        mode = 'a' if args.output.exists() and existing_ids else 'w'
        with args.output.open(mode, encoding='utf-8') as f:
            if mode == 'a' and output_data:
                f.write('\n' + output_data)
            elif output_data:
                f.write(output_data)
    else:
        print(output_data)

if __name__ == '__main__':
    raise SystemExit(main())
