"use client";

import { useState } from "react";
import { Handshake, Network, User, Users } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useArtistsTable, type ArtistRow } from "@/hooks/use-admin-tables";

const PAGE_SIZES = [25, 50, 100];

export function ArtistsTableCard() {
  const [pageSize, setPageSize] = useState(25);
  const [offset, setOffset] = useState(0);

  const { data, isLoading, isFetching } = useArtistsTable(pageSize, offset);

  const total = data?.total ?? 0;
  const page = Math.floor(offset / pageSize) + 1;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = offset > 0;
  const canNext = offset + pageSize < total;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between border-b">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Users className="h-4 w-4 text-purple-400" />
          Artists
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
                    <th className="px-3 py-2 font-medium">Artist</th>
                    <th className="px-3 py-2 font-medium w-24">Source</th>
                    <th className="px-3 py-2 font-medium">User</th>
                    <th className="px-3 py-2 font-medium w-52">
                      Library / Discovered
                    </th>
                    <th className="px-3 py-2 font-medium text-right w-20">
                      Discovery %
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items ?? []).map((row) => (
                    <ArtistRowView key={row.artist_name} row={row} />
                  ))}
                  {data && data.items.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-3 py-8 text-center text-muted-foreground"
                      >
                        No artists on this page.
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

function ArtistRowView({ row }: { row: ArtistRow }) {
  return (
    <tr className="border-b border-border/50 hover:bg-muted/30 transition-colors">
      <td className="px-3 py-2 font-medium">{row.artist_name}</td>
      <td className="px-3 py-2">
        <SourceBadge source={row.source} />
      </td>
      <td className="px-3 py-2 text-muted-foreground truncate max-w-[200px]">
        {row.user || "—"}
      </td>
      <td className="px-3 py-2">
        <LibraryBar
          libraryCount={row.tracks_library}
          total={row.tracks_total}
        />
        <div className="text-[10px] text-muted-foreground tabular-nums mt-0.5">
          {row.tracks_library} lib / {row.tracks_discovered} disc
          <span className="opacity-60"> · {row.tracks_global} global</span>
        </div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {row.discovery_pct.toFixed(1)}%
      </td>
    </tr>
  );
}

function SourceBadge({ source }: { source: ArtistRow["source"] }) {
  const config = {
    library: {
      Icon: User,
      label: "library",
      color: "text-emerald-400",
      bg: "bg-emerald-500/15 border-emerald-500/30",
    },
    cobweb: {
      Icon: Network,
      label: "cobweb",
      color: "text-purple-400",
      bg: "bg-purple-500/15 border-purple-500/30",
    },
    feat: {
      Icon: Handshake,
      label: "feat",
      color: "text-amber-400",
      bg: "bg-amber-500/15 border-amber-500/30",
    },
  }[source];
  const { Icon, label, color, bg } = config;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ${bg} ${color}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

function LibraryBar({
  libraryCount,
  total,
}: {
  libraryCount: number;
  total: number;
}) {
  if (total === 0) {
    return <div className="h-1.5 w-full rounded bg-muted" />;
  }
  const libPct = Math.min(100, (libraryCount / total) * 100);
  return (
    <div className="flex h-1.5 w-full overflow-hidden rounded bg-muted">
      <div className="bg-emerald-500" style={{ width: `${libPct}%` }} />
      <div
        className="bg-amber-500/60"
        style={{ width: `${Math.max(0, 100 - libPct)}%` }}
      />
    </div>
  );
}
