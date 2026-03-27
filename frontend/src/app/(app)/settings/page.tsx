"use client";

import { ServiceConnections } from "@/components/settings/service-connections";
import { BYOKKeyManager } from "@/components/settings/byok-key-manager";

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage your music services and AI configuration
        </p>
      </div>
      <ServiceConnections />
      <BYOKKeyManager />
    </div>
  );
}
