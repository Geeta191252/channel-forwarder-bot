import { FileText, ArrowRight, CheckCircle2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

interface LogEntry {
  id: string;
  fileName: string;
  timestamp: string;
  status: "success" | "pending" | "failed";
}

interface ActivityLogProps {
  logs: LogEntry[];
}

export function ActivityLog({ logs }: ActivityLogProps) {
  return (
    <Card className="bg-gradient-card border-border/50 shadow-card animate-slide-up" style={{ animationDelay: "0.2s" }}>
      <CardHeader className="pb-4">
        <CardTitle className="text-lg flex items-center gap-2">
          <FileText className="h-5 w-5 text-primary" />
          Recent Activity
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[300px] pr-4">
          {logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center py-8">
              <div className="p-4 rounded-full bg-secondary/50 mb-4">
                <FileText className="h-8 w-8 text-muted-foreground" />
              </div>
              <p className="text-muted-foreground text-sm">No files forwarded yet</p>
              <p className="text-muted-foreground/60 text-xs mt-1">
                Activity will appear here once files are forwarded
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {logs.map((log) => (
                <div
                  key={log.id}
                  className="flex items-center gap-3 p-3 rounded-lg bg-secondary/30 border border-border/30 hover:border-border/50 transition-colors"
                >
                  <div className="p-2 rounded-lg bg-primary/10">
                    <FileText className="h-4 w-4 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground truncate">
                      {log.fileName}
                    </p>
                    <p className="text-xs text-muted-foreground">{log.timestamp}</p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <ArrowRight className="h-3 w-3 text-muted-foreground" />
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
