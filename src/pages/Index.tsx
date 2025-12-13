import { useState, useEffect } from "react";
import { Send, Bot, Sparkles } from "lucide-react";
import { BotConfig } from "@/components/BotConfig";
import { StatusCard } from "@/components/StatusCard";
import { ActivityLog } from "@/components/ActivityLog";
import { BulkForward } from "@/components/BulkForward";
import { supabase } from "@/integrations/supabase/client";

const Index = () => {
  const [isConfigured, setIsConfigured] = useState(false);
  const [config, setConfig] = useState<{ sourceChannel: string; destChannel: string } | null>(null);
  const [filesForwarded, setFilesForwarded] = useState(0);
  const [lastActivity, setLastActivity] = useState<string | null>(null);
  const [logs, setLogs] = useState<Array<{
    id: string;
    fileName: string;
    timestamp: string;
    status: "success" | "pending" | "failed";
  }>>([]);

  // Fetch forwarded files count from database
  const fetchForwardedCount = async () => {
    const { count, error } = await supabase
      .from('forwarded_messages')
      .select('*', { count: 'exact', head: true });
    
    if (!error && count !== null) {
      setFilesForwarded(count);
    }

    // Get last activity
    const { data } = await supabase
      .from('forwarded_messages')
      .select('forwarded_at')
      .order('forwarded_at', { ascending: false })
      .limit(1);
    
    if (data && data.length > 0) {
      const date = new Date(data[0].forwarded_at);
      setLastActivity(date.toLocaleString());
    }
  };

  useEffect(() => {
    fetchForwardedCount();
    
    // Refresh count every 5 seconds
    const interval = setInterval(fetchForwardedCount, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleConfigSaved = (newConfig: { sourceChannel: string; destChannel: string }) => {
    setConfig(newConfig);
    setIsConfigured(true);
  };

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      {/* Background glow effect */}
      <div className="absolute inset-0 bg-gradient-glow pointer-events-none" />
      
      {/* Grid pattern */}
      <div 
        className="absolute inset-0 opacity-[0.02]"
        style={{
          backgroundImage: `linear-gradient(hsl(var(--border)) 1px, transparent 1px),
                           linear-gradient(90deg, hsl(var(--border)) 1px, transparent 1px)`,
          backgroundSize: '50px 50px'
        }}
      />

      <div className="relative z-10 container max-w-5xl py-8 px-4 md:py-12">
        {/* Header */}
        <header className="text-center mb-12 animate-slide-up">
          <div className="inline-flex items-center justify-center p-3 rounded-2xl bg-primary/10 mb-6 shadow-glow">
            <Bot className="h-10 w-10 text-primary" />
          </div>
          
          <h1 className="text-4xl md:text-5xl font-bold mb-4">
            <span className="text-gradient-telegram">Telegram</span>{" "}
            <span className="text-foreground">File Forwarder</span>
          </h1>
          
          <p className="text-muted-foreground text-lg max-w-xl mx-auto flex items-center justify-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            Auto-forward files from one channel to another
            <Send className="h-4 w-4 text-primary" />
          </p>
        </header>

        {/* Status Cards */}
        <div className="mb-8">
          <StatusCard
            status={isConfigured ? "online" : "configuring"}
            filesForwarded={filesForwarded}
            lastActivity={lastActivity}
          />
        </div>

        {/* Main Content Grid */}
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="space-y-8">
            <BotConfig onConfigSaved={handleConfigSaved} />
            {config && (
              <BulkForward 
                sourceChannel={config.sourceChannel} 
                destChannel={config.destChannel} 
              />
            )}
          </div>
          <ActivityLog logs={logs} />
        </div>

        {/* Footer */}
        <footer className="mt-12 text-center text-sm text-muted-foreground/60">
          <p>Powered by Telegram Bot API</p>
        </footer>
      </div>
    </div>
  );
};

export default Index;
