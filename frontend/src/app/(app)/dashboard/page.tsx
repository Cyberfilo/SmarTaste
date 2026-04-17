"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { toast } from "sonner";
import {
  Activity,
  Database,
  Fingerprint,
  KeyRound,
  Music,
  Plug,
  Sparkles,
  Timer,
  Users,
  Waves,
  Zap,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
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
  useLibraryDistributions,
  useRecentEnrichments,
  useTasteProfile,
} from "@/hooks/use-taste";
import type {
  KeyBucket,
  RecentEnrichment,
  ScatterPoint,
  TasteProfile,
  TempoBin,
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

// ── Value helpers ────────────────────────────────────────────────────

function clamp01(v: number): number {
  if (!Number.isFinite(v) || v <= 0) return 0;
  if (v >= 1) return 1;
  return v;
}

function normalizeTraitValue(key: string, raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 0;
  if (key === "tempo") {
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

// ── Narrative generation ─────────────────────────────────────────────

function describeSound(traits: Record<string, number>): string[] {
  const descriptors: string[] = [];
  const energy = traits.energy ?? 0;
  const dance = traits.danceability ?? 0;
  const valence = traits.valence_proxy ?? 0;
  const acoustic = traits.acousticness ?? 0;
  const tempoBpm = traits.tempo ?? 0;
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

function topGenreName(genreVector: Record<string, number>): string | null {
  const entries = Object.entries(genreVector);
  if (entries.length === 0) return null;
  const [name] = entries.sort(([, a], [, b]) => b - a)[0];
  return name;
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

// ── Shared sub-components ────────────────────────────────────────────

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

// ── Sound Signature hero ────────────────────────────────────────────

function SoundSignature({ profile }: { profile: TasteProfile }) {
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

// ── Genre DNA & Top Artists ──────────────────────────────────────────

function GenreDNA({ genreVector }: { genreVector: Record<string, number> }) {
  const entries = Object.entries(genreVector)
    .filter(([name, w]) => name && w > 0)
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
                <div className="w-20 sm:w-32">
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
                <div className="w-14 sm:w-20">
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

// ── Sound Space (energy × danceability scatter) ──────────────────────

function ScatterTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ScatterPoint }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-[#0f0a1f] px-2.5 py-2 text-[11px] text-foreground shadow-lg">
      <p className="font-semibold text-white">{p.name}</p>
      <p className="text-neutral-300">{p.artist_name}</p>
      <p className="mt-1 tabular-nums text-purple-300">
        Energy {Math.round(p.energy * 100)}% · Dance{" "}
        {Math.round(p.danceability * 100)}%
      </p>
    </div>
  );
}

function computeAxisDomain(
  values: number[],
  pad = 0.08,
  min = 0,
  max = 1,
): [number, number] {
  if (values.length === 0) return [min, max];
  const lo = Math.max(min, Math.min(...values) - pad);
  const hi = Math.min(max, Math.max(...values) + pad);
  // Always show at least a 0.35 range so a tightly-clustered library
  // still gets a readable spread rather than a squished line.
  if (hi - lo < 0.35) {
    const mid = (hi + lo) / 2;
    return [
      Math.max(min, mid - 0.175),
      Math.min(max, mid + 0.175),
    ];
  }
  return [Number(lo.toFixed(2)), Number(hi.toFixed(2))];
}

function SoundSpaceCard() {
  const { data, isLoading } = useLibraryDistributions();

  if (isLoading) return <SkeletonBlock />;

  const scatter = data?.scatter ?? [];

  if (scatter.length === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Activity}>Sound Space</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            Not enough enriched songs yet to plot the library's sound space.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Quadrant summary — how does the library cluster?
  const q = { hiHi: 0, hiLo: 0, loHi: 0, loLo: 0 };
  for (const p of scatter) {
    const hiEnergy = p.energy >= 0.5;
    const hiDance = p.danceability >= 0.5;
    if (hiEnergy && hiDance) q.hiHi++;
    else if (hiEnergy && !hiDance) q.hiLo++;
    else if (!hiEnergy && hiDance) q.loHi++;
    else q.loLo++;
  }
  const dominantLabel =
    q.hiHi >= q.hiLo && q.hiHi >= q.loHi && q.hiHi >= q.loLo
      ? "high-energy & danceable"
      : q.hiLo >= q.loHi && q.hiLo >= q.loLo
        ? "high-energy, low-groove"
        : q.loHi >= q.loLo
          ? "groove-heavy, low-intensity"
          : "mellow & sparse";

  const xDomain = computeAxisDomain(scatter.map((p) => p.danceability));
  const yDomain = computeAxisDomain(scatter.map((p) => p.energy));

  return (
    <Card className="min-w-0 overflow-hidden">
      <CardContent className="pt-4">
        <SectionLabel
          icon={Activity}
          hint={`${scatter.length} songs · mostly ${dominantLabel}`}
        >
          Sound Space
        </SectionLabel>
        <p className="mb-2 text-xs text-muted-foreground">
          Every dot is a song. Horizontal = danceability · vertical = energy.
          Hover for the track name.
        </p>
        <div className="w-full overflow-hidden">
          <ResponsiveContainer width="100%" height={240}>
            <ScatterChart margin={{ top: 10, right: 16, bottom: 24, left: 8 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#a855f733"
                vertical={false}
              />
              <XAxis
                type="number"
                dataKey="danceability"
                domain={xDomain}
                tickFormatter={(v) => `${Math.round(v * 100)}%`}
                tick={{ fill: "#a5adba", fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: "#2a2342" }}
                label={{
                  value: "Danceability →",
                  position: "insideBottom",
                  offset: -14,
                  fontSize: 10,
                  fill: "#a5adba",
                }}
              />
              <YAxis
                type="number"
                dataKey="energy"
                domain={yDomain}
                tickFormatter={(v) => `${Math.round(v * 100)}%`}
                tick={{ fill: "#a5adba", fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: "#2a2342" }}
                width={44}
                label={{
                  value: "Energy ↑",
                  angle: -90,
                  position: "insideLeft",
                  offset: 16,
                  fontSize: 10,
                  fill: "#a5adba",
                }}
              />
              <ZAxis type="number" range={[36, 36]} />
              <Tooltip
                cursor={{ strokeDasharray: "3 3", stroke: "#A855F7" }}
                content={<ScatterTooltip />}
              />
              <Scatter data={scatter} fill="#A855F7" fillOpacity={0.65} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Tempo Profile ────────────────────────────────────────────────────

function TempoTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: TempoBin }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-[#0f0a1f] px-2.5 py-2 text-[11px] shadow-lg">
      <p className="font-semibold tabular-nums text-white">{p.range} BPM</p>
      <p className="tabular-nums text-purple-300">{p.count} songs</p>
    </div>
  );
}

function TempoProfileCard() {
  const { data, isLoading } = useLibraryDistributions();

  if (isLoading) return <SkeletonBlock />;

  const hist = data?.tempo_histogram ?? [];
  const total = hist.reduce((s, b) => s + b.count, 0);
  const avg = data?.avg_tempo ?? 0;

  if (total === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={Timer}>Tempo Profile</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            No BPM data yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const peakBin = hist.reduce(
    (best, cur) => (cur.count > best.count ? cur : best),
    hist[0],
  );

  return (
    <Card className="min-w-0 overflow-hidden">
      <CardContent className="pt-4">
        <SectionLabel
          icon={Timer}
          hint={
            <span>
              avg <span className="text-purple-300 tabular-nums">{avg}</span>{" "}
              BPM · peak {peakBin.range}
            </span>
          }
        >
          Tempo Profile
        </SectionLabel>
        <div className="w-full overflow-hidden">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart
              data={hist}
              margin={{ top: 4, right: 8, bottom: 0, left: -16 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#a855f733"
                vertical={false}
              />
              <XAxis
                dataKey="range"
                tick={{ fill: "#a5adba", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fill: "#a5adba", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
                width={28}
              />
              <Tooltip
                cursor={{ fill: "#a855f722" }}
                content={<TempoTooltip />}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={40}>
                {hist.map((b, i) => (
                  <Cell
                    key={i}
                    fill={b.range === peakBin.range ? "#A855F7" : "#7E22CE"}
                    fillOpacity={b.range === peakBin.range ? 1 : 0.55}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Key & Mode ───────────────────────────────────────────────────────

function KeyModeCard() {
  const { data, isLoading } = useLibraryDistributions();

  if (isLoading) return <SkeletonBlock />;

  const keys: KeyBucket[] = data?.key_distribution ?? [];
  const total = keys.reduce((s, k) => s + k.major + k.minor, 0);

  if (total === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <SectionLabel icon={KeyRound}>Musical Keys</SectionLabel>
          <p className="py-6 text-center text-sm text-muted-foreground">
            Key analysis not available yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  const totalMajor = keys.reduce((s, k) => s + k.major, 0);
  const totalMinor = keys.reduce((s, k) => s + k.minor, 0);
  const majorPct = Math.round((totalMajor / total) * 100);
  const minorPct = 100 - majorPct;

  const max = keys.reduce(
    (m, k) => Math.max(m, k.major + k.minor),
    1,
  );

  return (
    <Card className="min-w-0 overflow-hidden">
      <CardContent className="pt-4">
        <SectionLabel
          icon={KeyRound}
          hint={
            <span>
              <span className="text-purple-300 tabular-nums">{majorPct}%</span>{" "}
              major ·{" "}
              <span className="text-purple-300 tabular-nums">{minorPct}%</span>{" "}
              minor
            </span>
          }
        >
          Musical Keys
        </SectionLabel>
        <p className="mb-3 text-xs text-muted-foreground">
          Per-key major (lighter) vs. minor (darker) distribution.
        </p>
        <ul className="space-y-1.5">
          {keys.map((k) => {
            const count = k.major + k.minor;
            const pct = Math.round((count / max) * 100);
            const majorWidth =
              count > 0 ? (k.major / count) * pct : 0;
            const minorWidth =
              count > 0 ? (k.minor / count) * pct : 0;
            return (
              <li key={k.key} className="flex items-center gap-2">
                <span className="w-5 text-right text-[11px] font-semibold tabular-nums text-muted-foreground">
                  {k.key}
                </span>
                <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-muted/50">
                  <div
                    className="absolute inset-y-0 left-0 bg-purple-400/80"
                    style={{ width: `${majorWidth}%` }}
                    title={`${k.major} ${k.key} major`}
                  />
                  <div
                    className="absolute inset-y-0 bg-purple-700"
                    style={{
                      left: `${majorWidth}%`,
                      width: `${minorWidth}%`,
                    }}
                    title={`${k.minor} ${k.key} minor`}
                  />
                </div>
                <span className="w-8 text-right text-[10px] tabular-nums text-muted-foreground">
                  {count}
                </span>
              </li>
            );
          })}
        </ul>
        <div className="mt-3 flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-purple-400/80" /> major
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-purple-700" /> minor
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Recently Analyzed ────────────────────────────────────────────────

function RecentTrackTile({ item }: { item: RecentEnrichment }) {
  const bpm = item.tempo ? Math.round(item.tempo) : null;
  return (
    <div className="w-32 shrink-0 space-y-1.5 sm:w-36">
      {item.artwork_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={item.artwork_url}
          alt=""
          aria-hidden="true"
          loading="lazy"
          className="h-32 w-32 rounded-md object-cover ring-1 ring-border/40 sm:h-36 sm:w-36"
        />
      ) : (
        <div
          aria-hidden="true"
          className="flex h-32 w-32 items-center justify-center rounded-md bg-gradient-to-br from-purple-700/40 to-purple-900/60 text-sm font-semibold text-purple-100 ring-1 ring-border/40 sm:h-36 sm:w-36"
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
              <div key={i} className="w-32 shrink-0 space-y-2 sm:w-36">
                <div className="h-32 w-32 rounded-md bg-muted sm:h-36 sm:w-36" />
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
    <Card className="overflow-hidden">
      <CardContent className="pt-4">
        <SectionLabel
          icon={Zap}
          hint={`${items.length} most recent · freshest first`}
        >
          Recently Analyzed
        </SectionLabel>
        <div className="overflow-x-auto pb-2 [scrollbar-width:thin]">
          <div className="flex gap-3">
            {items.map((item: RecentEnrichment) => (
              <RecentTrackTile key={item.catalog_id} item={item} />
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Library Snapshot ─────────────────────────────────────────────────

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
      hint: uniqueGenres > 0 ? "cleaned + deduped" : undefined,
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
        <SectionLabel icon={Database}>Library Snapshot</SectionLabel>
        <dl className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
          {rows.map((r) => (
            <div
              key={r.label}
              className="flex items-baseline justify-between gap-4 border-b border-border/40 py-2 last:border-b-0 sm:last:border-b"
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
    <div className="min-w-0 space-y-6">
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
          <div className="grid gap-4 md:grid-cols-2">
            <SkeletonBlock />
            <SkeletonBlock />
          </div>
          <SkeletonBlock />
          <div className="grid gap-4 md:grid-cols-2">
            <SkeletonBlock />
            <SkeletonBlock />
          </div>
          <SkeletonBlock />
          <SkeletonBlock />
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

          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="min-w-0">
              <GenreDNA genreVector={profile.genre_vector} />
            </div>
            <div className="min-w-0">
              <TopArtistsCard artists={profile.top_artists} />
            </div>
          </div>

          <SoundSpaceCard />

          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="min-w-0">
              <TempoProfileCard />
            </div>
            <div className="min-w-0">
              <KeyModeCard />
            </div>
          </div>

          <RecentlyAnalyzedStrip />

          <LibrarySnapshot profile={profile} />
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
