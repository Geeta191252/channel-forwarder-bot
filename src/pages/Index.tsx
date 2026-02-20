import { useState, useEffect } from "react";
import { Bot, Settings } from "lucide-react";
import { BotConfig } from "@/components/BotConfig";
import { StatusCard } from "@/components/StatusCard";
import { BulkForward } from "@/components/BulkForward";
import { ForceJoin } from "@/components/ForceJoin";
import { AdminGroups } from "@/components/AdminGroups";
import { ModerationCommands } from "@/components/ModerationCommands";
import { BotMenuButtons } from "@/components/BotMenuButtons";
import { supabase } from "@/integrations/supabase/client";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const Index = () => {
  const [isConfigured, setIsConfigured] = useState(false);
  const [config, setConfig] = useState<{ sourceChannel: string; destChannel: string } | null>(null);
  const [filesForwarded, setFilesForwarded] = useState(0);
  const [lastActivity, setLastActivity] = useState<string | null>(null);
  const [botApiUrl, setBotApiUrl] = useState(() => {
    return localStorage.getItem("botApiUrl") || "";
  });

  const fetchStats = async () => {
    const { count } = await supabase
      .from('forwarded_messages')
      .select('*', { count: 'exact', head: true });
    
    if (count !== null) setFilesForwarded(count);

    const { data } = await supabase
      .from('forwarded_messages')
      .select('forwarded_at')
      .order('forwarded_at', { ascending: false })
      .limit(1);
    
    if (data?.[0]) {
      setLastActivity(new Date(data[0].forwarded_at).toLocaleString());
    }
  };

  useEffect(() => {
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    localStorage.setItem("botApiUrl", botApiUrl);
  }, [botApiUrl]);

  const handleConfigSaved = (newConfig: { sourceChannel: string; destChannel: string }) => {
    setConfig(newConfig);
    setIsConfigured(true);
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container max-w-4xl py-8 px-4">
        <header className="text-center mb-8">
          <div className="inline-flex items-center justify-center p-3 rounded-2xl bg-primary/10 mb-4">
            <Bot className="h-10 w-10 text-primary" />
          </div>
          <h1 className="text-3xl font-bold text-foreground mb-2">
            Telegram Forwarder Bot
          </h1>
          <p className="text-muted-foreground">
            Auto-forward messages between channels
          </p>
        </header>

        <div className="space-y-6">
          <StatusCard
            status={isConfigured ? "online" : "configuring"}
            filesForwarded={filesForwarded}
            lastActivity={lastActivity}
          />

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings className="h-5 w-5" />
                Bot API Settings
              </CardTitle>
              <CardDescription>
                Enter your Koyeb bot URL to connect
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                <Label htmlFor="botApiUrl">Bot API URL</Label>
                <Input
                  id="botApiUrl"
                  placeholder="https://your-bot.koyeb.app"
                  value={botApiUrl}
                  onChange={(e) => setBotApiUrl(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Example: https://my-telegram-bot-xyz.koyeb.app
                </p>
              </div>
            </CardContent>
          </Card>

          <BotConfig onConfigSaved={handleConfigSaved} />

          {config && (
            <BulkForward
              sourceChannel={config.sourceChannel}
              destChannel={config.destChannel}
            />
          )}

          <AdminGroups botApiUrl={botApiUrl} />

          <BotMenuButtons />
          <ForceJoin />
          <ModerationCommands />
        </div>

        <footer className="mt-8 text-center text-sm text-muted-foreground">
          <p>Powered by Telegram Bot API</p>
        </footer>
      </div>
    </div>
  );
};

export default Index;
