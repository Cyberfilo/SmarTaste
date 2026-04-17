"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Music } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusDot } from "./status-dot";
import { useSongsTable, type SongRow } from "@/hooks/use-admin-tables";

const PAGE_SIZES = [25, 50, 100];

export function SongsTableCard() {
  const [pageSize, setPageSize] = useState(25);
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const { data, isLoading, isFetching } = useSongsTable(pageSize, offset);

  const total = data?.total ?? 0;
  const page = Math.floor(offset / pageSize) + 1;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = offset > 0;
  const canNext = offset + pageSize < total;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between border-b">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Music className="h-4 w-4 text-purple-400" />
          Songs
          <span className="ml-1 text-xs font-normal text-muted-foreground">
            ({total.toLocaleString()})
          </span>
        </CardTitle>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <label className="flex items-center gap-1.5">
            Rows:
            <select
              className="rounded bg-muted px-1.5 py-0.5"
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setOffset(0);
              }}
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <span className="tabular-nums">
            {page} / {pages}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!canPrev}
            onClick={() => setOffset(Math.max(0, offset - pageSize))}
          >
            Prev
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!canNext}
            onClick={() => setOffset(offset + pageSize)}
          >
            Next
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="p-4 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : (
          <div
            className={
              isFetching ? "opacity-60 transition-opacity" : "transition-opacity"
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="w-8" />
                    <th className="px-3 py-2 font-medium">Artwork</th>
                    <th className="px-3 py-2 font-medium">Title / Artist</th>
                    {["Essentia", "CLAP", "MERT", "ISRC", "Audio", "User"].map(
                      (h) => (
                        <th
                          key={h}
                          className="px-2 py-2 font-medium text-center"
                        >
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((row) => (
                    <SongRowView
                      key={row.catalog_id}
                      row={row}
                      expanded={expanded === row.catalog_id}
                      onToggle={() =>
                        setExpanded(
                          expanded === row.catalog_id ? null : row.catalog_id,
                        )
                      }
                    />
                  ))}
                  {data && data.items.length === 0 && (
                    <tr>
                      <td
                        colSpan={9}
                        className="px-3 py-8 text-center text-muted-foreground"
                      >
                        No songs on this page.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SongRowView({
  row,
  expanded,
  onToggle,
}: {
  row: SongRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        <td className="px-2 text-muted-foreground">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </td>
        <td className="px-3 py-2">
          {row.artwork_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={row.artwork_url}
              alt=""
              className="h-10 w-10 rounded object-cover"
            />
          ) : (
            <div className="h-10 w-10 rounded bg-muted" />
          )}
        </td>
        <td className="px-3 py-2 min-w-0">
          <div className="font-medium truncate max-w-[240px]">
            {row.name || "(untitled)"}
          </div>
          <div className="text-muted-foreground truncate max-w-[240px]">
            {row.artist_name}
          </div>
        </td>
        <td className="px-2 text-center">
          <StatusDot ok={row.has_essentia} label="Essentia features present" />
        </td>
        <td className="px-2 text-center">
          <StatusDot ok={row.has_clap} label="CLAP embedding present" />
        </td>
        <td className="px-2 text-center">
          <StatusDot ok={row.has_mert} label="MERT embedding present" />
        </td>
        <td className="px-2 text-center">
          <StatusDot ok={row.isrc_ok} label="ISRC present + valid" />
        </td>
        <td className="px-2 text-center">
          <StatusDot
            ok={row.has_cached_audio}
            label="Cached audio bytes available"
          />
        </td>
        <td className="px-2 text-center">
          <StatusDot ok={row.user_linked} label="User-linked (in library)" />
        </td>
      </tr>
      {expanded && <SongDetailsRow row={row} />}
    </>
  );
}

function SongDetailsRow({ row }: { row: SongRow }) {
  return (
    <tr className="border-b border-border/50 bg-muted/10">
      <td colSpan={9} className="px-4 py-3">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 text-[11px] tabular-nums">
          <Field label="Catalog ID" value={row.catalog_id} mono />
          <Field label="User-linked" value={row.user_linked ? "yes" : "no"} />
          <Field label="ISRC valid" value={row.isrc_ok ? "yes" : "no"} />
          <Field
            label="Essentia features"
            value={row.has_essentia ? "yes" : "missing"}
          />
          <Field
            label="CLAP / MERT"
            value={`${row.has_clap ? "✓" : "–"} / ${row.has_mert ? "✓" : "–"}`}
          />
          <Field
            label="Cached audio"
            value={row.has_cached_audio ? "yes" : "no"}
          />
        </div>
      </td>
    </tr>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="text-muted-foreground text-[10px] uppercase tracking-wide">
        {label}
      </div>
      <div className={mono ? "font-mono break-all" : ""}>{value}</div>
    </div>
  );
}
