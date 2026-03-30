"use client";

/**
 * Playlists page — create, view, and manage AI-powered playlists.
 * Each playlist has an AI context that guides recommendation expansion.
 */

import { useState } from "react";
import {
  usePlaylists,
  usePlaylist,
  useCreatePlaylist,
  useDeletePlaylist,
  useRemoveTrack,
} from "@/hooks/use-playlists";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  ListMusic,
  Plus,
  Trash2,
  ArrowLeft,
  Music,
  Sparkles,
  X,
} from "lucide-react";

export default function PlaylistsPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  if (selectedId !== null) {
    return (
      <PlaylistDetail
        playlistId={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Playlists
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            AI-powered playlist creation and management
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          New Playlist
        </button>
      </div>

      {showCreate && (
        <CreatePlaylistForm
          onClose={() => setShowCreate(false)}
          onCreated={(id) => {
            setShowCreate(false);
            setSelectedId(id);
          }}
        />
      )}

      <PlaylistList onSelect={setSelectedId} />
    </div>
  );
}

function PlaylistList({ onSelect }: { onSelect: (id: number) => void }) {
  const { data, isLoading } = usePlaylists();
  const deleteMutation = useDeletePlaylist();

  if (isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="space-y-3 pt-4">
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-3 w-full" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (!data || data.playlists.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <ListMusic className="h-12 w-12 text-muted-foreground/40" />
          <div>
            <p className="text-lg font-medium">No playlists yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Create your first playlist with AI context for smarter
              recommendations.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.playlists.map((pl) => (
        <Card
          key={pl.id}
          className="cursor-pointer transition-colors hover:bg-muted/50"
          onClick={() => onSelect(pl.id)}
        >
          <CardContent className="pt-4">
            <div className="flex items-start justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="truncate text-sm font-semibold">{pl.name}</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {pl.track_count} track{pl.track_count !== 1 ? "s" : ""}
                </p>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm("Delete this playlist?")) {
                    deleteMutation.mutate(pl.id, {
                      onSuccess: () => toast.success("Playlist deleted"),
                    });
                  }
                }}
                className="ml-2 shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
            {pl.ai_context && (
              <div className="mt-2 flex items-start gap-1.5">
                <Sparkles className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400" />
                <p className="line-clamp-2 text-xs text-muted-foreground">
                  {pl.ai_context}
                </p>
              </div>
            )}
            {pl.description && !pl.ai_context && (
              <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">
                {pl.description}
              </p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function CreatePlaylistForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [aiContext, setAiContext] = useState("");
  const createMutation = useCreatePlaylist();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    createMutation.mutate(
      { name: name.trim(), description, ai_context: aiContext },
      {
        onSuccess: (data: unknown) => {
          toast.success("Playlist created");
          const result = data as { id: number };
          onCreated(result.id);
        },
        onError: () => toast.error("Failed to create playlist"),
      }
    );
  };

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold">New Playlist</h2>
          <button onClick={onClose} className="rounded p-1 hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Playlist"
              className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Description (optional)
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What's this playlist about?"
              className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
            />
          </div>
          <div>
            <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Sparkles className="h-3 w-3 text-emerald-400" />
              AI Context
            </label>
            <textarea
              value={aiContext}
              onChange={(e) => setAiContext(e.target.value)}
              placeholder="Describe the vibe for AI recommendations, e.g.: 'chill Italian rap for late night drives, 90-120 BPM, no mainstream hits'"
              rows={3}
              className="w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
            />
            <p className="mt-1 text-[10px] text-muted-foreground">
              This context helps AI understand what songs to suggest when
              expanding or improving this playlist.
            </p>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!name.trim() || createMutation.isPending}
              className="rounded-lg bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {createMutation.isPending ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function PlaylistDetail({
  playlistId,
  onBack,
}: {
  playlistId: number;
  onBack: () => void;
}) {
  const { data: playlist, isLoading } = usePlaylist(playlistId);
  const removeMutation = useRemoveTrack(playlistId);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-32" />
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-10 w-10 rounded" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (!playlist) {
    return (
      <div className="text-center">
        <p className="text-muted-foreground">Playlist not found</p>
        <button onClick={onBack} className="mt-2 text-sm text-primary">
          Go back
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <button
          onClick={onBack}
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to playlists
        </button>
        <h1 className="text-2xl font-bold tracking-tight">{playlist.name}</h1>
        {playlist.description && (
          <p className="mt-1 text-sm text-muted-foreground">
            {playlist.description}
          </p>
        )}
        {playlist.ai_context && (
          <div className="mt-2 flex items-start gap-2 rounded-lg bg-emerald-500/10 px-3 py-2">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
            <p className="text-xs text-emerald-300">{playlist.ai_context}</p>
          </div>
        )}
        <p className="mt-2 text-xs text-muted-foreground">
          {playlist.track_count} track{playlist.track_count !== 1 ? "s" : ""}
          {playlist.updated_at &&
            ` · Updated ${new Date(playlist.updated_at).toLocaleDateString()}`}
        </p>
      </div>

      {/* Track list */}
      {playlist.items.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Music className="h-10 w-10 text-muted-foreground/40" />
            <div>
              <p className="text-sm font-medium">No tracks yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Add tracks from recommendations or ask the AI chat to populate
                this playlist.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="divide-y divide-border pt-2">
            {playlist.items.map((item, i) => (
              <div
                key={item.id}
                className="flex items-center gap-3 py-2 first:pt-0 last:pb-0"
              >
                <span className="w-5 text-right text-xs font-medium text-muted-foreground tabular-nums">
                  {i + 1}
                </span>

                {item.artwork_url ? (
                  <img
                    src={item.artwork_url}
                    alt=""
                    className="h-9 w-9 shrink-0 rounded object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded bg-muted">
                    <Music className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                )}

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{item.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {item.artist_name}
                    {item.album_name ? ` · ${item.album_name}` : ""}
                  </p>
                </div>

                {item.added_by !== "user" && (
                  <span className="shrink-0 rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400">
                    AI
                  </span>
                )}

                <button
                  onClick={() =>
                    removeMutation.mutate(item.id, {
                      onSuccess: () => toast.success("Track removed"),
                    })
                  }
                  className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
