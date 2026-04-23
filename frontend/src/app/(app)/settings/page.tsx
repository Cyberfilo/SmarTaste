"use client";

import { useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { ServiceConnections } from "@/components/settings/service-connections";
import { CalibrationManager } from "@/components/settings/calibration-manager";
import { useCalibrationStatus } from "@/hooks/use-calibration";

export default function SettingsPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { data: calibrationStatus } = useCalibrationStatus();

  // Handle OAuth callbacks (Spotify redirects back here with query params)
  useEffect(() => {
    const service = searchParams.get("service");
    const connStatus = searchParams.get("status");
    const detail = searchParams.get("detail");
    if (service && connStatus === "connected") {
      toast.success(`${service.charAt(0).toUpperCase() + service.slice(1)} connected!`, {
        description: "Calibrate your taste for better recommendations.",
        action: calibrationStatus && !calibrationStatus.completed
          ? { label: "Calibrate", onClick: () => router.push("/onboarding") }
          : undefined,
      });
    } else if (service && connStatus === "error") {
      toast.error(`Failed to connect ${service}`, {
        description: detail || "Please try again.",
      });
    }
    if (service && connStatus) {
      window.history.replaceState({}, "", "/settings");
    }
  }, [searchParams, calibrationStatus, router]);

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage your connected music services
        </p>
      </div>
      <ServiceConnections />
      <CalibrationManager />
    </div>
  );
}
