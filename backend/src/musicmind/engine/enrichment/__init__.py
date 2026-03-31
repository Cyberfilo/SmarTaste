"""Audio feature enrichment pipeline — Deezer preview → ReccoBeats analysis.

Pipeline per track:
1. Deezer: search by name → 30s preview MP3 + BPM (free, no auth)
2. ReccoBeats: upload preview → 9 audio features (free, no auth)
3. SoundStat: Spotify ID lookup for gaps (paid, optional)

Runs automatically when user connects a music service.
All features stored per-user in audio_features_cache with provenance.
"""
