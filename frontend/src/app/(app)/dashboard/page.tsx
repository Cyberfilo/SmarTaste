"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { toast } from "sonner";
import {
  Calendar,
  Database,
  Fingerprint,
  Music,
  Plug,
  Radar as RadarIcon,
  Sparkles,
  TrendingUp,
  Users,
  Waves,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  useCalibrationEntries,
  useCalibrationStatus,
  type CalibrationItem,
} from "@/hooks/use-calibration";
import { useServices } from "@/hooks/use-services";
import {
  useRecentEnrichments,
  useSonicNeighbors,
  useTasteProfile,
} from "@/hooks/use-taste";
import type {
  RecentEnrichment,
  SonicNeighbor,
  TasteProfile,
} from "@/types/api";

const tabs = [
  { href: "/dashboard/taste", label: "Taste Profile" },
  { href: "/dashboard/stats", label: "Listening Stats" },
  { href: "/dashboard/recommendations", label: "Recommendations" },
];

// ── Trait configuration ──────────────────────────────────────────────

const TRAIT_ORDER = [
  "energy",
  "danceability",
  "valence_proxy",
  "brightness",
  "beat_strength",
  "acousticness",
  "tempo",
] as const;

const TRAIT_LABELS: Record<string, string> = {
  energy: "Energy",
  danceability: "Danceability",
  valence_proxy: "Brightness",
  brightness: "Timbre",
  beat_strength: "Beat",
  acousticness: "Acoustic",
  tempo: "Tempo",
};

const TRAIT_DESCRIPTIONS: Record<string, string> = {
  energy: "Loudness and intensity",
  danceability: "Rhythmic stability",
  valence_proxy: "Positive / uplifting feel",
  brightness: "High-frequency content",
  beat_strength: "Percussive drive",
  acousticness: "Acoustic vs. electronic",
  tempo: "Preference for faster BPM",
};

// ── Narrative generation ─────────────────────────────────────────────

function describeSound(traits: Record<string, number>): string[] {
  const descriptors: string[] = [];
  const energy = traits.energy ?? 0;
  const dance = traits.danceability ?? 0;
  const valence = traits.valence_proxy ?? 0;
  const acoustic = traits.acousticness ?? 0;
  const tempoBpm = traits.tempo ?? 0; // raw BPM (60-200 range)
  const beat = traits.beat_strength ?? 0;

  if (energy > 0.68) descriptors.push("high-energy");
  else if (energy > 0 && energy < 0.38) descriptors.push("laid-back");

  if (dance > 0.62 || beat > 0.62) descriptors.push("rhythm-forward");
  else if (dance > 0 && dance < 0.38) descriptors.push("introspective");

  if (valence > 0.58) descriptors.push("uplifting");
  else if (valence > 0 && valence < 0.4) descriptors.push("moody");

  if (acoustic > 0.5) descriptors.push("organic");
  else if (acoustic > 0 && acoustic < 0.2) descriptors.push("electronic");

  if (descriptors.length < 2) {
    if (tempoBpm >= 130) descriptors.push("up-tempo");
    else if (tempoBpm > 0 && tempoBpm < 90) descriptors.push("slow-paced");
  }

  return descriptors.slice(0, 3);
}

function clamp01(v: number): number {
  if (!Number.isFinite(v) || v <= 0) return 0;
  if (v >= 1) return 1;
  return v;
}

function normalizeTraitValue(key: string, raw: number): number {
  // Essentia returns raw BPM for tempo (60-200). Everything else is nominally
  // 0-1 but danceability / beat_strength can exceed 1.0 on extreme tracks.
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  if (key === "tempo") {
    // Normalize 60-200 BPM to 0-1 for the radar visualization.
    const clamped = Math.max(60, Math.min(200, raw));
    return (clamped - 60) / 140;
  }
  return clamp01(raw);
}

function formatTraitValue(key: string, raw: number): string {
  if (!Number.isFinite(raw) || raw <= 0) return "—";
  if (key === "tempo") return `${Math.round(raw)} BPM`;
  return `${Math.round(clamp01(raw) * 100)}%`;
}

function topGenreName(genreVector: Record<string, number>): string | null {
  const entries = Object.entries(genreVector);
  if (entries.length === 0) return null;
  const [name] = entries.sort(([, a], [, b]) => b - a)[0];
  return name;
}

function describeEra(yearDist: Record<string, number>): {
  range: string;
  peak: string;
  peakShare: number;
} | null {
  const entries = Object.entries(yearDist)
    .map(([y, c]) => [Number(y), Number(c)] as const)
    .filter(([y, c]) => y > 1950 && y < 2100 && c > 0)
    .sort((a, b) => a[0] - b[0]);
  if (entries.length === 0) return null;
  const total = entries.reduce((sum, [, c]) => sum + c, 0);
  const minYear = entries[0][0];
  const maxYear = entries[entries.length - 1][0];
  const peak = entries.reduce((best, cur) => (cur[1] > best[1] ? cur : best));
  return {
    range: minYear === maxYear ? `${minYear}` : `${minYear}–${maxYear}`,
    peak: String(peak[0]),
    peakShare: Math.round((peak[1] / total) * 100),
  };
}

function formatService(services: string[], fallback: string): string {
  const src = services.length > 0 ? services : [fallback];
  return src
    .map((s) =>
      s === "apple_music"
        ? "Apple Music"
        : s === "spotify"
          ? "Spotify"
          : s.replace(/_/g, " "),
    )
    .join(" + ");
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "—";
  const diffMs = Date.now() - then.getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return (parts[0][0]! + parts[parts.length - 1][0]!).toUpperCase();
}

function breadthBand(score: number | undefined): string {
  if (score === undefined || score <= 0) return "—";
  if (score < 0.33) return "focused";
  if (score < 0.55) return "balanced";
  if (score < 0.75) return "broad";
  return "eclectic";
}

// ── Skeletons ────────────────────────────────────────────────────────

function SkeletonHero() {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="animate-pulse space-y-6">
          <div className="h-4 w-36 rounded bg-muted" />
          <div className="h-8 w-3/4 rounded bg-muted" />
          <div className="grid gap-6 md:grid-cols-[260px_1fr]">
            <div className="h-52 rounded bg-muted" />
            <div className="space-y-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-4 w-full rounded bg-muted" />
              ))}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonBlock() {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="animate-pulse space-y-4">
          <div className="h-3 w-28 rounded bg-muted" />
          <div className="h-44 rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  );
}

// ── Card sub-components ──────────────────────────────────────────────

function SectionLabel({
  icon: Icon,
  children,
  hint,
}: {
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  hint?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium uppercase tracking-wider">
          {children}
        </span>
      </div>
      {hint ? (
        <span className="text-[11px] text-muted-foreground">{hint}</span>
      ) : null}
    </div>
  );
}

function Artwork({
  url,
  fallbackText,
  size = "md",
  rounded = "md",
}: {
  url: string | null | undefined;
  fallbackText: string;
  size?: "xs" | "sm" | "md" | "lg";
  rounded?: "md" | "full";
}) {
  const sizeClass = {
    xs: "h-8 w-8 text-[10px]",
    sm: "h-10 w-10 text-[11px]",
    md: "h-12 w-12 text-xs",
    lg: "h-16 w-16 text-sm",
  }[size];
  const roundedClass = rounded === "full" ? "rounded-full" : "rounded-md";

  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={url}
        alt=""
        aria-hidden="true"
        loading="lazy"
        className={`${sizeClass} ${roundedClass} shrink-0 object-cover ring-1 ring-border/40`}
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      className={`${sizeClass} ${roundedClass} flex shrink-0 items-center justify-center bg-gradient-to-br from-purple-700/40 to-purple-900/60 font-semibold text-purple-100 ring-1 ring-border/40`}
    >
      {initials(fallbackText)}
    </div>
  );
}

function SoundSignature({ profile }: { profile: TasteProfile }) {
  // Prefer audio_centroid (real Essentia scalars) — falls back to the
  // legacy audio_trait_preferences field only if the centroid is empty,
  // which shouldn't happen once the profile is rebuilt post-V6.365.
  const rawCentroid = profile.audio_centroid ?? {};
  const hasCentroid = Object.values(rawCentroid).some((v) => v > 0);
  const traits: Record<string, number> = hasCentroid
    ? rawCentroid
    : (profile.audio_trait_preferences ?? {});

  const orderedTraits = TRAIT_ORDER.map((k) => ({
    key: k,
    label: TRAIT_LABELS[k],
    description: TRAIT_DESCRIPTIONS[k],
    raw: traits[k] ?? 0,
    normalized: normalizeTraitValue(k, traits[k] ?? 0),
  })).filter((t) => t.raw > 0);

  const hasTraits = orderedTraits.length >= 3;
  const descriptors = hasTraits ? describeSound(traits) : [];
  const topGenre = topGenreName(profile.genre_vector);

  const radarData = orderedTraits.map((t) => ({
    trait: t.label,
    value: Math.round(t.normalized * 100),
    fullMark: 100,
  }));

  return (
    <Card className="overflow-hidden border-purple-500/20 bg-gradient-to-br from-purple-500/[0.04] to-transparent">
      <CardContent className="pt-5">
        <SectionLabel icon={Waves}>Sound Signature</SectionLabel>

        {hasTraits ? (
          <>
            <p className="mb-5 text-lg leading-snug sm:text-xl">
              Your library sounds{" "}
              {descriptors.map((d, i) => (
                <span key={d}>
                  <span className="font-semibold text-purple-300">{d}</span>
                  {i < descriptors.length - 1
                    ? i === descriptors.length - 2
                      ? ", and "
                      : ", "
                    : ""}
                </span>
              ))}
              {topGenre ? (
                <>
                  {" "}— rooted in{" "}
                  <span className="font-semibold text-purple-300">
                    {topGenre}
                  </span>
                </>
              ) : null}
              .
            </p>

            <div className="grid gap-6 md:grid-cols-[260px_1fr]">
              <div className="mx-auto w-full max-w-[280px]">
                <ResponsiveContainer width="100%" height={230}>
                  <RadarChart
                    cx="50%"
                    cy="50%"
                    outerRadius="75%"
                    data={radarData}
                  >
                    <PolarGrid stroke="hsl(var(--border))" />
                    <PolarAngleAxis
                      dataKey="trait"
                      tick={{
                        fill: "hsl(var(--muted-foreground))",
                        fontSize: 10,
                      }}
                    />
                    <Radar
                      dataKey="value"
                      stroke="#A855F7"
                      fill="#A855F7"
                      fillOpacity={0.25}
                      strokeWidth={2}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      formatter={(v) => [`${v}%`, ""]}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>

              <ul className="grid grid-cols-1 gap-2 self-center sm:grid-cols-2">
                {orderedTraits.map((t) => {
                  const barPct = Math.round(t.normalized * 100);
                  const display = formatTraitValue(t.key, t.raw);
                  return (
                    <li key={t.key} className="space-y-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-medium">{t.label}</span>
                        <span className="text-sm font-semibold tabular-nums text-purple-300">
                          {display}
                        </span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-purple-600 to-purple-400 transition-all duration-700"
                          style={{ width: `${barPct}%` }}
                        />
                      </div>
                      <p className="text-[11px] text-muted-foreground">
                        {t.description}
                      </p>
                    </li>
                  );
                })}
              </ul>
            </div>
          </>
        ) : (
          <div className="flex flex-col items-start gap-3 py-4">
            <p className="text-sm text-muted-foreground">
              Your sound signature is still being computed. As the worker
              enriches your library with audio features, this card will fill
              in.
            </p>
            <p className="text-xs text-muted-foreground">
              {profile.total_songs_analyzed.toLocaleString()} song
              {profile.total_songs_analyzed === 1 ? "" : "s"} queued for
              analysis.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SonicNeighborsCard() {
  const { data, isLoading } = useSonicNeighbors(8);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={RadarIcon}>Sonic Neighbors</SectionLabel>
          <div className="grid animate-pulse grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-3 rounded-lg bg-muted/40 p-3"
              >
                <div className="h-12 w-12 shrink-0 rounded-md bg-muted" />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="h-3 w-3/4 rounded bg-muted" />
                  <div className="h-2 w-1/2 rounded bg-muted" />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const neighbors = data?.neighbors ?? [];
  const note = data?.note;

  if (neighbors.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={RadarIcon}>Sonic Neighbors</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            {note ??
              "No sonic neighbors to show yet — keep listening while the pipeline finishes."}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <SectionLabel
          icon={RadarIcon}
          hint="CLAP-centroid match · discovery only"
        >
          Sonic Neighbors
        </SectionLabel>
        <p className="mb-3 text-xs text-muted-foreground">
          Artists not in your library whose sound most closely matches your
          CLAP centroid.
        </p>
        <ul className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {neighbors.map((n: SonicNeighbor) => {
            const pct = Math.round(Math.max(0, n.similarity) * 100);
            return (
              <li key={n.artist_name}>
                <div className="group flex items-center gap-3 rounded-lg border border-border/40 bg-muted/20 p-2.5 transition-colors hover:border-purple-500/40 hover:bg-purple-500/5">
                  <Artwork
                    url={n.artwork_url}
                    fallbackText={n.artist_name}
                    size="md"
                    rounded="md"
                  />
                  <div className="min-w-0 flex-1">
                    <p
                      className="truncate text-sm font-medium"
                      title={n.artist_name}
                    >
                      {n.artist_name}
                    </p>
                    <p
                      className="truncate text-[11px] text-muted-foreground"
                      title={n.sample_song_name}
                    >
                      {n.sample_song_name || "—"}
                    </p>
                    <div className="mt-1.5 flex items-center gap-2">
                      <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted/60">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-purple-600 to-purple-400"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="text-[10px] font-semibold tabular-nums text-purple-300">
                        {pct}%
                      </span>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function GenreDNA({ genreVector }: { genreVector: Record<string, number> }) {
  const entries = Object.entries(genreVector)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 8);

  if (entries.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Fingerprint}>Genre DNA</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            No genres detected yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const max = entries[0][1] || 1;

  return (
    <Card>
      <CardContent className="pt-4">
        <SectionLabel icon={Fingerprint}>Genre DNA</SectionLabel>
        <ul className="space-y-2.5">
          {entries.map(([name, weight], i) => {
            const pct = Math.round((weight / max) * 100);
            const absPct = Math.round(weight * 100);
            return (
              <li key={name} className="flex items-center gap-3">
                <span className="w-4 text-right text-xs font-medium tabular-nums text-muted-foreground">
                  {i + 1}
                </span>
                <span
                  className="flex-1 truncate text-sm font-medium"
                  title={name}
                >
                  {name}
                </span>
                <div className="w-24 sm:w-36">
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-purple-600 to-purple-400 transition-all duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
                <span className="w-9 text-right text-xs font-semibold tabular-nums">
                  {absPct}%
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function TopArtistsCard({
  artists,
}: {
  artists: TasteProfile["top_artists"];
}) {
  const top = artists.slice(0, 8);
  if (top.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Users}>Top Artists</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            No artists ranked yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <SectionLabel icon={Users}>Top Artists</SectionLabel>
        <ul className="space-y-2">
          {top.map((artist, i) => {
            const pct = Math.round(artist.score * 100);
            return (
              <li
                key={artist.name}
                className="flex items-center gap-3 rounded-md px-1 py-1 transition-colors hover:bg-muted/40"
              >
                <span className="w-4 text-right text-xs font-medium tabular-nums text-muted-foreground">
                  {i + 1}
                </span>
                <Artwork
                  url={artist.sample_artwork_url}
                  fallbackText={artist.name}
                  size="sm"
                  rounded="full"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{artist.name}</p>
                  <p className="text-[11px] text-muted-foreground">
                    {artist.song_count} song
                    {artist.song_count === 1 ? "" : "s"}
                  </p>
                </div>
                <div className="w-16 sm:w-24">
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-purple-600 to-purple-400 transition-all duration-500"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
                <span className="w-9 text-right text-xs font-semibold tabular-nums">
                  {pct}%
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function RecentTrackTile({ item }: { item: RecentEnrichment }) {
  const bpm = item.tempo ? Math.round(item.tempo) : null;
  return (
    <div className="w-36 shrink-0 space-y-1.5">
      {item.artwork_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={item.artwork_url}
          alt=""
          aria-hidden="true"
          loading="lazy"
          className="h-36 w-36 rounded-md object-cover ring-1 ring-border/40"
        />
      ) : (
        <div
          aria-hidden="true"
          className="flex h-36 w-36 items-center justify-center rounded-md bg-gradient-to-br from-purple-700/40 to-purple-900/60 text-sm font-semibold text-purple-100 ring-1 ring-border/40"
        >
          {initials(item.artist_name || item.name)}
        </div>
      )}
      <div className="min-w-0">
        <p className="truncate text-xs font-semibold" title={item.name}>
          {item.name || "—"}
        </p>
        <p
          className="truncate text-[10px] text-muted-foreground"
          title={item.artist_name}
        >
          {item.artist_name || "—"}
        </p>
        <p className="mt-0.5 text-[10px] text-purple-300">
          {bpm ? `${bpm} BPM` : ""}
          {bpm && item.enriched_at ? " · " : ""}
          {formatRelative(item.enriched_at)}
        </p>
      </div>
    </div>
  );
}

function RecentlyAnalyzedStrip() {
  const { data, isLoading } = useRecentEnrichments(12);

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Zap}>Recently Analyzed</SectionLabel>
          <div className="flex animate-pulse gap-3 overflow-hidden">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="w-36 shrink-0 space-y-2">
                <div className="h-36 w-36 rounded-md bg-muted" />
                <div className="h-3 w-3/4 rounded bg-muted" />
                <div className="h-2 w-1/2 rounded bg-muted" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Zap}>Recently Analyzed</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            The worker hasn't analyzed any songs for you yet — this strip
            fills in as the pipeline processes your library.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <SectionLabel
          icon={Zap}
          hint={`${items.length} most recent · freshest first`}
        >
          Recently Analyzed
        </SectionLabel>
        <div className="-mx-4 flex gap-3 overflow-x-auto px-4 pb-2 [scrollbar-width:thin]">
          {items.map((item: RecentEnrichment) => (
            <RecentTrackTile key={item.catalog_id} item={item} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ReleaseEraCard({
  yearDist,
}: {
  yearDist: Record<string, number>;
}) {
  const era = describeEra(yearDist);
  const recent = Object.entries(yearDist)
    .map(([y, c]) => [Number(y), Number(c)] as const)
    .filter(([y, c]) => y > 1950 && y < 2100 && c > 0)
    .sort((a, b) => a[0] - b[0])
    .slice(-15);

  if (recent.length === 0 || !era) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Calendar}>Release Era</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            No release years detected yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const chartData = recent.map(([year, songs]) => ({
    year: String(year),
    songs,
    isPeak: String(year) === era.peak,
  }));

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-3 flex items-baseline justify-between gap-4">
          <SectionLabel icon={Calendar}>Release Era</SectionLabel>
          <div className="text-right">
            <p className="text-sm font-semibold tabular-nums">{era.range}</p>
            <p className="text-[11px] text-muted-foreground">
              Peak: <span className="text-purple-300">{era.peak}</span> ·{" "}
              {era.peakShare}% of library
            </p>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart
            data={chartData}
            margin={{ top: 5, right: 8, bottom: 0, left: -20 }}
          >
            <defs>
              <linearGradient id="eraGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#A855F7" stopOpacity={0.45} />
                <stop offset="95%" stopColor="#A855F7" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="year"
              tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
              width={32}
            />
            <Tooltip
              contentStyle={{
                background: "hsl(var(--card))",
                border: "1px solid hsl(var(--border))",
                borderRadius: "8px",
                fontSize: "12px",
              }}
              formatter={(v) => [`${v} songs`, "Count"]}
            />
            <Area
              type="monotone"
              dataKey="songs"
              stroke="#A855F7"
              strokeWidth={2}
              fill="url(#eraGradient)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

function LibrarySnapshot({ profile }: { profile: TasteProfile }) {
  const uniqueGenres = Object.keys(profile.genre_vector).length;
  const distinctArtists = profile.top_artists.length;
  const service = formatService(
    profile.services_included ?? [],
    profile.service,
  );
  const breadth = profile.breadth;
  const sonicBreadthPct = breadth
    ? Math.round(breadth.sonic_breadth * 100)
    : null;
  const breadthLabel = breadthBand(breadth?.sonic_breadth);
  const concentrationPct = breadth
    ? Math.round(breadth.artist_concentration * 100)
    : null;

  const rows: { label: string; value: string; hint?: string }[] = [
    {
      label: "Songs analyzed",
      value: profile.total_songs_analyzed.toLocaleString(),
    },
    {
      label: "Genres detected",
      value: uniqueGenres.toLocaleString(),
      hint: uniqueGenres > 0 ? "regional-weighted" : undefined,
    },
    {
      label: "Ranked artists",
      value: distinctArtists.toLocaleString(),
    },
    ...(sonicBreadthPct !== null
      ? [
          {
            label: "Sonic breadth",
            value: `${sonicBreadthPct}%`,
            hint: breadthLabel,
          },
        ]
      : []),
    ...(concentrationPct !== null
      ? [
          {
            label: "Top-5 concentration",
            value: `${concentrationPct}%`,
            hint:
              concentrationPct > 70
                ? "artist-focused"
                : concentrationPct > 40
                  ? "balanced"
                  : "spread out",
          },
        ]
      : []),
    { label: "Source", value: service },
    { label: "Last updated", value: formatRelative(profile.computed_at) },
  ];

  return (
    <Card>
      <CardContent className="pt-4">
        <SectionLabel icon={Database} hint={<TrendingUp className="h-3 w-3" />}>
          Library Snapshot
        </SectionLabel>
        <dl className="divide-y divide-border/40">
          {rows.map((r) => (
            <div
              key={r.label}
              className="flex items-baseline justify-between gap-4 py-2 first:pt-0 last:pb-0"
            >
              <dt className="text-xs text-muted-foreground">{r.label}</dt>
              <dd className="text-right">
                <span className="text-sm font-semibold tabular-nums">
                  {r.value}
                </span>
                {r.hint ? (
                  <span className="ml-1.5 text-[10px] text-muted-foreground">
                    · {r.hint}
                  </span>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

// ── Main page ────────────────────────────────────────────────────────

export default function DashboardPage() {
  const pathname = usePathname();
  const { data: profile, isLoading, error } = useTasteProfile();
  const { data: calibrationStatus } = useCalibrationStatus();
  const { data: servicesData } = useServices();
  const { data: calibrationEntries } = useCalibrationEntries();

  const hasConnectedService = servicesData?.services.some(
    (s) => s.status === "connected",
  );
  const showCalibrationBanner =
    hasConnectedService && calibrationStatus && !calibrationStatus.completed;
  const showCalibrationSummary =
    calibrationStatus?.completed && calibrationEntries?.items?.length;

  const calTopArtists = (calibrationEntries?.items || []).filter(
    (i: CalibrationItem) => i.calibration_type === "top_artist",
  );
  const calPlaylists = (calibrationEntries?.items || []).filter(
    (i: CalibrationItem) => i.calibration_type === "playlist",
  );
  const calSongs = (calibrationEntries?.items || []).filter(
    (i: CalibrationItem) => i.calibration_type === "playlist_song",
  );

  useEffect(() => {
    if (error) {
      toast.error("Failed to load taste profile", {
        description: error.message,
      });
    }
  }, [error]);

  const isEmpty = !isLoading && !error && !profile;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
        Dashboard
      </h1>

      {/* Calibration banner */}
      {showCalibrationBanner && (
        <Card className="border-purple-500/30 bg-purple-500/5">
          <CardContent className="flex items-center gap-4 py-3">
            <Sparkles className="h-5 w-5 shrink-0 text-purple-400" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Calibrate your taste</p>
              <p className="text-xs text-muted-foreground">
                Tell us which albums, artists, and songs matter most to you for
                better recommendations.
              </p>
            </div>
            <Link href="/onboarding">
              <Button
                size="sm"
                variant="outline"
                className="shrink-0 border-purple-500/30 text-purple-300 hover:bg-purple-500/10"
              >
                Start
              </Button>
            </Link>
          </CardContent>
        </Card>
      )}

      {/* Calibration summary */}
      {showCalibrationSummary && (
        <Card className="border-purple-500/20 bg-purple-500/5">
          <CardContent className="py-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-purple-400" />
                <span className="text-xs font-medium uppercase tracking-wider text-purple-400">
                  Your Calibration
                </span>
              </div>
              <Link href="/onboarding">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-xs text-muted-foreground"
                >
                  Edit
                </Button>
              </Link>
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              {calTopArtists.length > 0 && (
                <span>
                  <Users className="mr-1 inline h-3 w-3 text-purple-400" />
                  Top:{" "}
                  {calTopArtists
                    .map((a: CalibrationItem) => a.item_name)
                    .join(", ")}
                </span>
              )}
              {calPlaylists.length > 0 && (
                <span>
                  <Music className="mr-1 inline h-3 w-3 text-purple-400" />
                  {calPlaylists.length} playlist
                  {calPlaylists.length !== 1 ? "s" : ""}
                </span>
              )}
              {calSongs.length > 0 && (
                <span>
                  <Music className="mr-1 inline h-3 w-3 text-purple-400" />
                  {calSongs.length} fav song{calSongs.length !== 1 ? "s" : ""}
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Main content */}
      {isLoading ? (
        <>
          <SkeletonHero />
          <SkeletonBlock />
          <div className="grid gap-4 md:grid-cols-2">
            <SkeletonBlock />
            <SkeletonBlock />
          </div>
          <SkeletonBlock />
          <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
            <SkeletonBlock />
            <SkeletonBlock />
          </div>
        </>
      ) : isEmpty ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
            <Plug className="h-10 w-10 text-muted-foreground" />
            <div>
              <p className="text-lg font-medium">No music data yet</p>
              <p className="text-sm text-muted-foreground">
                Connect a service to see your music profile.
              </p>
            </div>
            <Link
              href="/settings"
              className="mt-2 inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Go to Settings
            </Link>
          </CardContent>
        </Card>
      ) : profile ? (
        <>
          <SoundSignature profile={profile} />

          <SonicNeighborsCard />

          <div className="grid gap-4 md:grid-cols-2">
            <GenreDNA genreVector={profile.genre_vector} />
            <TopArtistsCard artists={profile.top_artists} />
          </div>

          <RecentlyAnalyzedStrip />

          <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
            <ReleaseEraCard yearDist={profile.release_year_distribution} />
            <LibrarySnapshot profile={profile} />
          </div>
        </>
      ) : null}

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                isActive
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
