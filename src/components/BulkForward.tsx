import { useState, useEffect } from "react";
import { Play, Square, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/hooks/use-toast";
import { supabase } from "@/integrations/supabase/client";

interface BulkForwardProps {
  sourceChannel: string;
  destChannel: string;
}

interface ProgressData {
  success_count: number;
  failed_count: number;
  skipped_count: number;
  total_count: number;
  current_batch: number;
  total_batches: number;
  rate_limit_hits: number;
  speed: number;
  is_active: boolean;
}

export function BulkForward({ sourceChannel, destChannel }: BulkForwardProps) {
  const [startId, setStartId] = useState("");
  const [endId, setEndId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState<ProgressData | null>(null);
  const { toast } = useToast();

  // Fetch progress once on mount (supports auto-refresh even after reload)
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const { data } = await supabase.functions.invoke("telegram-forwarder", {
        body: { action: "progress" },
      });
      if (cancelled) return;
      if (data) {
        setProgress(data);
        if (data.is_active) setIsLoading(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Poll for progress while running
  useEffect(() => {
    if (!isLoading) return;

    const interval = setInterval(async () => {
      const { data } = await supabase.functions.invoke("telegram-forwarder", {
        body: { action: "progress" },
      });
      if (data) {
        setProgress(data);
        if (!data.is_active) {
          setIsLoading(false);
        }
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [isLoading]);

  const handleStart = async () => {
    const start = parseInt(startId);
    const end = parseInt(endId);

    if (isNaN(start) || isNaN(end) || start > end) {
      toast({
        title: "Invalid IDs",
        description: "Please enter valid start and end message IDs",
        variant: "destructive",
      });
      return;
    }

    setIsLoading(true);
    setProgress(null);

    try {
      const { error } = await supabase.functions.invoke("telegram-forwarder", {
        body: {
          action: "bulk-forward",
          startMessageId: start,
          endMessageId: end,
        },
      });

      if (error) throw error;

      toast({
        title: "Forwarding Started",
        description: "Progress will auto-refresh until completion.",
      });
    } catch (error) {
      console.error("Error:", error);
      setIsLoading(false);
      toast({
        title: "Error",
        description: "Forwarding failed. Check console for details.",
        variant: "destructive",
      });
    }
  };

  const handleStop = async () => {
    await supabase.functions.invoke("telegram-forwarder", {
      body: { action: "stop" },
    });
    toast({ title: "Stop Requested", description: "Will stop after current batch" });
  };

  const progressPercent = progress?.total_count 
    ? Math.round((progress.success_count / progress.total_count) * 100) 
    : 0;

  return (
    <Card className="border-border/50 shadow-lg">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Play className="h-5 w-5 text-primary" />
          Bulk Forward
        </CardTitle>
        <CardDescription>Forward a range of messages</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Start Message ID</Label>
            <Input
              type="number"
              placeholder="1"
              value={startId}
              onChange={(e) => setStartId(e.target.value)}
              disabled={isLoading}
            />
          </div>
          <div className="space-y-2">
            <Label>End Message ID</Label>
            <Input
              type="number"
              placeholder="1000"
              value={endId}
              onChange={(e) => setEndId(e.target.value)}
              disabled={isLoading}
            />
          </div>
        </div>

        {progress && (
          <div className="space-y-3 p-4 rounded-lg bg-secondary/30">
            <div className="flex justify-between text-sm">
              <span>Progress</span>
              <span>{progressPercent}%</span>
            </div>
            <Progress value={progressPercent} />
            <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
              <div>✅ Success: {progress.success_count}</div>
              <div>❌ Failed: {progress.failed_count}</div>
              <div>⏭️ Skipped: {progress.skipped_count}</div>
              <div>⚡ Speed: {progress.speed}/min</div>
              <div>📦 Batch: {progress.current_batch}/{progress.total_batches}</div>
              <div>🔄 Rate limits: {progress.rate_limit_hits}</div>
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <Button
            onClick={handleStart}
            disabled={isLoading || !startId || !endId}
            className="flex-1"
          >
            {isLoading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Forwarding...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Start Forward
              </>
            )}
          </Button>
          {isLoading && (
            <Button variant="destructive" onClick={handleStop}>
              <Square className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
