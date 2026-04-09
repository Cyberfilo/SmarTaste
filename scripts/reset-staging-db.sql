-- Reset staging database to clean state
-- Run this on the staging Postgres instance to wipe all user data
-- while keeping the schema (Alembic migrations) intact.

-- Order matters: delete from child tables first (FK constraints)
DELETE FROM audio_embeddings;
DELETE FROM sound_classification_cache;
DELETE FROM audio_features_cache;
DELETE FROM audio_features_global;
DELETE FROM play_count_proxy;
DELETE FROM listening_history;
DELETE FROM recommendation_feedback;
DELETE FROM taste_profile_snapshots;
DELETE FROM user_calibration;
DELETE FROM user_indexing_status;
DELETE FROM worker_status;
DELETE FROM artist_cobweb;
DELETE FROM global_song_cache;
DELETE FROM song_metadata_cache;
DELETE FROM artist_cache;
DELETE FROM lastfm_similar_tracks;
DELETE FROM lastfm_tags_cache;
DELETE FROM kg_relationships;
DELETE FROM kg_artists;
DELETE FROM acousticbrainz_cache;
DELETE FROM isrc_spotify_mapping;
DELETE FROM bandit_arms;
DELETE FROM playlist_items;
DELETE FROM generated_playlists;
DELETE FROM chat_messages;
DELETE FROM chat_conversations;
DELETE FROM refresh_tokens;
DELETE FROM service_connections;
DELETE FROM user_api_keys;
DELETE FROM users;

-- Reset worker status
INSERT INTO worker_status (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Confirm
SELECT 'Staging DB reset complete' AS status,
       (SELECT count(*) FROM users) AS users,
       (SELECT count(*) FROM song_metadata_cache) AS songs;
