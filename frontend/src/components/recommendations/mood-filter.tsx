"use client";

import { cn } from "@/lib/utils";

const moods = [
  { value: null, label: "All" },
  { value: "workout", label: "Workout" },
  { value: "chill", label: "Chill" },
  { value: "focus", label: "Focus" },
  { value: "party", label: "Party" },
  { value: "sad", label: "Sad" },
  { value: "driving", label: "Driving" },
] as const;

interface MoodFilterProps {
  value: string | null;
  onChange: (mood: string | null) => void;
}

export function MoodFilter({ value, onChange }: MoodFilterProps) {
  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Mood
      </label>
      {/* Horizontal scrollable on mobile, wrapping on md: */}
      <div className="flex gap-2 overflow-x-auto pb-1 md:flex-wrap md:overflow-x-visible">
        {moods.map((m) => (
          <button
            key={m.value ?? "all"}
            onClick={() => onChange(m.value)}
            className={cn(
              "shrink-0 rounded-full px-4 py-1.5 text-sm font-medium transition-colors border",
              value === m.value
                ? "bg-primary/15 text-primary border-primary/30"
                : "bg-card text-muted-foreground border-border hover:text-foreground hover:border-foreground/20"
            )}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}
