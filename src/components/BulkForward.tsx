import { useState, useEffect, useRef } from "react";
import { Rocket, Loader2, CheckCircle, AlertCircle, StopCircle, Zap, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "sonner";

interface BulkForwardProps {
  sourceChannel: string;
  destChannel: string;
}

interface LiveProgress {
  isRunning: boolean;
  success: number;
  failed: number;
  skipped: number;
  total: number;
  rateLimitHits: number;
  currentBatch: number;
  totalBatches: number;
  elapsedSeconds: number;
  speedPerMinute: number;
}

interface FinalResult {
  success: number;
  failed: number;
  skipped: number;
  total: number;
  stopped?: boolean;
  rateLimitHits?: number;
}

export function BulkForward({ sourceChannel, destChannel }: BulkForwardProps) {
  const [startId, setStartId] = useState("");
  const [endId, setEndId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<FinalResult | null>(null);
  const [liveProgress, setLiveProgress] = useState<LiveProgress | null>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // Poll for progress while loading
  useEffect(() => {
    if (isLoading) {
      const pollProgress = async () => {
        try {
          const { data } = await supabase.functions.invoke('telegram-forwarder', {
            body: { action: 'progress' },
          });
          if (data) {
            setLiveProgress(data);
          }
        } catch (error) {
          console.error('Progress poll error:', error);
        }
      };

      // Poll every 500ms
      pollingRef.current = setInterval(pollProgress, 500);
      pollProgress(); // Initial poll

      return () => {
        if (pollingRef.current) {
          clearInterval(pollingRef.current);
        }
      };
    } else {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    }
  }, [isLoading]);

  const handleStop = async () => {
    try {
      await supabase.functions.invoke('telegram-forwarder', {
        body: { action: 'stop' },
      });
      toast.info("Stop signal sent - forwarding will stop after current batch");
    } catch (error) {
      console.error('Stop error:', error);
      toast.error("Failed to send stop signal");
    }
  };

  const handleBulkForward = async () => {
    if (!startId || !endId) {
      toast.error("Please enter start and end message IDs");
      return;
    }

    const start = parseInt(startId);
    const end = parseInt(endId);

    if (isNaN(start) || isNaN(end)) {
      toast.error("Invalid message IDs");
      return;
    }

    if (start > end) {
      toast.error("Start ID should be less than End ID");
      return;
    }

    const totalMessages = end - start + 1;
    toast.info(`Starting to forward ${totalMessages.toLocaleString()} messages...`);

    setIsLoading(true);
    setResult(null);
    setLiveProgress(null);

    try {
      const { data, error } = await supabase.functions.invoke('telegram-forwarder', {
        body: {
          action: 'bulk-forward',
          sourceChannel,
          destChannel,
          startMessageId: start,
          endMessageId: end,
        },
      });

      if (error) throw error;

      setResult(data);
      setLiveProgress(null);
      
      if (data.stopped) {
        toast.info("Forwarding stopped by user");
      }
      
      if (data.skipped > 0) {
        toast.info(`${data.skipped} files skipped (already forwarded)`);
      }
      
      if (data.success > 0) {
        toast.success(`${data.success} files forwarded successfully!`);
      }
      
      if (data.failed > 0) {
        toast.warning(`${data.failed} files failed to forward`);
      }
    } catch (error) {
      console.error('Bulk forward error:', error);
      toast.error("Failed to bulk forward");
    } finally {
      setIsLoading(false);
    }
  };

  const progressPercent = liveProgress && liveProgress.total > 0 
    ? Math.round((liveProgress.success + liveProgress.failed) / liveProgress.total * 100)
    : 0;

  return (
    <Card className="bg-gradient-card border-border/50 shadow-card animate-slide-up" style={{ animationDelay: "0.3s" }}>
      <CardHeader className="pb-4">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Rocket className="h-5 w-5 text-primary" />
          Bulk Forward
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Forward existing files from channel history (100 files per batch)
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="startId" className="text-sm text-muted-foreground">
              Start Message ID
            </Label>
            <Input
              id="startId"
              type="number"
              placeholder="1"
              value={startId}
              onChange={(e) => setStartId(e.target.value)}
              className="bg-background/50 border-border/50"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="endId" className="text-sm text-muted-foreground">
              End Message ID
            </Label>
            <Input
              id="endId"
              type="number"
              placeholder="3000"
              value={endId}
              onChange={(e) => setEndId(e.target.value)}
              className="bg-background/50 border-border/50"
            />
          </div>
        </div>

        <p className="text-xs text-muted-foreground">
          💡 Message ID kaise nikale: Channel mein message ka link copy karo, last number = message ID
        </p>

        <div className="flex gap-2">
          <Button
            onClick={handleBulkForward}
            disabled={isLoading || !sourceChannel || !destChannel}
            className="flex-1 bg-primary hover:bg-primary/90"
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Forwarding...
              </>
            ) : (
              <>
                <Rocket className="mr-2 h-4 w-4" />
                Start Bulk Forward
              </>
            )}
          </Button>
          
          {isLoading && (
            <Button
              onClick={handleStop}
              variant="destructive"
              className="px-4"
            >
              <StopCircle className="h-4 w-4" />
            </Button>
          )}
        </div>

        {/* Live Progress Panel */}
        {isLoading && liveProgress && (
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 space-y-3 animate-pulse-slow">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                Live Progress
              </span>
              <span className="text-sm text-muted-foreground">
                {progressPercent}%
              </span>
            </div>
            
            <Progress value={progressPercent} className="h-2" />
            
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="flex items-center gap-2">
                <Zap className="h-4 w-4 text-yellow-500" />
                <span className="text-muted-foreground">Speed:</span>
                <span className="font-bold text-primary">{liveProgress.speedPerMinute.toLocaleString()}/min</span>
              </div>
              
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-orange-500" />
                <span className="text-muted-foreground">Rate Limits:</span>
                <span className="font-bold text-orange-500">{liveProgress.rateLimitHits}</span>
              </div>
            </div>
            
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="text-center p-2 rounded bg-success/10">
                <p className="text-success font-bold text-lg">{liveProgress.success.toLocaleString()}</p>
                <p className="text-muted-foreground">Success</p>
              </div>
              <div className="text-center p-2 rounded bg-destructive/10">
                <p className="text-destructive font-bold text-lg">{liveProgress.failed.toLocaleString()}</p>
                <p className="text-muted-foreground">Failed</p>
              </div>
              <div className="text-center p-2 rounded bg-muted/30">
                <p className="text-foreground font-bold text-lg">{liveProgress.total.toLocaleString()}</p>
                <p className="text-muted-foreground">Total</p>
              </div>
            </div>
            
            <div className="text-xs text-muted-foreground text-center">
              Batch {liveProgress.currentBatch}/{liveProgress.totalBatches} • 
              Time: {Math.floor(liveProgress.elapsedSeconds / 60)}m {liveProgress.elapsedSeconds % 60}s
            </div>
          </div>
        )}

        {/* Final Results */}
        {result && !isLoading && (
          <div className="rounded-lg border border-border/50 bg-background/50 p-4 space-y-2">
            <div className="flex items-center gap-2">
              {result.failed === 0 ? (
                <CheckCircle className="h-5 w-5 text-success" />
              ) : (
                <AlertCircle className="h-5 w-5 text-warning" />
              )}
              <span className="font-medium">Final Results</span>
              {result.rateLimitHits !== undefined && result.rateLimitHits > 0 && (
                <span className="text-xs text-orange-500 ml-auto">
                  ⚠️ {result.rateLimitHits} rate limits hit
                </span>
              )}
            </div>
            <div className="grid grid-cols-4 gap-2 text-sm">
              <div className="text-center p-2 rounded bg-success/10">
                <p className="text-success font-bold">{result.success}</p>
                <p className="text-muted-foreground text-xs">Success</p>
              </div>
              <div className="text-center p-2 rounded bg-blue-500/10">
                <p className="text-blue-500 font-bold">{result.skipped}</p>
                <p className="text-muted-foreground text-xs">Skipped</p>
              </div>
              <div className="text-center p-2 rounded bg-destructive/10">
                <p className="text-destructive font-bold">{result.failed}</p>
                <p className="text-muted-foreground text-xs">Failed</p>
              </div>
              <div className="text-center p-2 rounded bg-primary/10">
                <p className="text-primary font-bold">{result.total}</p>
                <p className="text-muted-foreground text-xs">Total</p>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}