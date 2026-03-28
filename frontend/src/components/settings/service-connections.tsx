"use client";

import { useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
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
import { Loader2, Unplug } from "lucide-react";
import { toast } from "sonner";
import {
  useServices,
  useSpotifyConnect,
  useAppleMusicDeveloperToken,
  useAppleMusicConnect,
  useDisconnectService,
  type ServiceConnection,
} from "@/hooks/use-services";

// ── MusicKit JS loader ──────────────────────────────────

declare global {
  interface Window {
    MusicKit: {
      configure: (config: {
        developerToken: string;
        app: { name: string; build: string };
      }) => typeof window.MusicKit;
      getInstance: () => {
        authorize: () => Promise<string>;
        isAuthorized: boolean;
        musicUserToken: string;
      };
    };
  }
}

function loadMusicKitJS(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (typeof window !== "undefined" && window.MusicKit) {
      resolve();
      return;
    }
    const script = document.createElement("script");
    script.src = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";
    script.async = true;
    script.crossOrigin = "anonymous";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load MusicKit JS"));
    document.head.appendChild(script);
  });
}

function statusBadge(status: string) {
  switch (status) {
    case "connected":
      return <Badge variant="outline" className="bg-emerald-500/15 text-emerald-400 border-emerald-500/30">Connected</Badge>;
    case "expired":
      return <Badge variant="outline" className="bg-amber-500/15 text-amber-400 border-amber-500/30">Expired</Badge>;
    default:
      return <Badge variant="outline" className="text-muted-foreground">Not Connected</Badge>;
  }
}

interface ServiceRowProps {
  service: ServiceConnection | undefined;
  serviceName: string;
  serviceKey: string;
  icon: React.ReactNode;
  onConnect: () => void;
  onDisconnect: (service: string) => void;
  isConnecting: boolean;
}

function ServiceRow({
  service,
  serviceName,
  serviceKey,
  icon,
  onConnect,
  onDisconnect,
  isConnecting,
}: ServiceRowProps) {
  const [showDisconnect, setShowDisconnect] = useState(false);
  const isConnected = service?.status === "connected";

  return (
    <>
      <div className="flex items-center gap-4">
        {/* Service icon */}
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted text-lg">
          {icon}
        </div>

        {/* Service info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium">{serviceName}</h4>
            {statusBadge(service?.status || "not_connected")}
          </div>
          {isConnected && service?.connected_at && (
            <p className="text-xs text-muted-foreground mt-0.5">
              Connected {new Date(service.connected_at).toLocaleDateString()}
            </p>
          )}
        </div>

        {/* Action button */}
        {isConnected ? (
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setShowDisconnect(true)}
            className="shrink-0"
          >
            <Unplug className="h-3.5 w-3.5 mr-1.5" />
            Disconnect
          </Button>
        ) : (
          <Button
            variant="default"
            size="sm"
            onClick={onConnect}
            disabled={isConnecting}
            className="shrink-0"
          >
            {isConnecting && <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />}
            Connect
          </Button>
        )}
      </div>

      {/* Disconnect confirmation dialog */}
      <AlertDialog open={showDisconnect} onOpenChange={setShowDisconnect}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Disconnect {serviceName}?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove your {serviceName} connection. You can reconnect at any time,
              but your listening data may need to be re-imported.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => onDisconnect(serviceKey)}
              className="bg-destructive/10 text-destructive hover:bg-destructive/20"
            >
              Disconnect
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function ServiceConnections() {
  const { data, isLoading } = useServices();
  const spotifyConnect = useSpotifyConnect();
  const appleMusicToken = useAppleMusicDeveloperToken();
  const appleMusicConnect = useAppleMusicConnect();
  const disconnectService = useDisconnectService();
  const [appleMusicLoading, setAppleMusicLoading] = useState(false);

  const spotify = data?.services.find((s) => s.service === "spotify");
  const appleMusic = data?.services.find((s) => s.service === "apple_music");

  function handleSpotifyConnect() {
    spotifyConnect.mutate(undefined, {
      onSuccess: (res) => {
        window.location.href = res.authorize_url;
      },
      onError: (err) => {
        toast.error(err.message || "Failed to connect Spotify");
      },
    });
  }

  const handleAppleMusicConnect = useCallback(async () => {
    setAppleMusicLoading(true);
    try {
      // 1. Get developer token from backend
      const tokenRes = await new Promise<{ developer_token: string }>(
        (resolve, reject) => {
          appleMusicToken.mutate(undefined, {
            onSuccess: resolve,
            onError: reject,
          });
        }
      );

      // 2. Load MusicKit JS
      toast.info("Loading Apple Music...");
      await loadMusicKitJS();

      // 3. Configure MusicKit with our developer token
      window.MusicKit.configure({
        developerToken: tokenRes.developer_token,
        app: { name: "MusicMind", build: "1.0.0" },
      });

      // 4. Authorize — opens Apple's sign-in popup
      const music = window.MusicKit.getInstance();
      const musicUserToken = await music.authorize();

      if (!musicUserToken) {
        toast.error("Apple Music authorization was cancelled");
        return;
      }

      // 5. Send the user token to our backend
      appleMusicConnect.mutate(musicUserToken, {
        onSuccess: () => {
          toast.success("Apple Music connected!");
        },
        onError: (err) => {
          toast.error(err.message || "Failed to store Apple Music connection");
        },
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to connect Apple Music";
      toast.error(message);
    } finally {
      setAppleMusicLoading(false);
    }
  }, [appleMusicToken, appleMusicConnect]);

  function handleDisconnect(service: string) {
    disconnectService.mutate(service, {
      onSuccess: () => {
        toast.success(`Service disconnected`);
      },
      onError: (err) => {
        toast.error(err.message || "Failed to disconnect service");
      },
    });
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Music Services</CardTitle>
          <CardDescription>Connect your music accounts</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4 animate-pulse">
            <div className="flex items-center gap-4">
              <div className="h-10 w-10 rounded-lg bg-muted" />
              <div className="flex-1 space-y-1">
                <div className="h-4 w-24 rounded bg-muted" />
                <div className="h-3 w-16 rounded bg-muted" />
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="h-10 w-10 rounded-lg bg-muted" />
              <div className="flex-1 space-y-1">
                <div className="h-4 w-28 rounded bg-muted" />
                <div className="h-3 w-16 rounded bg-muted" />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Music Services</CardTitle>
        <CardDescription>
          Connect your streaming accounts to build your taste profile
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Spotify */}
        <ServiceRow
          service={spotify}
          serviceName="Spotify"
          serviceKey="spotify"
          icon={
            <svg viewBox="0 0 24 24" className="h-5 w-5 fill-[#1DB954]">
              <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z" />
            </svg>
          }
          onConnect={handleSpotifyConnect}
          onDisconnect={handleDisconnect}
          isConnecting={spotifyConnect.isPending}
        />

        <Separator />

        {/* Apple Music */}
        <ServiceRow
          service={appleMusic}
          serviceName="Apple Music"
          serviceKey="apple_music"
          icon={
            <svg viewBox="0 0 24 24" className="h-5 w-5 fill-[#FA243C]">
              <path d="M23.994 6.124a9.23 9.23 0 00-.24-2.19c-.317-1.31-1.062-2.31-2.18-3.043a5.022 5.022 0 00-1.877-.726 10.496 10.496 0 00-1.564-.15c-.04-.003-.083-.01-.124-.013H5.986c-.152.01-.303.017-.455.026-.747.043-1.49.123-2.193.4-1.336.53-2.3 1.452-2.865 2.78-.192.448-.292.925-.363 1.408-.056.392-.088.785-.1 1.18 0 .032-.007.062-.01.093v12.223c.01.14.017.283.027.424.05.815.154 1.624.497 2.373.65 1.42 1.738 2.353 3.234 2.802.42.127.856.187 1.293.228.555.053 1.11.06 1.667.06h11.03c.525-.015 1.05-.04 1.573-.104.91-.11 1.77-.374 2.517-.964a5.1 5.1 0 001.537-1.963c.212-.504.338-1.03.394-1.573.052-.493.076-.988.08-1.483V6.124zM17.7 13.29c-.004.06-.007.13-.012.19-.072.97-.25 1.91-.7 2.78-.37.72-.86 1.3-1.59 1.65-.46.22-.95.33-1.47.33-.17 0-.34-.01-.52-.04-.54-.09-1.02-.3-1.48-.56-.87-.47-1.67-1.04-2.51-1.55-.14-.08-.28-.15-.42-.23v-4.43c.02-.02.04-.03.07-.04 1.14-.69 2.3-1.34 3.54-1.82.57-.22 1.14-.38 1.75-.42.61-.03 1.17.1 1.67.44.56.38.88.9 1.01 1.55.09.42.12.85.13 1.28.01.28.01.56 0 .84z" />
            </svg>
          }
          onConnect={handleAppleMusicConnect}
          onDisconnect={handleDisconnect}
          isConnecting={appleMusicLoading}
        />
      </CardContent>
    </Card>
  );
}
