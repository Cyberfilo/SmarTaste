#!/usr/bin/env python3
"""Import AcousticBrainz high-level descriptors into PostgreSQL.

Streams the deduplicated tarball without fully extracting.
Extracts only the fields we need and batch-inserts into acousticbrainz_cache.

Usage:
    # Set your Railway Postgres public URL
    export DATABASE_URL="postgresql://postgres:PASSWORD@host:port/railway"

    # Run the importer
    python scripts/import_acousticbrainz.py ~/acousticbrainz/acousticbrainz-highlevel-json-20220623-deduplicated.tar.zst

    # Or with a plain tar.gz
    python scripts/import_acousticbrainz.py ~/acousticbrainz/dump.tar.gz

Prerequisites:
    pip install psycopg2-binary zstandard
    # Download the dump first (see README)
"""

from __future__ import annotations

import json
import os
import sys
import tarfile
import time
from io import BytesIO

# Try zstandard for .tar.zst files
try:
    import zstandard as zstd

    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

import psycopg2
from psycopg2.extras import execute_values

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_SIZE = 5000


def extract_highlevel(data: dict) -> dict | None:
    """Extract the fields we need from a high-level JSON document."""
    hl = data.get("highlevel", {})
    if not hl:
        return None

    def _prob(key: str, label: str = "") -> float | None:
        """Get probability for a specific class."""
        entry = hl.get(key, {})
        if not entry:
            return None
        if label:
            classes = entry.get("all", {})
            return classes.get(label, entry.get("probability"))
        return entry.get("probability")

    def _value(key: str) -> str | None:
        entry = hl.get(key, {})
        return entry.get("value")

    # Genre probabilities: get top predictions
    genre_probs = {}
    for genre_key in ("genre_dortmund", "genre_electronic", "genre_rosamerica", "genre_tzanetakis"):
        entry = hl.get(genre_key, {})
        all_probs = entry.get("all", {})
        for genre_name, prob in all_probs.items():
            if prob > 0.1:  # Only keep meaningful probabilities
                genre_probs[genre_name] = max(genre_probs.get(genre_name, 0), round(prob, 3))

    # Sort and keep top 5
    genre_probs = dict(sorted(genre_probs.items(), key=lambda x: x[1], reverse=True)[:5])

    # Low-level summary
    ll = data.get("lowlevel", {})
    rhythm = data.get("rhythm", {})
    tonal = data.get("tonal", {})

    return {
        "mood_aggressive": _prob("mood_aggressive", "aggressive"),
        "mood_happy": _prob("mood_happy", "happy"),
        "mood_relaxed": _prob("mood_relaxed", "relaxed"),
        "mood_sad": _prob("mood_sad", "sad"),
        "mood_party": _prob("mood_party", "party"),
        "mood_electronic": _prob("mood_electronic", "electronic"),
        "mood_acoustic": _prob("mood_acoustic", "acoustic"),
        "danceability": _prob("danceability", "danceable"),
        "gender": _value("gender"),
        "voice_instrumental": _value("voice_instrumental"),
        "tonal_atonal": _value("tonal_atonal"),
        "genre_probabilities": genre_probs,
        "average_loudness": ll.get("average_loudness"),
        "bpm": rhythm.get("bpm"),
        "key": tonal.get("chords_key"),
        "scale": tonal.get("chords_scale"),
    }


def open_tar(path: str):
    """Open a tar file, handling .tar.zst and .tar.gz."""
    if path.endswith(".tar.zst") or path.endswith(".zst"):
        if not HAS_ZSTD:
            print("ERROR: Install zstandard: pip install zstandard")
            sys.exit(1)
        dctx = zstd.ZstdDecompressor()
        f = open(path, "rb")
        reader = dctx.stream_reader(f)
        return tarfile.open(fileobj=reader, mode="r|"), f
    elif path.endswith(".tar.gz") or path.endswith(".tgz"):
        return tarfile.open(path, "r:gz"), None
    elif path.endswith(".tar"):
        return tarfile.open(path, "r"), None
    else:
        print(f"ERROR: Unsupported format: {path}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_acousticbrainz.py <path-to-tar>")
        sys.exit(1)

    tar_path = sys.argv[1]
    if not os.path.exists(tar_path):
        print(f"ERROR: File not found: {tar_path}")
        sys.exit(1)

    if not DATABASE_URL:
        print("ERROR: Set DATABASE_URL environment variable")
        sys.exit(1)

    print(f"Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Ensure table exists
    cur.execute("SELECT COUNT(*) FROM acousticbrainz_cache")
    existing = cur.fetchone()[0]
    print(f"Existing rows: {existing}")

    print(f"Opening {tar_path}...")
    tar, raw_file = open_tar(tar_path)

    batch = []
    total = 0
    errors = 0
    start = time.time()

    try:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue

            # Extract MBID from filename (e.g., "ab/cd/abcdef-1234-....json")
            basename = os.path.basename(member.name)
            mbid = basename.replace(".json", "")
            if len(mbid) != 36:  # Valid UUID length
                continue

            try:
                f = tar.extractfile(member)
                if f is None:
                    continue
                data = json.loads(f.read())
                f.close()
            except (json.JSONDecodeError, OSError):
                errors += 1
                continue

            extracted = extract_highlevel(data)
            if extracted is None:
                continue

            batch.append((
                mbid,
                extracted["mood_aggressive"],
                extracted["mood_happy"],
                extracted["mood_relaxed"],
                extracted["mood_sad"],
                extracted["mood_party"],
                extracted["mood_electronic"],
                extracted["mood_acoustic"],
                extracted["danceability"],
                extracted["gender"],
                extracted["voice_instrumental"],
                extracted["tonal_atonal"],
                json.dumps(extracted["genre_probabilities"]),
                extracted["average_loudness"],
                extracted["bpm"],
                extracted["key"],
                extracted["scale"],
            ))

            if len(batch) >= BATCH_SIZE:
                _insert_batch(cur, batch)
                conn.commit()
                total += len(batch)
                elapsed = time.time() - start
                rate = total / elapsed if elapsed > 0 else 0
                print(
                    f"\r  {total:,} imported ({rate:.0f}/s, {errors} errors)",
                    end="", flush=True,
                )
                batch = []

        # Final batch
        if batch:
            _insert_batch(cur, batch)
            conn.commit()
            total += len(batch)

    finally:
        tar.close()
        if raw_file:
            raw_file.close()

    elapsed = time.time() - start
    print(f"\n\nDone! {total:,} recordings imported in {elapsed:.0f}s ({errors} errors)")

    cur.close()
    conn.close()


def _insert_batch(cur, batch):
    """Batch insert/upsert into acousticbrainz_cache."""
    execute_values(
        cur,
        """
        INSERT INTO acousticbrainz_cache
            (mbid, mood_aggressive, mood_happy, mood_relaxed, mood_sad,
             mood_party, mood_electronic, mood_acoustic, danceability,
             gender, voice_instrumental, tonal_atonal, genre_probabilities,
             average_loudness, bpm, key, scale)
        VALUES %s
        ON CONFLICT (mbid) DO NOTHING
        """,
        batch,
        page_size=1000,
    )


if __name__ == "__main__":
    main()
