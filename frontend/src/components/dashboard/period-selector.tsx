"use client";

/**
 * Period selector for listening stats.
 * Three buttons: Last Month, 6 Months, All Time.
 */

import type { Period } from "@/types/api";

const periods: { value: Period; label: string }[] = [
  { value: "month", label: "Last Month" },
  { value: "6months", label: "6 Months" },
  { value: "alltime", label: "All Time" },
];

interface PeriodSelectorProps {
  period: Period;
  onPeriodChange: (period: Period) => void;
}

export function PeriodSelector({ period, onPeriodChange }: PeriodSelectorProps) {
  return (
    <div className="flex w-full gap-1 rounded-lg bg-muted p-1 md:w-auto">
      {periods.map((p) => (
        <button
          key={p.value}
          onClick={() => onPeriodChange(p.value)}
          className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors md:flex-initial ${
            period === p.value
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}
