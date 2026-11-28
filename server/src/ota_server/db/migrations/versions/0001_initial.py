"""Initial schema: conversation, turn, settings, mcp_server, tool, and two seed rows.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = (
    """
    CREATE TABLE conversation (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        started_at timestamptz NOT NULL DEFAULT now(),
        ended_at timestamptz,
        CONSTRAINT pk_conversation PRIMARY KEY (id),
        CONSTRAINT ck_conversation_ended_after_started
            CHECK (ended_at IS NULL OR ended_at >= started_at)
    )
    """,
    """
    CREATE INDEX ix_conversation_last_activity
        ON conversation ((coalesce(ended_at, started_at)))
    """,
    """
    CREATE TABLE turn (
        id bigint GENERATED ALWAYS AS IDENTITY,
        conversation_id uuid NOT NULL,
        role text NOT NULL,
        content text,
        tool_calls jsonb,
        truncated boolean NOT NULL DEFAULT false,
        provider_model text,
        latency_ms integer,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_turn PRIMARY KEY (id),
        CONSTRAINT fk_turn_conversation_id_conversation
            FOREIGN KEY (conversation_id) REFERENCES conversation (id) ON DELETE CASCADE,
        CONSTRAINT ck_turn_role CHECK (role IN ('user', 'assistant', 'tool')),
        CONSTRAINT ck_turn_has_content CHECK (content IS NOT NULL OR tool_calls IS NOT NULL),
        CONSTRAINT ck_turn_latency_ms CHECK (latency_ms >= 0)
    )
    """,
    "CREATE INDEX ix_turn_conversation_id_id ON turn (conversation_id, id)",
    """
    CREATE TABLE settings (
        id boolean NOT NULL DEFAULT true,
        language text NOT NULL DEFAULT 'en',
        voice text NOT NULL DEFAULT '',
        system_prompt text NOT NULL DEFAULT '',
        volume integer NOT NULL DEFAULT 50,
        retention_days integer NOT NULL DEFAULT 90,
        memory_retention_days integer NOT NULL DEFAULT 365,
        speech_cap_s integer NOT NULL DEFAULT 60,
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_settings PRIMARY KEY (id),
        CONSTRAINT ck_settings_single_row CHECK (id),
        CONSTRAINT ck_settings_language CHECK (language IN ('en', 'pl', 'uk')),
        CONSTRAINT ck_settings_volume CHECK (volume BETWEEN 0 AND 100),
        CONSTRAINT ck_settings_retention_days CHECK (retention_days BETWEEN 1 AND 3650),
        CONSTRAINT ck_settings_memory_retention_days
            CHECK (memory_retention_days BETWEEN 1 AND 3650),
        CONSTRAINT ck_settings_speech_cap_s CHECK (speech_cap_s BETWEEN 5 AND 600)
    )
    """,
    """
    CREATE TABLE mcp_server (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        name text NOT NULL,
        transport text NOT NULL,
        url text,
        secret_headers jsonb NOT NULL DEFAULT '{}'::jsonb,
        enabled boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_mcp_server PRIMARY KEY (id),
        CONSTRAINT uq_mcp_server_name UNIQUE (name),
        CONSTRAINT ck_mcp_server_transport
            CHECK (transport IN ('device', 'stdio', 'sse', 'streamable_http')),
        CONSTRAINT ck_mcp_server_device_has_no_url CHECK ((transport = 'device') = (url IS NULL)),
        CONSTRAINT ck_mcp_server_secret_headers_object
            CHECK (jsonb_typeof(secret_headers) = 'object')
    )
    """,
    "CREATE UNIQUE INDEX uq_mcp_server_one_device ON mcp_server (transport) "
    "WHERE transport = 'device'",
    """
    CREATE TABLE tool (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        mcp_server_id uuid NOT NULL,
        name text NOT NULL,
        schema_hash text NOT NULL,
        enabled boolean NOT NULL DEFAULT false,
        confirm boolean NOT NULL DEFAULT true,
        approved_at timestamptz,
        seen_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_tool PRIMARY KEY (id),
        CONSTRAINT fk_tool_mcp_server_id_mcp_server
            FOREIGN KEY (mcp_server_id) REFERENCES mcp_server (id) ON DELETE CASCADE,
        CONSTRAINT uq_tool_mcp_server_id_name UNIQUE (mcp_server_id, name)
    )
    """,
    "INSERT INTO settings DEFAULT VALUES ON CONFLICT DO NOTHING",
    "INSERT INTO mcp_server (name, transport, url) VALUES ('device', 'device', NULL) "
    "ON CONFLICT DO NOTHING",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in ("tool", "mcp_server", "settings", "turn", "conversation"):
        op.execute(f"DROP TABLE {table}")
