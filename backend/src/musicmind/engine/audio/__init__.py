"""Audio analysis pipeline — feature extraction + embedding computation.

Tier 2 (essentia): BPM, danceability, arousal/valence, key/scale, loudness,
and 128-dim Discogs-EffNet embeddings from 30s preview clips.
Gracefully degrades to metadata-only scoring when essentia is unavailable.
"""
