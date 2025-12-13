import { useState } from "react";
import { Rocket, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "sonner";

interface BulkForwardProps {
  sourceChannel: string;
  destChannel: string;
}

export function BulkForward({ sourceChannel, destChannel }: BulkForwardProps) {
  const [startId, setStartId] = useState("");
  const [endId, setEndId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<{ success: number; failed: number; skipped: number; total: number } | null>(null);

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

        <Button
          onClick={handleBulkForward}
          disabled={isLoading || !sourceChannel || !destChannel}
          className="w-full bg-primary hover:bg-primary/90"
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

        {result && (
          <div className="rounded-lg border border-border/50 bg-background/50 p-4 space-y-2">
            <div className="flex items-center gap-2">
              {result.failed === 0 ? (
                <CheckCircle className="h-5 w-5 text-success" />
              ) : (
                <AlertCircle className="h-5 w-5 text-warning" />
              )}
              <span className="font-medium">Results</span>
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