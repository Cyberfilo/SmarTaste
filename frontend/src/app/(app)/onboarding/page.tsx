"use client";

import { CalibrationWizard } from "@/components/onboarding/calibration-wizard";

export default function OnboardingPage() {
  return (
    <div className="mx-auto max-w-2xl py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">
          Calibrate Your Taste
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Help us understand what you actually listen to. This takes less than a
          minute and makes your recommendations much better.
        </p>
      </div>
      <CalibrationWizard />
    </div>
  );
}
