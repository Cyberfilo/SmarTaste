"""Schema validation tests for MusicMind Web database."""

from __future__ import annotations

from musicmind.db.schema import metadata

ALL_TABLE_NAMES = [
    "users",
    "user_api_keys",
    "service_connections",
    "refresh_tokens",
    "listening_history",
    "song_metadata_cache",
    "artist_cache",
    "taste_profile_snapshots",
    "recommendation_feedback",
    "audio_features_cache",
    "sound_classification_cache",
    "play_count_proxy",
    "chat_conversations",
    "generated_playlists",
]

DATA_TABLE_NAMES = [
    "user_api_keys",
    "service_connections",
    "listening_history",
    "song_metadata_cache",
    "artist_cache",
    "taste_profile_snapshots",
    "recommendation_feedback",
    "audio_features_cache",
    "sound_classification_cache",
    "play_count_proxy",
    "chat_conversations",
    "generated_playlists",
]


def test_all_tables_present() -> None:
    """All 14 tables are defined in the schema metadata."""
    table_names = set(metadata.tables.keys())
    for name in ALL_TABLE_NAMES:
        assert name in table_names, f"Table '{name}' missing from schema"
    assert len(metadata.tables) == 14


def test_user_id_on_all_data_tables() -> None:
    """Every data table (not users itself) has a user_id column with FK to users.id."""
    for table_name in DATA_TABLE_NAMES:
        table = metadata.tables[table_name]
        assert "user_id" in table.columns, (
            f"Table '{table_name}' missing user_id column"
        )
        user_id_col = table.columns["user_id"]
        fk_targets = [fk.target_fullname for fk in user_id_col.foreign_keys]
        assert "users.id" in fk_targets, (
            f"Table '{table_name}' user_id does not reference users.id"
        )


def test_service_connections_unique_constraint() -> None:
    """service_connections has unique constraint on (user_id, service)."""
    table = metadata.tables["service_connections"]
    constraint_names = [c.name for c in table.constraints if c.name]
    assert "uq_user_service" in constraint_names, (
        "Missing uq_user_service unique constraint on service_connections"
    )


def test_listening_history_has_service_source() -> None:
    """listening_history has a service_source column."""
    table = metadata.tables["listening_history"]
    assert "service_source" in table.columns, (
        "listening_history missing service_source column"
    )


def test_song_metadata_composite_pk() -> None:
    """song_metadata_cache primary key is (catalog_id, user_id)."""
    table = metadata.tables["song_metadata_cache"]
    pk_col_names = [col.name for col in table.primary_key.columns]
    assert "catalog_id" in pk_col_names, "catalog_id not in primary key"
    assert "user_id" in pk_col_names, "user_id not in primary key"


def test_play_count_proxy_composite_pk() -> None:
    """play_count_proxy primary key is (song_id, user_id)."""
    table = metadata.tables["play_count_proxy"]
    pk_col_names = [col.name for col in table.primary_key.columns]
    assert "song_id" in pk_col_names, "song_id not in primary key"
    assert "user_id" in pk_col_names, "user_id not in primary key"


def test_service_source_on_multi_service_tables() -> None:
    """Tables that store per-service data have service_source column."""
    tables_with_service_source = [
        "listening_history",
        "song_metadata_cache",
        "artist_cache",
        "generated_playlists",
    ]
    for table_name in tables_with_service_source:
        table = metadata.tables[table_name]
        assert "service_source" in table.columns, (
            f"Table '{table_name}' missing service_source column"
        )


def test_timestamps_are_timezone_aware() -> None:
    """All DateTime columns use timezone=True."""
    for table_name, table in metadata.tables.items():
        for col in table.columns:
            if isinstance(col.type, type) and col.type.__class__.__name__ == "DateTime":
                continue
            if hasattr(col.type, "timezone") and isinstance(col.type.timezone, bool):
                assert col.type.timezone, (
                    f"{table_name}.{col.name} DateTime is not timezone-aware"
                )
