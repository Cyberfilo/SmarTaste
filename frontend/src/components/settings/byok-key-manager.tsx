"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Key,
  Loader2,
  Check,
  X,
  Trash2,
  RefreshCw,
  DollarSign,
  Info,
} from "lucide-react";
import { toast } from "sonner";
import {
  useKeyStatus,
  useStoreKey,
  useValidateKey,
  useDeleteKey,
  useCostEstimate,
} from "@/hooks/use-claude-key";

export function BYOKKeyManager() {
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [showUpdateInput, setShowUpdateInput] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  const { data: keyStatus, isLoading: statusLoading } = useKeyStatus();
  const storeKey = useStoreKey();
  const validateKey = useValidateKey();
  const deleteKey = useDeleteKey();
  const { data: costEstimate } = useCostEstimate();

  function handleSaveKey() {
    if (!apiKeyInput.trim()) {
      toast.error("Please enter an API key");
      return;
    }
    storeKey.mutate(apiKeyInput.trim(), {
      onSuccess: () => {
        toast.success("API key saved");
        setApiKeyInput("");
        setShowUpdateInput(false);
      },
      onError: (err) => {
        toast.error(err.message || "Failed to save API key");
      },
    });
  }

  function handleValidate() {
    validateKey.mutate(undefined, {
      onSuccess: (res) => {
        if (res.valid) {
          toast.success("API key is valid");
        } else {
          toast.error(res.error || "API key is invalid");
        }
      },
      onError: (err) => {
        toast.error(err.message || "Failed to validate API key");
      },
    });
  }

  function handleDelete() {
    deleteKey.mutate(undefined, {
      onSuccess: () => {
        toast.success("API key removed");
        setShowDeleteDialog(false);
      },
      onError: (err) => {
        toast.error(err.message || "Failed to remove API key");
      },
    });
  }

  if (statusLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>AI Assistant (Claude)</CardTitle>
          <CardDescription>Manage your Anthropic API key</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-1/3" />
        </CardContent>
      </Card>
    );
  }

  const isConfigured = keyStatus?.configured ?? false;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Key className="h-5 w-5 text-primary" />
          AI Assistant (Claude)
        </CardTitle>
        <CardDescription>
          Bring your own Anthropic API key to use the AI chat assistant
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isConfigured && !showUpdateInput ? (
          /* Key is configured -- show status and actions */
          <>
            <div className="flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm text-muted-foreground">Current key</p>
                <p className="text-sm font-mono mt-0.5">
                  {keyStatus?.masked_key || "sk-ant-..."}
                </p>
              </div>
              <Badge variant="outline" className="bg-emerald-500/15 text-emerald-400 border-emerald-500/30">
                <Check className="h-3 w-3 mr-1" />
                Configured
              </Badge>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleValidate}
                disabled={validateKey.isPending}
              >
                {validateKey.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
                )}
                Validate
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowUpdateInput(true)}
              >
                Update Key
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setShowDeleteDialog(true)}
              >
                <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                Remove
              </Button>
            </div>
          </>
        ) : (
          /* No key configured (or updating) -- show input */
          <>
            {showUpdateInput && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                <Info className="h-3 w-3" />
                Enter a new key to replace the current one
              </div>
            )}
            <div className="flex gap-2">
              <Input
                type="password"
                placeholder="sk-ant-..."
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveKey()}
                className="flex-1"
              />
              <Button
                onClick={handleSaveKey}
                disabled={storeKey.isPending || !apiKeyInput.trim()}
              >
                {storeKey.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                ) : null}
                Save Key
              </Button>
              {showUpdateInput && (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => {
                    setShowUpdateInput(false);
                    setApiKeyInput("");
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              )}
            </div>
            {!isConfigured && (
              <p className="text-xs text-muted-foreground">
                Get your API key from{" "}
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  console.anthropic.com
                </a>
              </p>
            )}
          </>
        )}

        {/* Cost estimate section */}
        {costEstimate && (
          <>
            <Separator />
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <DollarSign className="h-3.5 w-3.5" />
                Cost Estimate
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <span className="text-muted-foreground">Model</span>
                <span className="font-medium">{costEstimate.model}</span>
                <span className="text-muted-foreground">Est. per message</span>
                <span className="font-medium">{costEstimate.estimated_cost_per_message}</span>
                <span className="text-muted-foreground">Input tokens</span>
                <span className="font-mono text-muted-foreground">{costEstimate.input_token_price}</span>
                <span className="text-muted-foreground">Output tokens</span>
                <span className="font-mono text-muted-foreground">{costEstimate.output_token_price}</span>
              </div>
              <p className="text-[10px] text-muted-foreground/70 pt-1">
                You pay Anthropic directly. MusicMind does not charge anything.
              </p>
            </div>
          </>
        )}

        {/* Delete confirmation dialog */}
        <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Remove API Key?</AlertDialogTitle>
              <AlertDialogDescription>
                This will delete your stored Anthropic API key. The AI chat assistant
                will be unavailable until you add a new key.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleDelete}
                className="bg-destructive/10 text-destructive hover:bg-destructive/20"
              >
                Remove Key
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  );
}
