"""
Pequeno Atualizador: le arquivos JSON e atualiza documentos no MongoDB apenas
quando houver diferencas em relacao ao documento ja armazenado

Uso:
	python update_documents.py [--folder JSONs]
							   [--uri mongodb://localhost:27017]
							   [--db Colaboradores]
							   [--collection Servidores_Bruto]
							   [--id-field idLattes] [--dry-run]

O script compara cada JSON com o documento existente e faz replace/insert quando
necessario, exibindo um resumo no final
"""

import argparse
import json
from pathlib import Path
from pymongo import MongoClient, errors
from deepdiff import DeepDiff


def load_json(path: Path) -> dict:
	with path.open("r", encoding="utf-8") as handle:
		return json.load(handle)


def connect_mongodb(
	uri: str = "mongodb://localhost:27017",
	db_name: str = "Colaboradores",
	collection_name: str = "Servidores_Bruto",
) -> tuple:
	"""
 	Conecta com MongoDB
 	Retorna client e conexao
  	"""
	try:
		client = MongoClient(uri)
		client.admin.command("ping")  # Testa conexao
		db = client[db_name]
		collection = db[collection_name]
		return client, collection
	except errors.ServerSelectionTimeoutError:
		raise SystemExit(f"Cannot connect to MongoDB at {uri}")


def compare_documents(doc1: dict, doc2: dict) -> bool:
	"""
 	Checa se dois documentos sao diferentes
  	"""
	diff = DeepDiff(doc1, doc2, ignore_order=False)
	return bool(diff)


def update_documents(
	json_files: list[Path],
	collection,
	id_field: str = "idLattes",
	dry_run: bool = False,
) -> dict:
	"""
 	Atualiza os documentos com o JSON se ele difere do doc no MongoDB.
  	"""
	summary = {
		"files_processed": 0,
		"documents_checked": 0,
		"documents_updated": 0,
		"documents_identical": 0,
		"documents_not_found": 0,
		"errors": 0,
	}

	for path in json_files:
		try:
			summary["files_processed"] += 1
			json_data = load_json(path)

			id_value = json_data.get(id_field)
			if not id_value:
				print(f"{path.name}: Missing '{id_field}' field")
				summary["errors"] += 1
				continue

			# Encontra doc existente
			existing_doc = collection.find_one({id_field: id_value})
			summary["documents_checked"] += 1

			if not existing_doc:
				summary["documents_not_found"] += 1
				if not dry_run:
					# Insere novo doc
					collection.insert_one(json_data)
					summary["documents_updated"] += 1
					print(f"✓ {path.name}: Inserted new document")
			else:
				# Compara docs (excluindo o campo _id do mongo)
				existing_copy = {k: v for k, v in existing_doc.items() if k != "_id"}
				if compare_documents(existing_copy, json_data):
					summary["documents_updated"] += 1
					if not dry_run:
						collection.replace_one(
							{id_field: id_value},
							json_data
						)
					print(f"✓ {path.name}: Updated (differences found)")
				else:
					summary["documents_identical"] += 1
					print(f"- {path.name}: Identical (no changes needed)")

		except json.JSONDecodeError as e:
			print(f"✗ {path.name}: Invalid JSON - {e}")
			summary["errors"] += 1
		except Exception as e:
			print(f"✗ {path.name}: Error - {e}")
			summary["errors"] += 1

	return summary


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Update MongoDB documents with JSON data if they differ."
	)
	parser.add_argument(
		"--folder",
		default="JSONs",
		help="Path to folder containing JSON files (default: JSONs).",
	)
	parser.add_argument(
		"--uri",
		default="mongodb://localhost:27017",
		help="MongoDB connection URI (default: mongodb://localhost:27017).",
	)
	parser.add_argument(
		"--db",
		default="Colaboradores",
		help="MongoDB database name (default: Colaboradores).",
	)
	parser.add_argument(
		"--collection",
		default="Servidores_Bruto",
		help="MongoDB collection name (default: Servidores_Bruto).",
	)
	parser.add_argument(
		"--id-field",
		default="idLattes",
		help="Field used to identify documents (default: idLattes).",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Preview changes without updating database.",
	)
	args = parser.parse_args()

	folder = Path(args.folder).expanduser().resolve()
	json_files = sorted(folder.glob("*.json"))
	if not json_files:
		raise SystemExit(f"No JSON files found in {folder}")

	print(f"Found {len(json_files)} JSON files")
	print(f"Connecting to MongoDB at {args.uri}...")

	client, collection = connect_mongodb(args.uri, args.db, args.collection)

	try:
		mode = "DRY RUN" if args.dry_run else "UPDATE"
		print(f"Starting {mode}...\n")

		summary = update_documents(
			json_files,
			collection,
			args.id_field,
			args.dry_run,
		)

		print(f"\n{'='*50}")
		print("Summary:")
		print(f"  Files processed: {summary['files_processed']}")
		print(f"  Documents checked: {summary['documents_checked']}")
		print(f"  Documents updated: {summary['documents_updated']}")
		print(f"  Documents identical: {summary['documents_identical']}")
		print(f"  Documents not found: {summary['documents_not_found']}")
		print(f"  Errors: {summary['errors']}")
		if args.dry_run:
			print("\nDRY RUN: No changes were made to the database")
		print(f"{'='*50}")

	finally:
		client.close()


if __name__ == "__main__":
	main()
