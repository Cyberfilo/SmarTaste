"use client";

/**
 * Model preference selector for choosing the default AI model.
 *
 * Stores the user's preference in localStorage under "musicmind-preferred-model".
 * The chat interface reads this to determine which model to use by default,
 * and allows per-conversation overrides.
 */

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { useKeyStatus } from "@/hooks/use-claude-key";
import { useOpenAIKeyStatus } from "@/hooks/use-openai-key";

// ── Model definitions ─────────────────────────────────

const STORAGE_KEY = "musicmind-preferred-model";

export interface ModelOption {
  id: string;
  name: string;
  provider: "claude" | "openai";
  description: string;
}

export const MODEL_OPTIONS: ModelOption[] = [
  {
    id: "claude",
    name: "Claude Sonnet 4",
    provider: "claude",
    description: "Anthropic's latest, strong at reasoning",
  },
  {
    id: "gpt-4o",
    name: "GPT-4o",
    provider: "openai",
    description: "OpenAI's flagship, fast and capable",
  },
  {
    id: "gpt-4.1",
    name: "GPT-4.1",
    provider: "openai",
    description: "OpenAI's newest, improved coding and instruction following",
  },
];

export function getStoredModel(): string {
  if (typeof window === "undefined") return "claude";
  return localStorage.getItem(STORAGE_KEY) || "claude";
}

export function setStoredModel(model: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, model);
}

// ── Component ─────────────────────────────────────────

export function ModelSelector() {
  const [selected, setSelected] = useState("claude");
  const { data: claudeKey } = useKeyStatus();
  const { data: openaiKey } = useOpenAIKeyStatus();

  // Initialize from localStorage on mount
  useEffect(() => {
    setSelected(getStoredModel());
  }, []);

  function handleSelect(modelId: string) {
    setSelected(modelId);
    setStoredModel(modelId);
    const model = MODEL_OPTIONS.find((m) => m.id === modelId);
    if (model) {
      toast.success(`Preferred model updated to ${model.name}`);
    }
  }

  // Determine which keys are configured
  const claudeConfigured = claudeKey?.configured ?? false;
  const openaiConfigured = openaiKey?.configured ?? false;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" />
          Preferred AI Model
        </CardTitle>
        <CardDescription>
          Choose which AI model to use for chat (can override per-conversation)
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {MODEL_OPTIONS.map((model) => {
          const isSelected = selected === model.id;
          const needsKey =
            (model.provider === "claude" && !claudeConfigured) ||
            (model.provider === "openai" && !openaiConfigured);

          return (
            <button
              key={model.id}
              onClick={() => handleSelect(model.id)}
              className={`flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                isSelected
                  ? "border-emerald-500/50 bg-emerald-500/5"
                  : "border-border bg-transparent hover:border-border/80 hover:bg-zinc-800/30"
              }`}
            >
              {/* Radio indicator */}
              <div className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-current">
                {isSelected && (
                  <div className="h-2 w-2 rounded-full bg-emerald-500" />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{model.name}</span>
                  {isSelected && needsKey && (
                    <span className="text-[10px] text-amber-400">
                      {model.provider === "claude"
                        ? "Needs Claude API key"
                        : "Needs OpenAI API key"}
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {model.description}
                </p>
              </div>
            </button>
          );
        })}
      </CardContent>
    </Card>
  );
}
