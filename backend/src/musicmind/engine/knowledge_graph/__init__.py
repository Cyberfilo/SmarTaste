"""Music knowledge graph — artist relationships and genre embeddings.

Ingests data from MusicBrainz API, builds a graph of artist relationships
(collaborated_with, member_of, influenced_by, similar_to), and computes
Node2Vec embeddings for graph-based similarity scoring.
"""
