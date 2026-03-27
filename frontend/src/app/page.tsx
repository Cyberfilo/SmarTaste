"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";

/**
 * Root page: redirects to /dashboard if authenticated, /login otherwise.
 */
export default function Home() {
  const router = useRouter();
  const { checkAuth } = useAuthStore();

  useEffect(() => {
    checkAuth().then((user) => {
      if (user) {
        router.replace("/dashboard");
      } else {
        router.replace("/login");
      }
    });
  }, [checkAuth, router]);

  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
  );
}
