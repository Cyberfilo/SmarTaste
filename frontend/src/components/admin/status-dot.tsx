import { cn } from "@/lib/utils";

export function StatusDot({
  ok,
  label,
  className,
}: {
  ok: boolean | null;
  label?: string;
  className?: string;
}) {
  const color =
    ok === true
      ? "bg-emerald-500"
      : ok === false
        ? "bg-red-500"
        : "bg-muted";
  return (
    <span
      title={label}
      aria-label={label}
      className={cn("inline-block h-2.5 w-2.5 rounded-full", color, className)}
    />
  );
}
