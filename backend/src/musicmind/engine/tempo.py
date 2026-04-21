"""Tempo-band psychology — maps BPM to psychologically meaningful zones.

The research (Moelants 2002; Bernardi, Porta & Sleight 2006; Van Dyck 2017)
is unanimous that raw BPM delta is the wrong similarity signal. Tempo groups
into five arousal-equivalence zones anchored on bodily entrainment ranges:
resting-heart-rate, walking, brisk-walk, fast-motor, and driven-urgency.

Two-in-one-zone BPM pairs (say 74 and 82) are functionally identical for a
listener, while a 60-BPM ballad and a 90-BPM groove are subjectively very
different despite both being "slow." Raw euclidean blurs that distinction.

Halftime-feel detection is a separate signal orthogonal to the band.
A 142 BPM drill track with kick-on-1 / snare-on-3 / double-time hats
feels like 71 BPM in the body while sounding busy to the ear; classical
scalar-similarity scoring collapses this into generic "140 BPM." For a
listener whose top plays are Italian drill, this dissociation IS their
taste — not a confound to be smoothed over.
"""

from __future__ import annotations

import math

# ── Tempo bands ──────────────────────────────────────────────────────────
# Bands labelled by the dominant physiological anchor + musical niche.
# Names intentionally avoid genre labels — a 140 BPM drill and a 140 BPM
# techno both live in FAST_MOTOR, the halftime flag handles the drill-
# specific dissociation.

BAND_RESTING = "resting"       # 40–80  — ballad, chillhop, lo-fi, classic R&B
BAND_WALKING = "walking"       # 80–110 — classic hip-hop, soul, rock
BAND_BRISK = "brisk"           # 110–130 — house/pop/dance entry
BAND_FAST_MOTOR = "fast_motor" # 130–160 — drill, trap, techno, dnb-halftime
BAND_DRIVEN = "driven"         # 160–200+ — dnb, jungle, gabber, hardcore

ALL_BANDS = (BAND_RESTING, BAND_WALKING, BAND_BRISK, BAND_FAST_MOTOR, BAND_DRIVEN)

# Band centers for smoothed-histogram contributions. A 132 BPM track is
# mostly FAST_MOTOR but not *only* — some probability leaks to BRISK.
_BAND_CENTERS = {
    BAND_RESTING: 60.0,
    BAND_WALKING: 95.0,
    BAND_BRISK: 120.0,
    BAND_FAST_MOTOR: 145.0,
    BAND_DRIVEN: 180.0,
}
# Standard deviation for Gaussian membership. Chosen so adjacent bands
# overlap at ~30% membership — matches how listeners actually perceive.
_BAND_SIGMA = 18.0


def tempo_band(bpm: float | None) -> str:
    """Return the dominant band label for a BPM. Defaults to WALKING on None."""
    if bpm is None or bpm <= 0:
        return BAND_WALKING
    if bpm < 80:
        return BAND_RESTING
    if bpm < 110:
        return BAND_WALKING
    if bpm < 130:
        return BAND_BRISK
    if bpm < 160:
        return BAND_FAST_MOTOR
    return BAND_DRIVEN


def tempo_band_membership(bpm: float | None) -> dict[str, float]:
    """Soft membership vector over the five bands (sums to 1.0).

    Gaussian kernel around each band center. A track at 125 BPM gets
    ~0.6 BRISK + ~0.3 FAST_MOTOR + residuals; a track at 200 BPM gets
    mostly DRIVEN. Used to build user histograms that don't knife-edge.
    """
    if bpm is None or bpm <= 0:
        return {b: 1.0 / len(ALL_BANDS) for b in ALL_BANDS}
    raw = {
        b: math.exp(-((bpm - center) ** 2) / (2 * _BAND_SIGMA ** 2))
        for b, center in _BAND_CENTERS.items()
    }
    total = sum(raw.values())
    if total <= 0:
        return {b: 1.0 / len(ALL_BANDS) for b in ALL_BANDS}
    return {b: v / total for b, v in raw.items()}


def tempo_band_distribution(
    bpms: list[float | None],
    weights: list[float] | None = None,
) -> dict[str, float]:
    """Build a normalized histogram of tempo-band membership across tracks.

    With optional engagement weights (play count, feedback, recency) so
    a library of 300 tracks with 10 heavily-played drill tracks doesn't
    get swamped by 290 casual WALKING-band tracks.
    """
    if not bpms:
        return {b: 0.0 for b in ALL_BANDS}

    weights = weights or [1.0] * len(bpms)
    accum: dict[str, float] = {b: 0.0 for b in ALL_BANDS}
    total_weight = 0.0
    for bpm, w in zip(bpms, weights):
        if w <= 0:
            continue
        m = tempo_band_membership(bpm)
        for b, p in m.items():
            accum[b] += p * w
        total_weight += w

    if total_weight <= 0:
        return {b: 0.0 for b in ALL_BANDS}
    return {b: round(v / total_weight, 4) for b, v in accum.items()}


def tempo_band_similarity(
    user_distribution: dict[str, float] | None,
    candidate_bpm: float | None,
) -> float:
    """Probability mass the user's band distribution assigns to the candidate.

    Intuition: if 55% of the user's weighted library sits in FAST_MOTOR and
    the candidate is at 145 BPM (mostly FAST_MOTOR), return ~0.55 with a
    small residual from adjacent bands. A WALKING track against the same
    user returns ~0.10. Bounded to [0, 1]; no penalty beyond low match.
    """
    if not user_distribution or candidate_bpm is None or candidate_bpm <= 0:
        return 0.0
    cand_membership = tempo_band_membership(candidate_bpm)
    dot = sum(
        user_distribution.get(b, 0.0) * cand_membership.get(b, 0.0)
        for b in ALL_BANDS
    )
    # Normalize by max possible (user fully in one band + candidate centered)
    # to keep the dim in a comfortable [0, 1] range.
    user_max = max(user_distribution.values()) if user_distribution else 1.0
    cand_max = max(cand_membership.values()) if cand_membership else 1.0
    denom = user_max * cand_max
    if denom <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / denom))


# ── Halftime-feel detection ─────────────────────────────────────────────
# Drill/trap: 130–160 BPM grid, kick-on-1 / snare-on-3 (slow pulse) with
# double-time hi-hats (busy surface). Body entrains to ~70 BPM pulse; ear
# hears 140 BPM activity. Essentia's scalar features don't expose the
# metrical placement directly, but three signals correlate strongly:
#   - tempo in the 130–160 band (otherwise the dissociation isn't possible)
#   - moderate-to-high beat_strength (clear pulse, not ambient wash)
#   - moderate danceability (halftime feels like "groove with suspended
#     motion" — lower than peak-dance tracks, higher than ballads)
# A noisy proxy but good enough to be useful as a tie-breaker. Calibrated
# against the user's library ratio below, so the feature is self-normalizing.


def halftime_feel_score(
    tempo: float | None,
    beat_strength: float | None,
    danceability: float | None,
) -> float:
    """Probability the track has a halftime feel (0–1).

    Returns 0.0 unless tempo is in the 130–160 band. Inside that band,
    score combines beat-strength presence (the pulse is audible) and
    moderate danceability (halftime sits around 0.5–0.75, not 0.9+).
    """
    if tempo is None or tempo < 128 or tempo > 165:
        return 0.0

    # Tempo-in-band Gaussian weight, peaking at ~142 (drill/trap mode).
    tempo_fit = math.exp(-((tempo - 142.0) ** 2) / (2 * 12.0 ** 2))

    bs = beat_strength if beat_strength is not None else 0.5
    d = danceability if danceability is not None else 0.5

    # Beat-strength needs to be at least mid to indicate pulse, not wash.
    if bs < 0.35:
        return 0.0

    # Danceability sweet spot for halftime is roughly 0.45–0.80.
    # Use an inverted-U: penalize at the extremes (sleepy ballad / peak-dance).
    d_fit = math.exp(-((d - 0.62) ** 2) / (2 * 0.18 ** 2))

    return round(tempo_fit * bs * d_fit, 3)


def halftime_ratio(
    tempos: list[float | None],
    beat_strengths: list[float | None],
    danceabilities: list[float | None],
    weights: list[float] | None = None,
    threshold: float = 0.35,
) -> float:
    """Fraction of the (engagement-weighted) library that reads as halftime.

    A ratio above ~0.30 means the user's baseline taste strongly includes
    halftime-feel tracks — the scorer uses this to unlock a halftime
    bonus for matching candidates.
    """
    if not tempos:
        return 0.0
    weights = weights or [1.0] * len(tempos)
    total_weight = 0.0
    halftime_weight = 0.0
    for t, bs, d, w in zip(tempos, beat_strengths, danceabilities, weights):
        if w <= 0:
            continue
        total_weight += w
        if halftime_feel_score(t, bs, d) >= threshold:
            halftime_weight += w
    if total_weight <= 0:
        return 0.0
    return round(halftime_weight / total_weight, 4)
