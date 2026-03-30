"""Audio feature enrichment pipeline — progressive fill from free external APIs.

Enrichment order:
1. Deezer (ISRC-native, free, BPM)
2. ReccoBeats (Spotify ID, free, energy/danceability/valence/acousticness)
3. SoundStat (Spotify ID, paid, complete features — budget-gated)

ISRC → Spotify ID resolution via MusicBrainz when needed.
"""
