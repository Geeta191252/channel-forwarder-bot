import { Activity, Zap, FileText, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface StatusCardProps {
  status: "online" | "offline" | "configuring";
  filesForwarded: number;
  lastActivity: string | null;
}

export function StatusCard({ status, filesForwarded, lastActivity }: StatusCardProps) {
  const statusConfig = {
    online: {
      color: "text-success",
      bg: "bg-success/10",
      label: "Online",
      pulse: true,
    },
    offline: {
      color: "text-muted-foreground",
      bg: "bg-muted/50",
      label: "Offline",
      pulse: false,
    },
    configuring: {
      color: "text-warning",
      bg: "bg-warning/10",
      label: "Configuring",
      pulse: true,
    },
  };

  const config = statusConfig[status];

  return (
    <div className="grid gap-4 md:grid-cols-3 animate-slide-up" style={{ animationDelay: "0.1s" }}>
      <Card className="bg-gradient-card border-border/50 shadow-card">
        <CardContent className="p-6">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Bot Status</p>
              <div className="flex items-center gap-2">
                <span className={`relative flex h-2.5 w-2.5`}>
                  {config.pulse && (
                    <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${config.bg} opacity-75`} />
                  )}
                  <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${config.bg}`} />
                </span>
                <span className={`font-semibold ${config.color}`}>{config.label}</span>
              </div>
            </div>
            <div className={`p-3 rounded-xl ${config.bg}`}>
              <Zap className={`h-5 w-5 ${config.color}`} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="bg-gradient-card border-border/50 shadow-card">
        <CardContent className="p-6">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Files Forwarded</p>
              <p className="text-2xl font-bold text-foreground">{filesForwarded}</p>
            </div>
            <div className="p-3 rounded-xl bg-primary/10">
              <FileText className="h-5 w-5 text-primary" />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="bg-gradient-card border-border/50 shadow-card">
        <CardContent className="p-6">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">Last Activity</p>
              <p className="text-sm font-medium text-foreground">
                {lastActivity || "No activity yet"}
              </p>
            </div>
            <div className="p-3 rounded-xl bg-secondary">
              <Clock className="h-5 w-5 text-muted-foreground" />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
