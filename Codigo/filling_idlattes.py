"""
Preenchedor de IDs: preenche id_lattes ausentes em colaboradores de producoes
com base na lista de nomes de citacao presente nos JSONs.

Uso:
	python filling_idlattes.py --folder <pasta-jsons> [--log filled_idlattes_log.csv]

O script le todos os JSONs da pasta informada, tenta preencher id_lattes
quando existe correspondencia unica, e registra cada preenchimento em CSV.
"""

import argparse
import csv
import json
from pathlib import Path


def load_json(path: Path) -> dict:
	with path.open("r", encoding="utf-8") as handle:
		return json.load(handle)


def write_json(path: Path, data: dict) -> None:
	with path.open("w", encoding="utf-8") as handle:
		json.dump(data, handle, ensure_ascii=False, indent=2)
		handle.write("\n")


def build_name_index(json_files: list[Path]) -> dict[str, set[str]]:
	name_to_ids: dict[str, set[str]] = {}
	for path in json_files:
		data = load_json(path)
		id_lattes = (data.get("idLattes") or "").strip()
		# Mapeia o nome completo para o id
		full_name = (data.get("nomeCompleto") or "").strip()
		if full_name and id_lattes:
			name_to_ids.setdefault(full_name, set()).add(id_lattes)
		
		for raw_name in data.get("listaNomesCitacao", []):
			# Mapeia cada nome de citação para todos os IDs que o utilizam (pode ser ambiguo)
			key = (raw_name or "").strip()
			if not key or not id_lattes:
				continue
			name_to_ids.setdefault(key, set()).add(id_lattes)
	return name_to_ids


def fill_missing_ids(
	json_files: list[Path],
	name_to_ids: dict[str, set[str]],
	log_path: Path,
) -> dict:
	summary = {
		"files_processed": 0,
		"collaborators_missing": 0,
		"filled": 0,
		"ambiguous": 0,
		"no_match": 0,
		"files_updated": 0,
	}
	with log_path.open("w", encoding="utf-8", newline="") as handle:
		writer = csv.writer(handle)
		writer.writerow(
			[
				"json_file",
				"publication_index",
				"publication_title",
				"collaborator_name",
				"filled_id_lattes",
			]
		)

		for path in json_files:
			data = load_json(path)
			changed = False
			summary["files_processed"] += 1

			# Processar colaboradores em listaPB e listaPT
			for section_name in ["listaPB", "listaPT", "listaPA"]:
				for item_index, item in enumerate(data.get(section_name, []), start=1):
					for collaborator in item.get("colaboradores", []):
						current_id = (collaborator.get("id_lattes") or "").strip()
						if current_id:
							continue

						summary["collaborators_missing"] += 1
						key = (collaborator.get("nome", "") or "").strip()
						if not key:
							summary["no_match"] += 1
							continue

						ids = name_to_ids.get(key, set())
						if len(ids) == 1:
							# Somente preenche quando o nome mapeia para exatamente um ID
							filled_id = next(iter(ids))
							collaborator["id_lattes"] = filled_id
							summary["filled"] += 1
							changed = True
							writer.writerow(
								[
									path.name,
									f"{section_name}[{item_index}]",
									item.get("titulo", ""),
									key,
									filled_id,
								]
							)
						elif len(ids) > 1:
							summary["ambiguous"] += 1
						else:
							summary["no_match"] += 1

			# Processar membros em listaPP
			for item_index, item in enumerate(data.get("listaPP", []), start=1):
				for member in item.get("membros", []):
					current_id = (member.get("id_lattes") or "").strip()
					if current_id:
						continue

					summary["collaborators_missing"] += 1
					key = (member.get("nome", "") or "").strip()
					if not key:
						summary["no_match"] += 1
						continue

					ids = name_to_ids.get(key, set())
					if len(ids) == 1:
						filled_id = next(iter(ids))
						member["id_lattes"] = filled_id
						summary["filled"] += 1
						changed = True
						writer.writerow(
							[
								path.name,
								f"listaPP[{item_index}]",
								item.get("titulo", ""),
								key,
								filled_id,
							]
						)
					elif len(ids) > 1:
						summary["ambiguous"] += 1
					else:
						summary["no_match"] += 1

			# Processar orientandos e orientadores em listaOA e listaOC
			for section_name in ["listaOA", "listaOC"]:
				for item_index, item in enumerate(data.get(section_name, []), start=1):
					# Processar orientando
					orientando = item.get("orientando", {})
					current_id = (orientando.get("id_lattes") or "").strip()
					if not current_id:
						summary["collaborators_missing"] += 1
						key = (orientando.get("nome", "") or "").strip()
						if key:
							ids = name_to_ids.get(key, set())
							if len(ids) == 1:
								filled_id = next(iter(ids))
								orientando["id_lattes"] = filled_id
								summary["filled"] += 1
								changed = True
								writer.writerow(
									[
										path.name,
										f"{section_name}[{item_index}]-orientando",
										item.get("titulo_projeto", ""),
										key,
										filled_id,
									]
								)
							elif len(ids) > 1:
								summary["ambiguous"] += 1
							else:
								summary["no_match"] += 1
						else:
							summary["no_match"] += 1

					# Processar orientadores
					for orientador in item.get("orientadores", []):
						current_id = (orientador.get("id_lattes") or "").strip()
						if current_id:
							continue

						summary["collaborators_missing"] += 1
						key = (orientador.get("nome", "") or "").strip()
						if not key:
							summary["no_match"] += 1
							continue

						ids = name_to_ids.get(key, set())
						if len(ids) == 1:
							filled_id = next(iter(ids))
							orientador["id_lattes"] = filled_id
							summary["filled"] += 1
							changed = True
							writer.writerow(
								[
									path.name,
									f"{section_name}[{item_index}]-orientador",
									item.get("titulo_projeto", ""),
									key,
									filled_id,
								]
							)
						elif len(ids) > 1:
							summary["ambiguous"] += 1
						else:
							summary["no_match"] += 1

			if changed:
				write_json(path, data)
				summary["files_updated"] += 1

	return summary


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Fill missing collaborator id_lattes using listaNomesCitacao across JSON files."
	)
	parser.add_argument(
		"--folder",
		default="JSONs",
		help="Path to the folder containing JSON files (default: JSONs).",
	)
	parser.add_argument(
		"--log",
		default="filled_idlattes_log.csv",
		help="CSV path for logging filled IDs (default: filled_idlattes_log.csv).",
	)
	args = parser.parse_args()

	folder = Path(args.folder).expanduser().resolve()
	json_files = sorted(folder.glob("*.json"))
	if not json_files:
		raise SystemExit(f"No JSON files found in {folder}")

	name_to_ids = build_name_index(json_files)
	log_path = Path(args.log).expanduser().resolve()
	summary = fill_missing_ids(json_files, name_to_ids, log_path)

	print("Done.")
	print(
		"Processed: {files_processed} | Updated: {files_updated} | "
		"Missing: {collaborators_missing} | Filled: {filled} | "
		"Ambiguous: {ambiguous} | No match: {no_match}".format(**summary)
	)
	print(f"Log: {log_path}")


if __name__ == "__main__":
	main()
