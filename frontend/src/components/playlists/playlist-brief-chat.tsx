"use client";

/**
 * V 6.450 — per-playlist chat-driven suggestions.
 *
 * Flow:
 *   1. User types a freeform brief ("Italian rap for a long drive,
 *      reflective, similar to Sfera but darker").
 *   2. Optionally mentions reference songs — a debounced autocomplete
 *      under the textarea returns matches from /api/search/songs with
 *      enrichment. Each selected song becomes an artwork "character"
 *      chip above the textarea. Per-chip role + "why I like this" text.
 *   3. Submit → POST /brief. Backend synthesizes a target_vector via
 *      gpt-5.4 and echoes it back.
 *   4. Suggestions load from /recommendations?brief_id=<id> in the
 *      panel below — each item has an Add-to-Playlist stub button.
 *
 * The MusicKit JS wiring for the Add-to-Playlist action lands in 6b.
 */

import { useEffect, useState } from "react";
import {
  CircleX,
  Loader2,
  Music,
  Plus,
  Search,
  Send,
  Sparkles,
} from "lucide-react";
import Image from "next/image";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  useAddTrackToPlaylist,
  useCreateBrief,
  usePlaylistRecommendations,
  useSongAutocomplete,
} from "@/hooks/use-playlists";
import type {
  BriefMentionedSong,
  BriefTargetVector,
  SongAutocompleteItem,
} from "@/types/api";

// ── Local types ──────────────────────────────────────────────

type Role = BriefMentionedSong["role"];

interface Mention {
  song: SongAutocompleteItem;
  role: Role;
  reason: string;
  showReason: boolean;
}

interface Props {
  playlistId: string;
  service: string;
}

// V 6.470 — "+ Apple" button states per suggestion catalog_id.
type AddState = "idle" | "adding" | "added" | "failed";

// ── Helpers ──────────────────────────────────────────────────

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

function enrichmentLine(item: SongAutocompleteItem): string {
  const e = item.enrichment;
  const parts: string[] = [];
  if (e.mood_tags?.length) parts.push(e.mood_tags.slice(0, 2).join(" · "));
  if (e.tempo) parts.push(`${Math.round(e.tempo)} bpm`);
  if (e.energy != null) parts.push(`energy ${e.energy.toFixed(2)}`);
  if (item.genre_names?.length) parts.push(item.genre_names[0]);
  return parts.join("  ·  ");
}

// ── Main component ───────────────────────────────────────────

export function PlaylistBriefChat({ playlistId, service }: Props) {
  const [briefText, setBriefText] = useState("");
  const [mentions, setMentions] = useState<Mention[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [submittedBriefId, setSubmittedBriefId] = useState<string | null>(null);
  const [submittedTarget, setSubmittedTarget] =
    useState<BriefTargetVector | null>(null);

  const debouncedQuery = useDebounced(searchQuery, 200);
  const { data: searchData, isFetching: searchFetching } = useSongAutocomplete(
    debouncedQuery,
    { limit: 8 },
  );

  const createBrief = useCreateBrief();
  const canSubmit =
    briefText.trim().length > 0 && !createBrief.isPending;

  const suggestions = usePlaylistRecommendations(playlistId, service, {
    briefId: submittedBriefId,
    enabled: submittedBriefId !== null,
    limit: 20,
  });

  function addMention(song: SongAutocompleteItem) {
    if (mentions.some((m) => m.song.catalog_id === song.catalog_id)) {
      return; // already mentioned
    }
    setMentions((prev) => [
      ...prev,
      { song, role: "referenced", reason: "", showReason: false },
    ]);
    setSearchQuery("");
    setSearchOpen(false);
  }

  function removeMention(idx: number) {
    setMentions((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateMention(idx: number, patch: Partial<Mention>) {
    setMentions((prev) =>
      prev.map((m, i) => (i === idx ? { ...m, ...patch } : m)),
    );
  }

  async function submitBrief() {
    if (!canSubmit) return;
    try {
      const result = await createBrief.mutateAsync({
        playlistId,
        service,
        briefText: briefText.trim(),
        mentionedSongs: mentions.map((m) => ({
          catalog_id: m.song.catalog_id,
          isrc: m.song.isrc ?? null,
          role: m.role,
          reason_text: m.reason.trim() || null,
        })),
      });
      setSubmittedBriefId(result.id);
      setSubmittedTarget(result.target_vector);
    } catch {
      // error surfaced in createBrief.error
    }
  }

  function reset() {
    setBriefText("");
    setMentions([]);
    setSubmittedBriefId(null);
    setSubmittedTarget(null);
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-purple-400" />
        <h2 className="text-sm font-semibold">Chat-driven suggestions</h2>
        {submittedBriefId && (
          <button
            onClick={reset}
            className="ml-auto text-xs text-muted-foreground underline hover:text-foreground"
          >
            Start over
          </button>
        )}
      </div>

      {/* Mentioned songs row — the "characters" */}
      {mentions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {mentions.map((m, idx) => (
            <MentionChip
              key={m.song.catalog_id}
              mention={m}
              onRemove={() => removeMention(idx)}
              onRoleChange={(role) => updateMention(idx, { role })}
              onToggleReason={() =>
                updateMention(idx, { showReason: !m.showReason })
              }
              onReasonChange={(reason) => updateMention(idx, { reason })}
            />
          ))}
        </div>
      )}

      {/* Brief textarea */}
      <Card>
        <CardContent className="space-y-3 pt-4">
          <textarea
            value={briefText}
            onChange={(e) => setBriefText(e.target.value)}
            placeholder="What kind of vibe are you building? e.g. 'Italian rap for a long drive, reflective, similar to Sfera but a bit darker.'"
            rows={3}
            className="w-full resize-none rounded-xl border border-border bg-zinc-900/40 px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-purple-500/50 focus:outline-none focus:ring-1 focus:ring-purple-500/30"
            disabled={submittedBriefId !== null}
          />

          {/* Song mention search row */}
          {!submittedBriefId && (
            <div className="space-y-2">
              {!searchOpen ? (
                <button
                  onClick={() => setSearchOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-purple-500/40 hover:text-foreground"
                >
                  <Plus className="h-3 w-3" />
                  Mention a reference song
                </button>
              ) : (
                <div className="space-y-2">
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      autoFocus
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Type a song or artist name…"
                      className="pl-9"
                    />
                    <button
                      onClick={() => {
                        setSearchOpen(false);
                        setSearchQuery("");
                      }}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground"
                      aria-label="Close search"
                    >
                      <CircleX className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {searchQuery.trim().length > 0 && (
                    <SearchResults
                      items={searchData?.results ?? []}
                      loading={searchFetching}
                      onSelect={addMention}
                      alreadyMentioned={new Set(
                        mentions.map((m) => m.song.catalog_id),
                      )}
                    />
                  )}
                </div>
              )}
            </div>
          )}

          {/* Submit / status */}
          {!submittedBriefId ? (
            <div className="flex items-center justify-end gap-2">
              {createBrief.isError && (
                <p className="text-xs text-red-400">
                  Failed to synthesize target. Try again.
                </p>
              )}
              <Button
                onClick={submitBrief}
                disabled={!canSubmit}
                className="gap-2"
                size="sm"
              >
                {createBrief.isPending ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Synthesizing…
                  </>
                ) : (
                  <>
                    <Send className="h-3.5 w-3.5" />
                    Get suggestions
                  </>
                )}
              </Button>
            </div>
          ) : (
            <TargetSummary target={submittedTarget} />
          )}
        </CardContent>
      </Card>

      {/* Suggestions */}
      {submittedBriefId && (
        <SuggestionsList
          loading={suggestions.isLoading}
          data={suggestions.data}
          service={service}
          playlistId={playlistId}
        />
      )}
    </div>
  );
}

// ── Subcomponents ────────────────────────────────────────────

function MentionChip({
  mention,
  onRemove,
  onRoleChange,
  onToggleReason,
  onReasonChange,
}: {
  mention: Mention;
  onRemove: () => void;
  onRoleChange: (r: Role) => void;
  onToggleReason: () => void;
  onReasonChange: (v: string) => void;
}) {
  const { song } = mention;
  return (
    <div className="flex w-full max-w-sm flex-col gap-1.5 rounded-xl border border-border bg-zinc-900/40 p-2">
      <div className="flex items-center gap-2">
        {song.artwork_url ? (
          <Image
            src={song.artwork_url}
            alt={`${song.name} cover`}
            width={36}
            height={36}
            className="h-9 w-9 shrink-0 rounded object-cover"
            unoptimized
          />
        ) : (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-muted">
            <Music className="h-4 w-4 text-muted-foreground" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium">{song.name}</p>
          <p className="truncate text-[11px] text-muted-foreground">
            {song.artist_name}
          </p>
        </div>
        <button
          onClick={onRemove}
          className="shrink-0 text-muted-foreground/60 hover:text-foreground"
          aria-label="Remove mention"
        >
          <CircleX className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex items-center gap-1.5 text-[10px]">
        <RoleToggle value={mention.role} onChange={onRoleChange} />
        <button
          onClick={onToggleReason}
          className="rounded border border-border/60 px-1.5 py-0.5 text-muted-foreground transition-colors hover:border-purple-500/40 hover:text-foreground"
        >
          {mention.showReason ? "hide note" : "+ note"}
        </button>
      </div>
      {mention.showReason && (
        <Input
          value={mention.reason}
          onChange={(e) => onReasonChange(e.target.value)}
          placeholder="Why does this fit? (optional)"
          className="h-7 text-[11px]"
        />
      )}
    </div>
  );
}

const ROLES: Array<{ value: Role; label: string }> = [
  { value: "referenced", label: "like" },
  { value: "target_example", label: "target" },
  { value: "anti_example", label: "avoid" },
];

function RoleToggle({
  value,
  onChange,
}: {
  value: Role;
  onChange: (r: Role) => void;
}) {
  return (
    <div className="flex overflow-hidden rounded border border-border/60">
      {ROLES.map((r) => (
        <button
          key={r.value}
          onClick={() => onChange(r.value)}
          className={`px-1.5 py-0.5 transition-colors ${
            r.value === value
              ? "bg-purple-500/20 text-purple-300"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function SearchResults({
  items,
  loading,
  onSelect,
  alreadyMentioned,
}: {
  items: SongAutocompleteItem[];
  loading: boolean;
  onSelect: (song: SongAutocompleteItem) => void;
  alreadyMentioned: Set<string>;
}) {
  if (loading && items.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border bg-zinc-900/40 px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Searching…
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <p className="px-2 text-xs text-muted-foreground/60">
        No matches. Try a different query.
      </p>
    );
  }
  return (
    <div className="max-h-64 overflow-y-auto rounded-lg border border-border bg-zinc-900/40">
      {items.map((item) => {
        const picked = alreadyMentioned.has(item.catalog_id);
        return (
          <button
            key={item.catalog_id}
            disabled={picked}
            onClick={() => onSelect(item)}
            className="flex w-full items-center gap-2.5 border-b border-border/50 px-3 py-2 text-left transition-colors last:border-b-0 hover:bg-zinc-800/60 disabled:opacity-40"
          >
            {item.artwork_url ? (
              <Image
                src={item.artwork_url}
                alt=""
                width={32}
                height={32}
                className="h-8 w-8 shrink-0 rounded object-cover"
                unoptimized
              />
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-muted">
                <Music className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">{item.name}</p>
              <p className="truncate text-[10px] text-muted-foreground">
                {item.artist_name}
              </p>
              <p className="truncate text-[10px] text-muted-foreground/60">
                {enrichmentLine(item)}
              </p>
            </div>
            {picked && (
              <span className="shrink-0 text-[10px] text-purple-400">added</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function TargetSummary({ target }: { target: BriefTargetVector | null }) {
  if (!target) {
    return (
      <p className="text-xs text-muted-foreground">
        Target synthesized. Loading suggestions…
      </p>
    );
  }
  const chips: string[] = [];
  if (target.mood_tags?.length) chips.push(...target.mood_tags);
  if (target.tempo_target)
    chips.push(`~${Math.round(target.tempo_target)} bpm`);
  if (target.energy_target != null)
    chips.push(`energy ${target.energy_target.toFixed(2)}`);
  if (target.valence_target != null)
    chips.push(`valence ${target.valence_target.toFixed(2)}`);
  return (
    <div className="space-y-2 rounded-lg border border-purple-500/20 bg-purple-500/5 px-3 py-2">
      <p className="text-xs text-muted-foreground">
        <span className="font-medium text-purple-300">Target: </span>
        {target.summary}
      </p>
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <span
              key={c}
              className="rounded bg-purple-500/10 px-2 py-0.5 text-[10px] text-purple-300"
            >
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Suggestions ──────────────────────────────────────────────

function SuggestionsList({
  loading,
  data,
  service,
  playlistId,
}: {
  loading: boolean;
  data: ReturnType<typeof usePlaylistRecommendations>["data"];
  service: string;
  playlistId: string;
}) {
  if (loading) {
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="flex items-center gap-3 pt-3">
              <Skeleton className="h-10 w-10 rounded" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3.5 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }
  if (!data || data.items.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No suggestions came back — try a different brief or add more reference
        songs.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        {data.items.length} suggestions · playlist weight{" "}
        {data.blend ? Math.round(data.blend.playlist * 100) : 80}% / user{" "}
        {data.blend ? Math.round(data.blend.user * 100) : 20}%
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {data.items.map((item) => (
          <SuggestionCard
            key={item.catalog_id}
            item={item}
            service={service}
            playlistId={playlistId}
          />
        ))}
      </div>
    </div>
  );
}

function SuggestionCard({
  item,
  service,
  playlistId,
}: {
  item: NonNullable<
    ReturnType<typeof usePlaylistRecommendations>["data"]
  >["items"][number];
  service: string;
  playlistId: string;
}) {
  const [addState, setAddState] = useState<AddState>("idle");
  const addTrack = useAddTrackToPlaylist();
  const canAdd = service === "apple_music";

  async function handleAdd() {
    if (!canAdd || addState === "adding" || addState === "added") return;
    setAddState("adding");
    try {
      await addTrack.mutateAsync({
        playlistId,
        catalogId: item.catalog_id,
        service: "apple_music",
      });
      setAddState("added");
      toast.success("Added to playlist", {
        description: `${item.name} — ${item.artist_name}`,
      });
    } catch (err) {
      setAddState("failed");
      toast.error("Couldn't add to playlist", {
        description:
          err instanceof Error
            ? err.message.slice(0, 120)
            : "Apple Music rejected the request.",
      });
      // Allow retry after a moment
      setTimeout(() => setAddState("idle"), 3000);
    }
  }

  const addBtnLabel = {
    idle: "+ Apple",
    adding: "Adding…",
    added: "✓ Added",
    failed: "Retry",
  }[addState];

  return (
    <Card className="transition-colors hover:bg-muted/50">
      <CardContent className="flex items-center gap-3 pt-3">
        {item.artwork_url ? (
          <Image
            src={item.artwork_url}
            alt={`${item.name} cover`}
            width={40}
            height={40}
            className="h-10 w-10 shrink-0 rounded object-cover"
            unoptimized
          />
        ) : (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-muted">
            <Music className="h-4 w-4 text-muted-foreground" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{item.name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {item.artist_name}
          </p>
          {item.explanation && (
            <p className="line-clamp-1 text-[10px] text-muted-foreground/60">
              {item.explanation}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-xs font-medium tabular-nums text-purple-400">
            {Math.round(item.score * 100)}%
          </span>
          {canAdd ? (
            <Button
              size="sm"
              variant={addState === "added" ? "ghost" : "outline"}
              className={`h-6 px-2 text-[10px] ${
                addState === "added" ? "text-green-400" : ""
              }`}
              onClick={handleAdd}
              disabled={addState === "adding" || addState === "added"}
            >
              {addState === "adding" && (
                <Loader2 className="mr-1 h-2.5 w-2.5 animate-spin" />
              )}
              {addBtnLabel}
            </Button>
          ) : (
            <span className="text-[10px] text-muted-foreground/60">
              {item.service_source}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
