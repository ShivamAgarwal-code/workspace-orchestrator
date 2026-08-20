# Entity-Relationship Diagram

```mermaid
erDiagram
    USERS ||--o{ CONVERSATIONS : has
    USERS ||--o{ GMAIL_CACHE : has
    USERS ||--o{ GCAL_CACHE : has
    USERS ||--o{ GDRIVE_CACHE : has
    USERS ||--o{ SYNC_STATUS : has
    USERS ||--o{ AUDIT_LOG : has
    CONVERSATIONS ||--o{ AUDIT_LOG : produces

    USERS {
        uuid id PK
        varchar email UK
        text google_access_token
        text google_refresh_token
        timestamptz google_token_expiry
        varchar timezone
        timestamptz created_at
    }

    CONVERSATIONS {
        uuid id PK
        uuid user_id FK
        text query
        jsonb intent
        text response
        jsonb actions_taken
        jsonb entities_referenced "pronoun/reference resolution context"
        timestamptz created_at
    }

    GMAIL_CACHE {
        uuid id PK
        uuid user_id FK
        varchar email_id "gmail message id"
        varchar thread_id
        text subject
        text body_preview
        varchar sender
        text[] recipients
        text[] labels
        timestamptz received_at
        varchar content_hash "change detection for re-embedding"
        vector_1536 embedding
        timestamptz updated_at
    }

    GCAL_CACHE {
        uuid id PK
        uuid user_id FK
        varchar event_id
        varchar calendar_id
        text title
        text description
        text location
        varchar organizer
        text[] attendees
        varchar status
        timestamptz start_time
        timestamptz end_time
        varchar content_hash
        vector_1536 embedding
        timestamptz updated_at
    }

    GDRIVE_CACHE {
        uuid id PK
        uuid user_id FK
        varchar file_id
        text name
        varchar mime_type
        text content_preview
        text[] owners
        text web_view_link
        varchar parent_folder_id
        timestamptz modified_at
        varchar content_hash
        vector_1536 embedding
        timestamptz updated_at
    }

    SYNC_STATUS {
        uuid id PK
        uuid user_id FK
        enum service "gmail|gcal|gdrive"
        enum state "idle|running|error"
        timestamptz last_synced_at
        text last_error
        int items_synced
        text sync_cursor "pagination/history token for incremental sync"
    }

    AUDIT_LOG {
        uuid id PK
        uuid user_id FK
        uuid conversation_id FK
        varchar service
        varchar operation
        jsonb payload
        varchar result "success|error"
        text error_detail
        timestamptz created_at
    }
```

## Notes

- `gmail_cache` / `gcal_cache` / `gdrive_cache` are kept as separate tables rather than one
  polymorphic `documents` table: each has distinct filterable metadata (sender vs attendees vs
  mime_type) that benefit from dedicated btree/GIN indexes, and independent per-service sync jobs
  avoid write contention on a shared table.
- Every cache table carries a `content_hash` so the sync job can skip re-embedding unchanged items
  (embedding calls are the most expensive part of a sync pass).
- Every `*_cache` table has an `ivfflat` index on `embedding` with `vector_cosine_ops`, plus a
  composite `(user_id, <time column>)` btree index — queries always filter by `user_id` first
  (tenant isolation), so this index lets Postgres narrow the candidate set before the vector scan.
- `audit_log` is append-only and records every write operation (send/create/delete) executed by an
  agent, satisfying the security/compliance requirement for multi-tenant write auditing.
- `sync_cursor` stores Gmail `historyId` / Calendar `syncToken` / Drive `startPageToken` for
  incremental sync instead of re-scanning full mailboxes every 15 minutes.
