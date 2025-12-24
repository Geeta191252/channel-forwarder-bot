import { useState, useEffect } from "react";
import { Bot } from "lucide-react";
import { BotConfig } from "@/components/BotConfig";
import { StatusCard } from "@/components/StatusCard";
import { BulkForward } from "@/components/BulkForward";
import { ForceJoin } from "@/components/ForceJoin";
import { supabase } from "@/integrations/supabase/client";

const Index = () => {
  const [isConfigured, setIsConfigured] = useState(false);
  const [config, setConfig] = useState<{ sourceChannel: string; destChannel: string } | null>(null);
  const [filesForwarded, setFilesForwarded] = useState(0);
  const [lastActivity, setLastActivity] = useState<string | null>(null);

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

          <BotConfig onConfigSaved={handleConfigSaved} />

          {config && (
            <BulkForward
              sourceChannel={config.sourceChannel}
              destChannel={config.destChannel}
            />
          )}

          <ForceJoin />
        </div>

        <footer className="mt-8 text-center text-sm text-muted-foreground">
          <p>Powered by Telegram Bot API</p>
        </footer>
      </div>
    </div>
  );
};

export default Index;
