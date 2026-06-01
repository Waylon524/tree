"""RAGClient: Qdrant (embedded) + local embedding service.

★ INTERFACE UNCHANGED — migrate from previous engine (step 3).
See docs/LEGACY-DESIGN.md §5. Single collection `tree-knowledge`, COSINE,
dim 2560. Three content_kind namespaces: source / finished / draft.

Public surface to preserve:
  - RAGClient(store_path=None, embed_url=None, embed_model=None, ...)
  - index_file(file_seq, filename, text, *, content_kind, source_collection,
               path, doc_id, ...) -> int
  - query(query_text, top_k, filters, include_drafts, neighbor_window) -> list[dict]
  - scroll_chunks / delete_file / document_indexed / make_doc_id / close

New-architecture note: each MTU is indexed as one document
(doc_id = mtu_id, payload carries node_id/title/keywords/collection/line_range).
"""

from __future__ import annotations


class RAGClient:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("RAGClient — migrate in step 3")
