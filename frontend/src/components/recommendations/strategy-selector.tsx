"use client";

import { cn } from "@/lib/utils";

const strategies = [
  { value: "auto", label: "Auto" },
  { value: "similar_artists", label: "Similar Artists" },
  { value: "genre_adjacent", label: "Genre Adjacent" },
  { value: "editorial", label: "Editorial" },
  { value: "charts", label: "Charts" },
] as const;

interface StrategySelectorProps {
  value: string;
  onChange: (strategy: string) => void;
}

export function StrategySelector({ value, onChange }: StrategySelectorProps) {
  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        Strategy
      </label>
      {/* Mobile: full-width select dropdown */}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="md:hidden h-9 w-full rounded-lg border border-input bg-card px-3 text-sm text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/50"
      >
        {strategies.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
      {/* Desktop: inline button group */}
      <div className="hidden md:flex gap-1 rounded-lg bg-muted p-1">
        {strategies.map((s) => (
          <button
            key={s.value}
            onClick={() => onChange(s.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              value === s.value
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-accent"
            )}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}
