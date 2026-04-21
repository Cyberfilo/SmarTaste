# SmarTaste Audio Intelligence Tier 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the audio intelligence stack — Essentia classifier heads on CPU, Modal GPU function for CLAP+MERT+captions, multi-signal scoring, LLM explanations, natural language search.

**Architecture:** Railway CPU handles Essentia DSP + EffNet embeddings (already done). Modal serverless A100 handles CLAP (512-dim) + MERT (768-dim) + captioning. OpenAI GPT-5.4 generates explanations from structured tags. Results stored in PostgreSQL, served by existing FastAPI backend.

**Tech Stack:** Essentia, ONNX Runtime, Modal (Python SDK), LAION CLAP, MERT (transformers), OpenAI API, FastAPI, SQLAlchemy, PostgreSQL.

**Branch:** `staging` — all commits here. Every task ends with commit + push.

---

## File Structure

### New files
| File | Responsibility |
|------|---------------|
| `backend/src/musicmind/engine/audio/classifiers.py` | Essentia classifier heads (mood, genre, acousticness) on CPU |
| `gpu-worker/handler.py` | Modal serverless function: CLAP + MERT + captioning |
| `gpu-worker/requirements.txt` | GPU worker dependencies |
| `backend/src/musicmind/engine/enrichment/gpu_client.py` | HTTP client to call Modal from Railway worker |
| `backend/src/musicmind/api/search/router.py` | Natural language search API endpoint |
| `backend/src/musicmind/api/search/service.py` | CLAP-based semantic search logic |
| `backend/src/musicmind/engine/explanations.py` | OpenAI GPT-5.4 explanation generation |
| `backend/alembic/versions/020_multi_signal_enrichment.py` | DB migration for new columns |

### Modified files
| File | Changes |
|------|---------|
| `backend/src/musicmind/engine/audio/essentia_extractor.py` | Call classifier heads after embedding |
| `backend/src/musicmind/engine/audio/models.py` | Add classifier result fields |
| `backend/src/musicmind/db/schema.py` | Add CLAP/MERT embedding columns, caption, tags JSON |
| `backend/src/musicmind/engine/similarity.py` | Multi-signal similarity function |
| `backend/src/musicmind/engine/scorer.py` | Use multi-signal similarity, add explanation |
| `backend/src/musicmind/engine/enrichment/orchestrator.py` | Call GPU worker for Tier 2 enrichment |
| `backend/src/musicmind/worker.py` | Queue tracks for GPU enrichment after Tier 1 |
| `backend/src/musicmind/config.py` | Add openai_api_key, modal settings |
| `backend/src/musicmind/api/router.py` | Register search router |
| `backend/src/musicmind/api/recommendations/service.py` | Load CLAP/MERT embeddings, pass to scorer |
| `backend/pyproject.toml` | Add openai dependency |
| `VERSION`, `CHANGELOG.md`, `README.md` | Docs + version |

---

## Task 1: Essentia Classifier Heads (CPU, Railway)

**Files:**
- Create: `backend/src/musicmind/engine/audio/classifiers.py`
- Modify: `backend/src/musicmind/engine/audio/essentia_extractor.py`
- Modify: `backend/src/musicmind/engine/audio/models.py`

- [ ] **Step 1: Create classifiers.py**

Run Essentia's pre-trained classifier heads on top of the EffNet embedding.
These are tiny FC layers (~1-5MB each) that read the 1,280-dim vector.

```python
# classifiers.py — run classifier heads on EffNet embedding
# Models: mood (aggressive/happy/party/relaxed/sad), danceability, 
#         voice_instrumental, mood_acoustic, genre_discogs400
# Each classifier shares the same embedding — negligible extra compute.

def classify_from_embedding(embedding, model_dir="/models"):
    """Run all available classifier heads on a pre-computed EffNet embedding."""
    results = {}
    # For each .pb model file found in model_dir:
    #   TensorflowPredict2D(graphFilename=path, output="model/Softmax")(embedding)
    #   Parse output into named dict
    return results
```

- [ ] **Step 2: Update ExtractedFeatures model**

Add fields: `mood_aggressive`, `mood_happy`, `mood_relaxed`, `mood_sad`, `mood_party`, `genre_tags` (dict), `vocal_instrumental` (float).

- [ ] **Step 3: Wire classifiers into essentia_extractor.py**

After embedding extraction, call `classify_from_embedding(embedding)` and merge results into ExtractedFeatures.

- [ ] **Step 4: Commit + push**
```bash
git add -A && git commit -m "feat(audio): Essentia classifier heads — mood, genre, acousticness on CPU"
git push origin staging
```

---

## Task 2: DB Migration for Multi-Signal Enrichment

**Files:**
- Create: `backend/alembic/versions/020_multi_signal_enrichment.py`
- Modify: `backend/src/musicmind/db/schema.py`

- [ ] **Step 1: Create migration 020**

Add to `song_metadata_cache` or a new `track_enrichment` table:
- `clap_embedding` (JSON, 512-dim vector)
- `mert_embedding` (JSON, 768-dim vector)
- `ai_caption` (Text, Music Flamingo / LLM-generated description)
- `ai_tags` (JSON, structured tags from classifiers — genre probs, mood scores, instruments)
- `structure_sections` (JSON, verse/chorus/bridge labels)

Add to `audio_embeddings_global`:
- `clap_embedding` (JSON)
- `mert_embedding` (JSON)

- [ ] **Step 2: Update schema.py to match**

- [ ] **Step 3: Commit + push**

---

## Task 3: Modal GPU Worker

**Files:**
- Create: `gpu-worker/handler.py`
- Create: `gpu-worker/requirements.txt`
- Create: `gpu-worker/README.md`

- [ ] **Step 1: Create Modal handler**

```python
import modal

app = modal.App("smartaste-gpu-worker")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("laion-clap", "transformers", "torch", "essentia", "onnxruntime", "numpy")
)

@app.function(image=image, gpu="A100", timeout=300, keep_warm=0)
def enrich_track(preview_url: str) -> dict:
    """Full Tier 2 enrichment: CLAP + MERT embeddings."""
    # 1. Download preview
    # 2. CLAP embedding (512-dim)
    # 3. MERT embedding (768-dim) 
    # 4. Return JSON blob
    ...

@app.function(image=image, gpu="A100", timeout=600, keep_warm=0)
def enrich_batch(preview_urls: list[str]) -> list[dict]:
    """Batch enrichment for efficiency."""
    return [enrich_track.local(url) for url in preview_urls]
```

- [ ] **Step 2: Create requirements.txt**
- [ ] **Step 3: Create README with setup instructions**

```markdown
## Setup
1. pip install modal
2. modal token new
3. modal deploy handler.py
4. Set MODAL_ENDPOINT_URL in Railway backend env vars
```

- [ ] **Step 4: Commit + push**

---

## Task 4: GPU Client (Railway → Modal)

**Files:**
- Create: `backend/src/musicmind/engine/enrichment/gpu_client.py`
- Modify: `backend/src/musicmind/config.py`

- [ ] **Step 1: Create gpu_client.py**

```python
async def enrich_via_gpu(preview_url: str, modal_token: str) -> dict | None:
    """Call Modal serverless function for Tier 2 enrichment."""
    # HTTP POST to Modal endpoint
    # Returns: {clap_512: [...], mert_768: [...], ai_tags: {...}}
    ...

async def enrich_batch_via_gpu(urls: list[str], modal_token: str) -> list[dict]:
    """Batch GPU enrichment."""
    ...
```

- [ ] **Step 2: Add config settings**

```python
# config.py additions
modal_token_id: str | None = None
modal_token_secret: str | None = None
openai_api_key: str | None = None  # For explanation generation
```

- [ ] **Step 3: Wire into orchestrator — after Essentia Tier 1, queue Tier 2**

In `_enrich_single_track()` after Essentia succeeds:
```python
# Queue for GPU enrichment (async, non-blocking)
if settings.modal_token_id and preview_url:
    await _queue_gpu_enrichment(engine, catalog_id, preview_url)
```

- [ ] **Step 4: Commit + push**

---

## Task 5: OpenAI Explanation Generation

**Files:**
- Create: `backend/src/musicmind/engine/explanations.py`
- Modify: `backend/pyproject.toml` (add `openai>=1.0`)

- [ ] **Step 1: Create explanations.py**

```python
async def generate_track_caption(tags: dict, api_key: str) -> str:
    """Generate a natural language track description from structured tags.
    
    Input: {genre_probs, mood_scores, bpm, key, energy, danceability, ...}
    Output: "Dark Italian drill track at 142 BPM in C minor..."
    """
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    
    prompt = f"""Describe this song in 2-3 sentences based on these audio features:
    BPM: {tags.get('bpm')}, Key: {tags.get('key')} {tags.get('scale')}
    Energy: {tags.get('energy')}, Danceability: {tags.get('danceability')}
    Moods: {tags.get('moods', {})}
    Genres: {tags.get('genres', {})}
    Be specific about production style, atmosphere, and musical characteristics."""
    
    resp = await client.chat.completions.create(
        model="gpt-5.4",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200,
        temperature=0.7,
    )
    return resp.choices[0].message.content

async def generate_recommendation_explanation(
    seed_tags: dict, candidate_tags: dict,
    seed_name: str, candidate_name: str,
    api_key: str,
) -> str:
    """Generate "why you'll like this" explanation."""
    ...
```

- [ ] **Step 2: Wire into enrichment pipeline**

After Essentia extraction, call `generate_track_caption()` with the extracted tags. Store in `ai_caption` column.

- [ ] **Step 3: Wire into recommendation response**

In `service.py get_recommendations()`, generate explanations for top results using `generate_recommendation_explanation()`.

- [ ] **Step 4: Add openai to pyproject.toml dependencies**
- [ ] **Step 5: Commit + push**

---

## Task 6: Multi-Signal Similarity Scoring

**Files:**
- Modify: `backend/src/musicmind/engine/similarity.py`
- Modify: `backend/src/musicmind/engine/scorer.py`

- [ ] **Step 1: Add multi_signal_similarity() to similarity.py**

```python
def multi_signal_similarity(
    a: dict[str, Any], b: dict[str, Any],
) -> float:
    """Multi-signal similarity using all available embeddings.
    
    Weights:
      0.30 × CLAP cosine (holistic vibe + text search)
      0.25 × EffNet cosine (genre/style)
      0.20 × MERT cosine (musical structure)
      0.10 × mood vector similarity
      0.10 × scalar similarity (BPM, key, energy)
      0.05 × caption embedding similarity
    
    Falls back to simpler scoring when signals unavailable.
    """
```

- [ ] **Step 2: Update score_candidate() to accept CLAP + MERT embeddings**

Add `candidate_clap`, `user_clap_centroid`, `candidate_mert`, `user_mert_centroid` params. Use `multi_signal_similarity()` when available.

- [ ] **Step 3: Commit + push**

---

## Task 7: Natural Language Search API

**Files:**
- Create: `backend/src/musicmind/api/search/router.py`
- Create: `backend/src/musicmind/api/search/service.py`
- Modify: `backend/src/musicmind/api/router.py` (register search router)

- [ ] **Step 1: Create search service**

```python
class SearchService:
    async def semantic_search(
        self, engine, *, user_id: str, query: str, limit: int = 10,
    ) -> list[dict]:
        """Search user's catalog using CLAP text-to-audio similarity.
        
        1. Encode query text via CLAP text encoder (Modal function)
        2. Load all CLAP embeddings for user's songs
        3. Cosine similarity → rank → return top N
        """
```

- [ ] **Step 2: Create search router**

```python
@router.get("/api/search")
async def search(
    request: Request, q: str = Query(...), limit: int = Query(default=10),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Natural language music search. 'find me something darker'"""
```

- [ ] **Step 3: Add Modal function for text encoding**

```python
@app.function(image=image, gpu="A100", timeout=30)
def encode_text_clap(text: str) -> list[float]:
    """Encode a text query to CLAP 512-dim embedding."""
```

- [ ] **Step 4: Register router + commit + push**

---

## Task 8: Tag-to-Caption Generation Pipeline

**Files:**
- Modify: `backend/src/musicmind/engine/enrichment/orchestrator.py`
- Modify: `backend/src/musicmind/engine/explanations.py`

- [ ] **Step 1: Add caption generation to enrichment pipeline**

After Essentia extracts features + tags, generate a caption via OpenAI:
```python
if settings.openai_api_key and features:
    caption = await generate_track_caption(
        features.to_full_dict(), settings.openai_api_key,
    )
    await _store_caption(engine, catalog_id, user_id, caption)
```

- [ ] **Step 2: Add batch caption generation for existing tracks**

Worker phase: for songs that have features but no caption, generate captions in batches.

- [ ] **Step 3: Commit + push**

---

## Task 9: Version Bump + Documentation

**Files:**
- Modify: `VERSION` → `6.100`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 1: Update all docs**
- [ ] **Step 2: Final commit + push**

---

## Execution Order

Tasks are ordered by dependency:
1. **Task 1** (classifiers) — no deps, CPU-only
2. **Task 2** (migration) — no deps
3. **Task 5** (OpenAI explanations) — no deps
4. **Task 3** (Modal worker) — no deps
5. **Task 4** (GPU client) — depends on Task 3
6. **Task 6** (multi-signal similarity) — depends on Task 2
7. **Task 7** (search) — depends on Tasks 3, 4, 6
8. **Task 8** (captions pipeline) — depends on Tasks 1, 5
9. **Task 9** (docs) — last

Tasks 1, 2, 3, 5 can run in parallel.
