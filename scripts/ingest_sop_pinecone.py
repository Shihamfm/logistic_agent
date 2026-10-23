import os
import json
from pathlib import Path
import pypdf
import pandas as pd
import hashlib

from langchain_openai import OpenAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from dotenv import load_dotenv

# Suppress the Windows symlink warning noise completely from HuggingFace Hub
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"


#__file__ is scripts\ingest_sop_pinecone.py
script_dir = Path(__file__).resolve().parent # points to scripts folder
project_root = script_dir.parents[0] # climbs up 1 levels to project root folder

load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

if not PINECONE_API_KEY:
    raise ValueError("PINECONE_API_KEY is not set in the environment variables.")

# Set up clean production data cache path
cash_dir = project_root / "data" / "cache"
cash_dir.mkdir(parents=True, exist_ok=True)  # Create the cache directory if it doesn't exist
HASH_CACHE_FILE = cash_dir / "hash_cache.json"

hash_cashe = {}
if HASH_CACHE_FILE.exists():
    try:
        with open(HASH_CACHE_FILE, "r") as f:
            hash_cashe = json.load(f)
    except Exception as e:
        print(f"Error loading hash cache: {e}")
        hash_cashe = {}
        
# Dynamic Environment Routing
EMBEDDINGS_MODEL_SETTING = os.getenv("Embeddings_model", "LOCAL").strip().upper()

if EMBEDDINGS_MODEL_SETTING == "OPENAI":
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    INDEX_NAME = "fde-sop-openai-embeddings"
    TARGET_DIMENSION = 1536
else:
    local_model_target = os.getenv("Local_Embedding_Model", "sentence-transformers/all-MiniLM-L6-v2").strip()
    embeddings = HuggingFaceEmbeddings(
        model_name=local_model_target,
        model_kwargs={"device": "cpu"}  # Use CPU for inference
        )
    INDEX_NAME = "fde-sop-local-embeddings"
    TARGET_DIMENSION = 384


# PINECONE PROVISIONING
print(f"Connecting to Pinecone Index: {INDEX_NAME}")
pc = Pinecone(api_key=PINECONE_API_KEY)

exisisting_indexes = pc.list_indexes()

# Self healing verification in case an index was created with a legacy dimension
if INDEX_NAME in exisisting_indexes:
    index_info = pc.describe_index(INDEX_NAME)
    current_dimension = index_info['dimension']
    if current_dimension != TARGET_DIMENSION:
        print(f"Existing index '{INDEX_NAME}' has dimension {current_dimension}, expected {TARGET_DIMENSION}. Deleting and recreating.")
        pc.delete_index(INDEX_NAME)
        exisisting_indexes = [name for name in exisisting_indexes if name != INDEX_NAME]

if INDEX_NAME not in exisisting_indexes:
    print(f"Creating Pinecone Index: {INDEX_NAME} with dimension {TARGET_DIMENSION}")
    pc.create_index(
        name = INDEX_NAME, 
        dimension=TARGET_DIMENSION,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )

index_client = pc.Index(INDEX_NAME)
vector_store = PineconeVectorStore(index=index_client, embedding=embeddings)

# Robust polymorphic document parser
def parse_and_chunk_document(doc_path: Path) -> list[Document]:
    ext = doc_path.suffix.lower()
    raw_chunks: list[Document] = []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=600,
        chunk_overlap=60)

    if ext == ".md":
        headers_to_split_on = [("#", "Header_1"), ("##", "Header_2"), ("###", "Header_3")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        raw_text = doc_path.read_text(encoding="utf-8")
        header_docs = md_splitter.split_text(raw_text)
        raw_chunks = text_splitter.split_documents(header_docs)

    elif ext == ".txt":
        raw_text = doc_path.read_text(encoding="utf-8")
        raw_docs = [Document(page_content=raw_text)]
        raw_chunks = text_splitter.split_text(raw_docs)

    elif ext == ".pdf":
        pdf_docs = []
        try:
            with open(doc_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                for page_num, page in enumerate(reader.pages):
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        pdf_docs.append(Document(page_content=page_text, metadata={"page_number": page_num + 1}))
            raw_chunks = text_splitter.split_documents(pdf_docs)

        except Exception as e:
            print(f"Error reading PDF {doc_path}: {e}")
            return []
    elif ext in [".csv", ".xlsx"]:
        try:
            df = pd.read_csv(doc_path) if ext == ".csv" else pd.read_excel(doc_path)
        except Exception as e:
            print(f"Error reading {ext} file {doc_path}: {e}")
            return []

        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            row_items = [
                f"{str(col)}: {str(val)}" 
                for col, val in row_dict.items()
                if pd.notna(val) and str(val).strip() != ""
            ]

            if row_items:
                row_text = " | ".join(row_items)
                doc_item = Document(page_content=row_text, metadata={"row_index": idx})
                raw_chunks.append(doc_item)

    # sanity check
    valid_chunks = []
    for chunk in raw_chunks:
        clean_text = chunk.page_content.strip()
        if clean_text:
            chunk.page_content = clean_text
            valid_chunks.append(chunk)

    return valid_chunks

# Incremental pipeline with batching
policy_dir = project_root / "data" / "policy"
target_pattern = ["*.md", "*.pdf", "*.txt", "*.csv", "*.xlsx"]
current_files = {}
for pattern in target_pattern:
    for file_path in policy_dir.glob(pattern):
        current_files[file_path.name] = file_path

print(f"Found {len(current_files)} policy files in the directory: {policy_dir}")

updated_cache = {}
cache_modified = False

cached_filenames = set(hash_cashe.keys())
current_filenames = set(current_files.keys())   
deleted_files = cached_filenames - current_filenames

for deleted_file in deleted_files:
    print(f"File deleted: {deleted_file}. Removing from Pinecone index and cache.")
    try:
        index_client.delete(filter={"source_file": {"$eq": deleted_file}})
        cache_modified = True
    except Exception as e:
        print(f"Error deleting vectors for {deleted_file} from Pinecone: {e}")

for filename, file_path in current_files.items():
    file_bytes = file_path.read_bytes()
    file_hash = hashlib.md5(file_bytes).hexdigest()
    updated_cache[filename] = file_hash

    if hash_cashe.get(filename) == file_hash:
        print(f"Skipped (Unchanged): {filename}")
        continue

    print(f"Processing file: {filename}")
    cache_modified = True

    try:
        try:
            index_client.delete(filter={"source_file": {"$eq": filename}})
        except Exception as e:
            pass

        chunks = parse_and_chunk_document(file_path)
        if not chunks:
            print(f"No valid chunks found for {filename}. Skipping ingestion.")
            continue

        explicit_ids = []
        for idx, chunk in enumerate(chunks):
            chunk.metadata["source_file"] = filename
            chunk.metadata["file_format"] = file_path.suffix.replace(".", "").upper()
            chunk.metadata["document_type"] = "Compliance Asset"
            explicit_ids.append(f"{filename}_chunk_{idx}")

        batch_size = 100
        total_chunks = len(chunks)
        print(f"Ingesting {total_chunks} chunks for {filename} into Pinecone in batches of {batch_size}.")

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_ids = explicit_ids[i:i + batch_size]
            vector_store.add_documents(batch_chunks, ids=batch_ids)
            print(f"Batch {i // batch_size + 1} ingested for {filename}.")

    except Exception as e:
        print(f"Error during ingestion of {filename}: {e}")
        updated_cache.pop(filename, None)

 # Sync hash cache
if cache_modified:
    with open(HASH_CACHE_FILE, "w") as f:
        json.dump(updated_cache, f, indent=4)
    print(f"Hash cache updated at {HASH_CACHE_FILE}.")
else:
    print("Index is already up to date.")



