"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkles, Mic2, ListMusic, Music, Pencil } from "lucide-react";
import {
  useCalibrationStatus,
  useCalibrationEntries,
  type CalibrationItem,
} from "@/hooks/use-calibration";

function groupEntries(items: CalibrationItem[]) {
  const topArtists: CalibrationItem[] = [];
  const rankedArtists: CalibrationItem[] = [];
  const playlists: CalibrationItem[] = [];
  const songs: CalibrationItem[] = [];

  for (const item of items) {
    switch (item.calibration_type) {
      case "top_artist":
        topArtists.push(item);
        break;
      case "artist_rank":
        rankedArtists.push(item);
        break;
      case "playlist":
        playlists.push(item);
        break;
      case "playlist_song":
        songs.push(item);
        break;
    }
  }

  return { topArtists, rankedArtists, playlists, songs };
}

export function CalibrationManager() {
  const { data: status, isLoading: statusLoading } = useCalibrationStatus();
  const { data: entriesData, isLoading: entriesLoading } = useCalibrationEntries();

  const isLoading = statusLoading || entriesLoading;

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Taste Calibration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 animate-pulse">
            <div className="h-4 w-40 rounded bg-muted" />
            <div className="h-4 w-32 rounded bg-muted" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!status?.completed) {
    return (
      <Card className="border-purple-500/20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-purple-400" />
            Taste Calibration
          </CardTitle>
          <CardDescription>
            Calibrate your taste for better recommendations
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/onboarding">
            <Button size="sm">
              <Sparkles className="h-3.5 w-3.5 mr-1.5" />
              Start Calibration
            </Button>
          </Link>
        </CardContent>
      </Card>
    );
  }

  const items = entriesData?.items || [];
  const { topArtists, rankedArtists, playlists, songs } = groupEntries(items);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-purple-400" />
              Taste Calibration
            </CardTitle>
            <CardDescription className="mt-1">
              Your selections influence recommendations
            </CardDescription>
          </div>
          <Link href="/onboarding">
            <Button variant="outline" size="sm">
              <Pencil className="h-3.5 w-3.5 mr-1.5" />
              Edit
            </Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Top Artists */}
        {topArtists.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Mic2 className="h-3.5 w-3.5 text-purple-400" />
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Top Artists
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {topArtists.map((a, i) => (
                <Badge
                  key={a.item_id}
                  variant="outline"
                  className="bg-purple-500/10 text-purple-300 border-purple-500/20"
                >
                  <span className="mr-1 text-[10px] font-bold text-purple-400">
                    #{i + 1}
                  </span>
                  {a.item_name}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Ranked Artists */}
        {rankedArtists.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Mic2 className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Other Artists ({rankedArtists.length})
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {rankedArtists.slice(0, 5).map((a) => a.item_name).join(", ")}
              {rankedArtists.length > 5 && ` +${rankedArtists.length - 5} more`}
            </p>
          </div>
        )}

        {/* Playlists */}
        {playlists.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <ListMusic className="h-3.5 w-3.5 text-purple-400" />
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Key Playlists
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {playlists.map((p) => (
                <Badge
                  key={p.item_id}
                  variant="outline"
                  className="text-muted-foreground"
                >
                  {p.item_name}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Songs */}
        {songs.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-2">
              <Music className="h-3.5 w-3.5 text-purple-400" />
              <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Favorite Songs ({songs.length})
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {songs.slice(0, 5).map((s) => s.item_name).join(", ")}
              {songs.length > 5 && ` +${songs.length - 5} more`}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
