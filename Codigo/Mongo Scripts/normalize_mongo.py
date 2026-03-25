"""
Pequeno Normalizador: le documentos de uma colecao MongoDB e normaliza campos
textuais com lematizacao spaCy, escolhendo o idioma com spacy_langdetect

Uso:
    python normalize_mongo.py [--uri mongodb://localhost:27017]
                              [--source-db Colaboradores]
                              [--source-collection Servidores_Bruto]
                              [--dest-db Colaboradores]
                              [--dest-collection Servidores_Normalizados]
                              [--batch-size 200] [--dry-run]

O script processa documentos em lotes e faz upsert por _id na colecao de destino
"""

import argparse
from typing import Any, Iterable, Dict
import pymongo
import spacy
from spacy.language import Language
from spacy_langdetect import LanguageDetector


# Campos que devem ser normalizados
FIELDS_TO_NORMALIZE = [
    "areasAtuacao.grandeArea",
    "areasAtuacao.area",
    "areasAtuacao.subarea",
    "areasAtuacao.especialidade",
    "linhasPesquisa",
    "listaPB.titulo",
    "listaPT.titulo",
    "listaPA.titulo",
    "listaPP.titulo",
    "listaPP.descricao",
    "listaOA.titulo_projeto",
    "listaOA.area",
    "listaOC.titulo_projeto",
    "listaOC.area",
]


def get_by_path(data: Any, path: str) -> Any:
    current = data
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def set_by_path(data: dict, path: str, value: Any) -> None:
    current = data
    parts = path.split(".")
    for key in parts[:-1]:
        if key not in current:
            return  # Pular caminhos nao existentes
        current = current[key]
    
    # Se estamos em um dict settar o valor
    if isinstance(current, dict):
        current[parts[-1]] = value


def create_language_detector() -> spacy.Language:
    if "language_detector" not in Language.factories:
        Language.factory(
            "language_detector",
            func=lambda nlp, name: LanguageDetector(),
        )

    detector_nlp = spacy.blank("xx")
    detector_nlp.add_pipe("sentencizer")
    detector_nlp.add_pipe("language_detector")
    return detector_nlp


def detect_language(detector_nlp: spacy.Language, text: str) -> str:
    if not text or not text.strip():
        return "pt"

    doc = detector_nlp(text)
    detected = doc._.language.get("language", "pt")

    if detected.startswith("en"):
        return "en"
    if detected.startswith("pt"):
        return "pt"
    return "pt"


def normalize_text(
    nlp_models: Dict[str, spacy.Language],
    detector_nlp: spacy.Language,
    text: str,
) -> str:
    """
    Normaliza o texto baseado na lingua detectada 
    """
    lang = detect_language(detector_nlp, text)
    nlp = nlp_models.get(lang, nlp_models['pt'])
    doc = nlp(text)
    lemmas = [token.lemma_ for token in doc if not token.is_space]
    return " ".join(lemmas).lower().strip()


def normalize_value(
    nlp_models: Dict[str, spacy.Language],
    detector_nlp: spacy.Language,
    value: Any,
) -> Any:
    if isinstance(value, str):
        return normalize_text(nlp_models, detector_nlp, value)
    if isinstance(value, list):
        return [normalize_value(nlp_models, detector_nlp, item) for item in value]
    if isinstance(value, dict):
        return {
            key: normalize_value(nlp_models, detector_nlp, item)
            for key, item in value.items()
        }
    return value


def normalize_document(
    nlp_models: Dict[str, spacy.Language],
    detector_nlp: spacy.Language,
    doc: dict,
) -> dict:
    new_doc = dict(doc)
    for field_path in FIELDS_TO_NORMALIZE:
        parts = field_path.split(".")
        if len(parts) < 2:
            continue
        
        # Pega a lista/container
        list_key = parts[0]
        field_key = ".".join(parts[1:])
        
        if list_key not in new_doc:
            continue
        
        container = new_doc[list_key]
        
        # Se eh uma lista normaliza cada item
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict) and field_key in item:
                    original = item[field_key]
                    if original is not None:
                        item[field_key] = normalize_value(nlp_models, detector_nlp, original)
        # se eh um dict normaliza o campo dentro
        elif isinstance(container, dict):
            original = get_by_path(container, field_key)
            if original is not None:
                normalized = normalize_value(nlp_models, detector_nlp, original)
                set_by_path(container, field_key, normalized)
    
    return new_doc


def iter_documents(collection: pymongo.collection.Collection, batch_size: int) -> Iterable[dict]:
    cursor = collection.find({}).batch_size(batch_size)
    try:
        for doc in cursor:
            yield doc
    finally:
        cursor.close()


def insert_buffer(
    collection: pymongo.collection.Collection,
    buffer: list[dict],
    *,
    fallback_one_by_one: bool,
) -> None:
    try:
        # Substitui docs que existem e insere novos
        ops = [pymongo.ReplaceOne({"_id": doc.get("_id")}, doc, upsert=True) for doc in buffer]
        collection.bulk_write(ops, ordered=False)
        return
    except pymongo.errors.BulkWriteError as exc:
        print(f"Bulk insert failed: {exc.details}")
    except Exception as exc:  # pragma: sem cobertura - erro insperado de insertion
        print(f"Insert failed: {exc}")

    if not fallback_one_by_one:
        raise

    print("Retrying failed batch one-by-one to locate bad documents...")
    for doc in buffer:
        try:
            collection.replace_one({"_id": doc.get("_id")}, doc, upsert=True)
        except Exception as exc:  # pragma: sem cobertura - depende do dado
            doc_id = doc.get("_id")
            print(f"Failed document _id={doc_id}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize MongoDB documents using spaCy language models."
    )
    parser.add_argument(
        "--uri",
        default="mongodb://localhost:27017",
        help="MongoDB connection URI (default: mongodb://localhost:27017).",
    )
    parser.add_argument(
        "--source-db",
        default="Colaboradores",
        help="Source MongoDB database name (default: Colaboradores).",
    )
    parser.add_argument(
        "--source-collection",
        default="Servidores_Bruto",
        help="Source MongoDB collection name (default: Servidores_Bruto).",
    )
    parser.add_argument(
        "--dest-db",
        default="Colaboradores",
        help="Destination MongoDB database name (default: Colaboradores).",
    )
    parser.add_argument(
        "--dest-collection",
        default="Servidores_Normalizados",
        help="Destination MongoDB collection name (default: Servidores_Normalizados).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Batch size for processing documents (default: 200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview normalization without updating database.",
    )
    args = parser.parse_args()

    print(f"Connecting to MongoDB at {args.uri}...")
    client = pymongo.MongoClient(args.uri)
    source = client[args.source_db][args.source_collection]
    dest = client[args.dest_db][args.dest_collection]

    # Pega a quantidade total
    total_docs = source.count_documents({})
    print(f"Total documents to process: {total_docs}")

    print("Loading spaCy models...")
    nlp_models = {
        'pt': spacy.load("pt_core_news_lg"),
        'en': spacy.load("en_core_web_lg")
    }
    detector_nlp = create_language_detector()
    print("Models loaded: Portuguese and English")

    mode = "DRY RUN" if args.dry_run else "PROCESS"
    print(f"Starting {mode}...\n")

    buffer = []
    processed = 0
    for doc in iter_documents(source, args.batch_size):
        normalized = normalize_document(nlp_models, detector_nlp, doc)
        buffer.append(normalized)
        if len(buffer) >= args.batch_size:
            if not args.dry_run:
                insert_buffer(dest, buffer, fallback_one_by_one=True)
            processed += len(buffer)
            print(f"Processed {processed}/{total_docs} documents ({100*processed/total_docs:.1f}%)")
            buffer.clear()

    if buffer:
        if not args.dry_run:
            insert_buffer(dest, buffer, fallback_one_by_one=True)
        processed += len(buffer)
        print(f"Processed {processed}/{total_docs} documents ({100*processed/total_docs:.1f}%)")
    
    print(f"\n{'='*50}")
    if args.dry_run:
        print("DRY RUN: No changes were made to the database")
    else:
        print("Normalization complete!")
    print(f"{'='*50}")
    client.close()


if __name__ == "__main__":
    main()
