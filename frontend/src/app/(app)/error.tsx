"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("App error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[50vh] items-center justify-center p-4">
      <Card className="max-w-md w-full">
        <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
          <AlertTriangle className="h-10 w-10 text-amber-400" />
          <div>
            <p className="text-lg font-medium">Something went wrong</p>
            <p className="text-sm text-muted-foreground mt-1">
              {error.message || "An unexpected error occurred."}
            </p>
          </div>
          <Button onClick={reset} variant="outline" size="sm">
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Try Again
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
