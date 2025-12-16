import { useState } from "react";
import { Settings, Send, ArrowRight, Check, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import { supabase } from "@/integrations/supabase/client";

interface BotConfigProps {
  onConfigSaved: (config: { sourceChannel: string; destChannel: string }) => void;
}

export function BotConfig({ onConfigSaved }: BotConfigProps) {
  const [sourceChannel, setSourceChannel] = useState("");
  const [destChannel, setDestChannel] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isConfigured, setIsConfigured] = useState(false);
  const { toast } = useToast();

  const handleSaveConfig = async () => {
    if (!sourceChannel || !destChannel) {
      toast({
        title: "Error",
        description: "Please enter both source and destination channel IDs",
        variant: "destructive",
      });
      return;
    }

    setIsLoading(true);
    
    try {
      const { data, error } = await supabase.functions.invoke("telegram-forwarder", {
        body: { 
          action: "configure",
          sourceChannel,
          destChannel 
        },
      });

      if (error) throw error;

      // Auto-set webhook
      const webhookUrl = `https://wqspxhsjujakaldaxhvm.supabase.co/functions/v1/telegram-forwarder`;
      const webhookResult = await supabase.functions.invoke("telegram-forwarder", {
        body: { action: "set-webhook", webhookUrl },
      });

      setIsConfigured(true);
      onConfigSaved({ sourceChannel, destChannel });
      
      toast({
        title: "Configuration Saved",
        description: webhookResult.data?.ok 
          ? "Bot configured and webhook set successfully!" 
          : "Config saved. Webhook may need manual setup.",
      });
    } catch (error) {
      console.error("Error saving config:", error);
      toast({
        title: "Configuration Error",
        description: "Failed to save configuration. Check your channel IDs.",
        variant: "destructive",
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Card className="border-border/50 shadow-lg">
      <CardHeader className="pb-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-primary/10 text-primary">
            <Settings className="h-5 w-5" />
          </div>
          <div>
            <CardTitle className="text-xl">Bot Configuration</CardTitle>
            <CardDescription>Set up your forwarding channels</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-6 md:grid-cols-3 items-end">
          <div className="space-y-2">
            <Label htmlFor="source" className="text-sm font-medium text-muted-foreground">
              Source Channel ID
            </Label>
            <Input
              id="source"
              placeholder="e.g., -1001234567890"
              value={sourceChannel}
              onChange={(e) => setSourceChannel(e.target.value)}
              className="bg-secondary/50 border-border font-mono text-sm"
            />
          </div>

          <div className="flex justify-center pb-2">
            <ArrowRight className="h-5 w-5 text-primary" />
          </div>

          <div className="space-y-2">
            <Label htmlFor="dest" className="text-sm font-medium text-muted-foreground">
              Destination Channel ID
            </Label>
            <Input
              id="dest"
              placeholder="e.g., -1009876543210"
              value={destChannel}
              onChange={(e) => setDestChannel(e.target.value)}
              className="bg-secondary/50 border-border font-mono text-sm"
            />
          </div>
        </div>

        <div className="flex items-center gap-3 p-3 rounded-lg bg-secondary/30 border border-border/50">
          <AlertCircle className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          <p className="text-xs text-muted-foreground">
            Add your bot as admin to both channels. Use channel IDs starting with -100
          </p>
        </div>

        <Button
          onClick={handleSaveConfig}
          disabled={isLoading || !sourceChannel || !destChannel}
          className="w-full"
        >
          {isLoading ? (
            <span className="flex items-center gap-2">
              <div className="h-4 w-4 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
              Configuring...
            </span>
          ) : isConfigured ? (
            <span className="flex items-center gap-2">
              <Check className="h-4 w-4" />
              Configured
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <Send className="h-4 w-4" />
              Save Configuration
            </span>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}
