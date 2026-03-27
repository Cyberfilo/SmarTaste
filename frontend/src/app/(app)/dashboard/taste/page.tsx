"use client";

/**
 * Taste profile page.
 * Three sections stacked vertically: Genres, Artists, Audio Traits.
 */

import { TasteGenres } from "@/components/dashboard/taste-genres";
import { TasteArtists } from "@/components/dashboard/taste-artists";
import { TasteAudioTraits } from "@/components/dashboard/taste-audio-traits";

export default function TasteProfilePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          Taste Profile
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Your musical DNA based on listening data
        </p>
      </div>

      <TasteGenres />
      <TasteArtists />
      <TasteAudioTraits />
    </div>
  );
}
