"""Hybrid (vector + full-text keyword) search directly against pgvector, per cache table.

Design notes (see also DESIGN.md "Embedding & Search Layer"):

- Metadata filters (user_id always; optionally sender/attendee/mime_type/date range) are pushed
  into the WHERE clause of BOTH the vector and keyword candidate-generation CTEs, narrowing the
  candidate set *before* the vector distance scan runs — this is what keeps queries under the
  500ms target as a mailbox grows, rather than scanning + re-ranking the full table. Because both
  CTEs are already scoped to the filtered, user-owned rows, the outer query needs no additional
  WHERE clause — it only needs to fetch full rows for whatever ids the CTEs produced.
- Vector candidates and keyword (Postgres full-text, `ts_rank`) candidates are fused with
  Reciprocal Rank Fusion (RRF, k=10 — a corpus this size, thousands of rows per user rather than
  millions, benefits from a smaller k than the textbook k=60 so rank differences aren't flattened
  away). RRF is used instead of a raw weighted sum because vector cosine distance and ts_rank
  live on completely different, uncalibrated scales; fusing on *rank* rather than raw score
  avoids one signal silently dominating the other.
- A small recency boost (exponential decay, 30-day half-life) is added at query time rather than
  baked into the embedding, so relevance ranking naturally favors newer items without requiring
  re-embedding on every sync pass.
"""
from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

RRF_K = 10
RECENCY_WEIGHT = 0.03
RECENCY_HALFLIFE_DAYS = 30
DEFAULT_VECTOR_CANDIDATES = 50
DEFAULT_KEYWORD_CANDIDATES = 50
_DECAY_RATE = 0.6931 / RECENCY_HALFLIFE_DAYS  # ln(2) / halflife


def _fused_score_sql(time_col: str | None) -> str:
    recency = (
        f" + ({RECENCY_WEIGHT} * exp(-GREATEST(EXTRACT(EPOCH FROM (now() - t.{time_col})), 0) "
        f"/ 86400.0 * {_DECAY_RATE:.6f}))"
        if time_col
        else ""
    )
    return f"(COALESCE(1.0/({RRF_K}+v.rnk), 0) + COALESCE(1.0/({RRF_K}+k.rnk), 0){recency})"


def _hybrid_query(table: str, id_col: str, select_cols: str, ts_expr: str, filter_sql: str, time_col: str | None) -> str:
    """`filter_sql` is a trusted, statically-built (no user input concatenated) WHERE fragment
    of bound-parameter conditions, applied identically inside both candidate CTEs. `select_cols`
    is an explicit column list (never `t.*`) so the ~6KB `embedding` vector is never pulled back
    over the wire for every search hit — it stays server-side, used only for the `<=>` distance.
    """
    fused = _fused_score_sql(time_col)
    return f"""
    WITH vector_matches AS (
        SELECT {id_col} AS _id, ROW_NUMBER() OVER (ORDER BY embedding <=> :query_embedding) AS rnk
        FROM {table}
        WHERE user_id = :user_id AND embedding IS NOT NULL {filter_sql}
        ORDER BY embedding <=> :query_embedding
        LIMIT :vector_candidates
    ),
    keyword_matches AS (
        SELECT {id_col} AS _id, ROW_NUMBER() OVER (
            ORDER BY ts_rank({ts_expr}, plainto_tsquery('english', :query_text)) DESC
        ) AS rnk
        FROM {table}
        WHERE user_id = :user_id
          AND :query_text != ''
          AND {ts_expr} @@ plainto_tsquery('english', :query_text)
          {filter_sql}
        LIMIT :keyword_candidates
    ),
    matched AS (
        SELECT COALESCE(v._id, k._id) AS _id FROM vector_matches v FULL OUTER JOIN keyword_matches k USING (_id)
    )
    SELECT {select_cols}, {fused} AS fused_score, v.rnk AS vector_rank, k.rnk AS keyword_rank
    FROM {table} t
    JOIN matched ON matched._id = t.{id_col}
    LEFT JOIN vector_matches v ON v._id = t.{id_col}
    LEFT JOIN keyword_matches k ON k._id = t.{id_col}
    ORDER BY fused_score DESC
    LIMIT :limit
    """


async def _run(
    session: AsyncSession,
    sql: str,
    user_id: UUID,
    query_text: str,
    query_embedding: list[float],
    limit: int,
    extra_params: dict,
) -> list[dict]:
    settings = get_settings()
    stmt = text(sql).bindparams(bindparam("query_embedding", type_=Vector(settings.embedding_dim)))
    params = {
        "user_id": user_id,
        "query_text": (query_text or "").strip(),
        "query_embedding": query_embedding,
        "vector_candidates": DEFAULT_VECTOR_CANDIDATES,
        "keyword_candidates": DEFAULT_KEYWORD_CANDIDATES,
        "limit": limit,
        **extra_params,
    }
    result = await session.execute(stmt, params)
    return [dict(row._mapping) for row in result]


async def search_gmail(
    session: AsyncSession,
    user_id: UUID,
    query_text: str,
    query_embedding: list[float],
    sender: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
) -> list[dict]:
    ts_expr = "to_tsvector('english', coalesce(subject,'') || ' ' || coalesce(body_preview,''))"
    filter_sql = (
        "AND (:sender::text IS NULL OR sender ILIKE :sender) "
        "AND (:since::timestamptz IS NULL OR received_at >= :since) "
        "AND (:until::timestamptz IS NULL OR received_at <= :until)"
    )
    select_cols = (
        "t.id, t.user_id, t.email_id, t.thread_id, t.subject, t.body_preview, t.sender, "
        "t.recipients, t.labels, t.received_at, t.updated_at"
    )
    sql = _hybrid_query("gmail_cache", "id", select_cols, ts_expr, filter_sql, "received_at")
    return await _run(
        session, sql, user_id, query_text, query_embedding, limit,
        {"sender": f"%{sender}%" if sender else None, "since": since, "until": until},
    )


async def search_gcal(
    session: AsyncSession,
    user_id: UUID,
    query_text: str,
    query_embedding: list[float],
    attendee: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
) -> list[dict]:
    ts_expr = (
        "to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'') "
        "|| ' ' || coalesce(location,''))"
    )
    filter_sql = (
        "AND (:attendee::text IS NULL OR :attendee = ANY(attendees)) "
        "AND (:since::timestamptz IS NULL OR start_time >= :since) "
        "AND (:until::timestamptz IS NULL OR start_time <= :until)"
    )
    select_cols = (
        "t.id, t.user_id, t.event_id, t.calendar_id, t.title, t.description, t.location, "
        "t.organizer, t.attendees, t.status, t.start_time, t.end_time, t.updated_at"
    )
    sql = _hybrid_query("gcal_cache", "id", select_cols, ts_expr, filter_sql, "start_time")
    return await _run(
        session, sql, user_id, query_text, query_embedding, limit,
        {"attendee": attendee, "since": since, "until": until},
    )


async def search_gdrive(
    session: AsyncSession,
    user_id: UUID,
    query_text: str,
    query_embedding: list[float],
    mime_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 10,
) -> list[dict]:
    ts_expr = "to_tsvector('english', coalesce(name,'') || ' ' || coalesce(content_preview,''))"
    filter_sql = (
        "AND (:mime_type::text IS NULL OR mime_type = :mime_type) "
        "AND (:since::timestamptz IS NULL OR modified_at >= :since) "
        "AND (:until::timestamptz IS NULL OR modified_at <= :until)"
    )
    select_cols = (
        "t.id, t.user_id, t.file_id, t.name, t.mime_type, t.content_preview, t.owners, "
        "t.web_view_link, t.parent_folder_id, t.modified_at, t.updated_at, t.extra_metadata"
    )
    sql = _hybrid_query("gdrive_cache", "id", select_cols, ts_expr, filter_sql, "modified_at")
    return await _run(
        session, sql, user_id, query_text, query_embedding, limit,
        {"mime_type": mime_type, "since": since, "until": until},
    )
