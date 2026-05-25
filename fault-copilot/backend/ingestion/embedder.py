"""Chunk content, generate embeddings, and persist to ChromaDB."""

from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import chromadb  # noqa: E402
from anthropic import Anthropic  # noqa: E402

from config import ANTHROPIC_API_KEY, CHROMA_PATH, EMBEDDING_MODEL  # noqa: E402
from embeddings import get_embedding_function  # noqa: E402
from ingestion.image_describer import describe_image  # noqa: E402
from ingestion.pdf_parser import parse_manual  # noqa: E402


_TOKENS_PER_WORD  = 1.33
_SENTENCE_OVERLAP = 2       # sentences carried forward as context overlap

# ── Sentence splitter ─────────────────────────────────────────────────────────
# Protects abbreviations and decimal numbers from being treated as sentence ends.
_PROTECT_ABBREV  = re.compile(
    r'\b(?:Fig|FIG|Sec|No|Vol|vs|etc|approx|min|max|ref|Ref|'
    r'St|Dr|Mr|Mrs|Ms|Prof|Dept|Figs)\.',
    re.IGNORECASE,
)
_PROTECT_DECIMAL = re.compile(r'(\d)\.(\d)')        # 4.2  or  5.2.5
_PROTECT_LIST    = re.compile(r'(\(\s*\w+\s*\))\.')  # (1).  (a).
_SENT_SPLIT      = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
_PLACEHOLDER     = '\x00'


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence boundaries using lightweight regex."""
    p = _PROTECT_ABBREV.sub(lambda m: m.group(0).replace('.', _PLACEHOLDER), text)
    p = _PROTECT_DECIMAL.sub(lambda m: m.group(0).replace('.', _PLACEHOLDER), p)
    p = _PROTECT_LIST.sub(lambda m: m.group(0).replace('.', _PLACEHOLDER), p)
    parts = _SENT_SPLIT.split(p)
    return [s.replace(_PLACEHOLDER, '.').strip() for s in parts if s.strip()]


def _chunk_text(
    text: str,
    max_tokens: int = 800,
    overlap_tokens: int = 100,
) -> list[str]:
    """Sentence-aware chunker: never splits mid-sentence.

    Overlap is sentence-level — the last _SENTENCE_OVERLAP sentences of the
    current chunk become the first sentences of the next chunk.  Single
    sentences that exceed the token budget are split word-by-word as a
    fallback (rare in practice for service manuals).
    """
    sentences = _split_sentences(text)
    if not sentences:
        return []

    max_words = max(1, int(max_tokens / _TOKENS_PER_WORD))
    chunks: list[str] = []
    cur_sents: list[str] = []
    cur_wc = 0

    for sentence in sentences:
        sw = sentence.split()
        swc = len(sw)

        # Edge case: sentence alone exceeds budget → word-boundary fallback
        if swc > max_words:
            if cur_sents:
                chunks.append(" ".join(cur_sents))
                cur_sents, cur_wc = [], 0
            for i in range(0, swc, max_words):
                chunks.append(" ".join(sw[i : i + max_words]))
            continue

        if cur_wc + swc > max_words and cur_sents:
            chunks.append(" ".join(cur_sents))
            overlap = cur_sents[-_SENTENCE_OVERLAP:]
            cur_sents = overlap + [sentence]
            cur_wc = sum(len(s.split()) for s in cur_sents)
        else:
            cur_sents.append(sentence)
            cur_wc += swc

    if cur_sents:
        chunks.append(" ".join(cur_sents))

    return [c for c in chunks if c.strip()]


def ingest_manual(
    pdf_path: str,
    manual_id: str,
    client: Anthropic | None,
    use_vision: bool = True,
    max_pages: int | None = None,
) -> int:
    """Parse, optionally describe images, chunk, and store a manual into ChromaDB.

    Parameters
    ----------
    use_vision: Call Claude Vision on embedded images (default True).
                Pass False for fast text-only ingestion at zero API cost.
    max_pages:  Process at most this many pages. None = no limit.

    Returns the number of chunks written.
    """
    pdf_path_obj = Path(pdf_path).resolve()
    if not pdf_path_obj.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages = parse_manual(str(pdf_path_obj))
    total_pdf_pages = len(pages)
    if total_pdf_pages == 0:
        print(f"[warn] no pages extracted from {pdf_path}", file=sys.stderr)
        return 0

    if max_pages is not None and total_pdf_pages > max_pages:
        pages = pages[:max_pages]

    total_pages = len(pages)
    mode_tag = "text+vision" if use_vision else "text-only"
    print(
        f"  Mode      : {mode_tag}  |  "
        f"Pages     : {total_pages}"
        + (f" / {total_pdf_pages} in PDF" if total_pdf_pages != total_pages else "")
    )

    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    ef = get_embedding_function(EMBEDDING_MODEL)
    coll_kwargs: dict = {"name": f"manual_{manual_id}"}
    if ef is not None:
        coll_kwargs["embedding_function"] = ef
    collection = chroma_client.get_or_create_collection(**coll_kwargs)

    # 1E: carry the last seen heading forward so multi-page sections retain their
    # section metadata on every page, not just the first page of that section.
    last_section: str = ""

    total_chunks = 0
    for i, page in enumerate(pages, start=1):
        print(f"Processing page {i} of {total_pages}...")
        page_num = page["page_num"]
        detected = page.get("section_heading") or ""
        if detected:
            last_section = detected
        section = detected or last_section
        page_text = page.get("text") or ""

        if use_vision and client is not None:
            for img_idx, b64 in enumerate(page.get("images", [])):
                try:
                    description = describe_image(b64, section, client)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"  [warn] image {img_idx} on page {page_num} description "
                        f"failed: {exc}",
                        file=sys.stderr,
                    )
                    continue
                if description:
                    page_text += f"\n\n[Image description: {description}]"

        if not page_text.strip():
            continue

        chunks = _chunk_text(page_text)
        if not chunks:
            continue

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        for chunk_idx, chunk in enumerate(chunks):
            ids.append(f"{manual_id}_p{page_num}_c{chunk_idx}")
            documents.append(chunk)
            metadatas.append(
                {
                    "manual_id": manual_id,
                    "page_num": page_num,
                    "section_heading": section,
                    "chunk_index": chunk_idx,
                    "source_pdf": str(pdf_path_obj),
                }
            )

        try:
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            total_chunks += len(chunks)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [warn] failed to store chunks for page {page_num}: {exc}",
                file=sys.stderr,
            )
            continue

    return total_chunks


if __name__ == "__main__":
    # python embedder.py <pdf_path> <manual_id>
    # Example: python embedder.py ../manuals/TBA19_OM.pdf tba19_om
    if len(sys.argv) != 3:
        print(
            "usage: python embedder.py <pdf_path> <manual_id>",
            file=sys.stderr,
        )
        sys.exit(2)

    cli_pdf_path = sys.argv[1]
    cli_manual_id = sys.argv[2]

    if not ANTHROPIC_API_KEY:
        print(
            "[error] ANTHROPIC_API_KEY is not set (check your .env file)",
            file=sys.stderr,
        )
        sys.exit(1)

    cli_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        stored = ingest_manual(cli_pdf_path, cli_manual_id, cli_client)
        print(f"Stored {stored} chunks in collection 'manual_{cli_manual_id}'")
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] ingestion failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
