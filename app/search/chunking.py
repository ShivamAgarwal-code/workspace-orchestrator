"""Text preparation for embedding: what to embed, and how to chunk long content.

Strategy (documented here since it directly affects embedding quality):

- Each cached item (email, event, file) is embedded as a single vector of `title + body`,
  not split into a separate chunks table. At the per-user corpus sizes this system targets
  (thousands, not millions, of items per mailbox), one vector per item keeps the schema simple
  and query-time joins cheap, while `body` is prepared below to keep the embedded signal dense.
- Email threads quote prior messages beneath the newest reply ("On ... wrote:"). We keep a
  head-heavy slice of the body (most of the budget from the start, a smaller tail slice from the
  end) so the embedding captures both the newest reply and the original thread context, instead
  of truncating naively and losing everything after `max_chars`.
- Temporal decay is intentionally NOT baked into the embedding vector itself (that would make
  the same email's vector drift over time and require re-embedding on every sync). Instead it is
  applied at query time as a recency boost in the ranking formula — see `search.vector_store`.
"""

HEAD_RATIO = 0.8


def build_embedding_text(title: str | None, body: str | None, max_chars: int = 3000) -> str:
    title = (title or "").strip()
    body = (body or "").strip()
    if len(body) <= max_chars:
        return f"{title}\n\n{body}".strip()

    head_len = int(max_chars * HEAD_RATIO)
    tail_len = max_chars - head_len
    chunked_body = f"{body[:head_len]}\n...\n{body[-tail_len:]}"
    return f"{title}\n\n{chunked_body}".strip()
