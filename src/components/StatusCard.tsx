import { Bot, FileText, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface StatusCardProps {
  status: "online" | "offline" | "configuring";
  filesForwarded: number;
  lastActivity: string | null;
}

export function StatusCard({ status, filesForwarded, lastActivity }: StatusCardProps) {
  const statusConfig = {
    online: { color: "text-green-500", bg: "bg-green-500/10", label: "Online" },
    offline: { color: "text-red-500", bg: "bg-red-500/10", label: "Offline" },
    configuring: { color: "text-yellow-500", bg: "bg-yellow-500/10", label: "Configuring" },
  };

  const config = statusConfig[status];

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card className="border-border/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${config.bg}`}>
              <Bot className={`h-5 w-5 ${config.color}`} />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Status</p>
              <p className={`font-semibold ${config.color}`}>{config.label}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-primary/10">
              <FileText className="h-5 w-5 text-primary" />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Files Forwarded</p>
              <p className="font-semibold text-foreground">{filesForwarded.toLocaleString()}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-secondary">
              <Clock className="h-5 w-5 text-muted-foreground" />
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Last Activity</p>
              <p className="font-semibold text-foreground text-sm">
                {lastActivity || "No activity yet"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
