import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Shield, UserPlus, Trash2, Info, Copy, CheckCircle } from "lucide-react";
import { toast } from "sonner";

export const ForceJoin = () => {
  const [copied, setCopied] = useState<string | null>(null);

  const commands = [
    {
      id: "setforcejoin",
      name: "Set Force Join",
      command: "/setforcejoin @channel|Channel Name|https://t.me/+invite",
      description: "Group में Force Join enable करें। Users को channel join करना होगा।",
      icon: UserPlus,
      color: "text-green-500",
    },
    {
      id: "removeforcejoin",
      name: "Remove Force Join",
      command: "/removeforcejoin",
      description: "Group से Force Join disable करें।",
      icon: Trash2,
      color: "text-red-500",
    },
    {
      id: "forcejoininfo",
      name: "Force Join Info",
      command: "/forcejoininfo",
      description: "Current Force Join status देखें।",
      icon: Info,
      color: "text-blue-500",
    },
  ];

  const copyCommand = (command: string, id: string) => {
    navigator.clipboard.writeText(command);
    setCopied(id);
    toast.success("Command copied!");
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <Card className="border-primary/20">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Force Join Settings</CardTitle>
        </div>
        <CardDescription>
          Group में message भेजने से पहले users को channel join करना होगा
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {commands.map((cmd) => {
          const Icon = cmd.icon;
          return (
            <div
              key={cmd.id}
              className="flex items-start gap-3 p-3 rounded-lg bg-muted/50 border border-border"
            >
              <div className={`mt-0.5 ${cmd.color}`}>
                <Icon className="h-5 w-5" />
              </div>
              <div className="flex-1 min-w-0">
                <h4 className="font-medium text-foreground">{cmd.name}</h4>
                <p className="text-sm text-muted-foreground mb-2">{cmd.description}</p>
                <div className="flex items-center gap-2">
                  <code className="text-xs bg-background px-2 py-1 rounded border border-border flex-1 overflow-x-auto">
                    {cmd.command}
                  </code>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => copyCommand(cmd.command, cmd.id)}
                    className="shrink-0"
                  >
                    {copied === cmd.id ? (
                      <CheckCircle className="h-4 w-4 text-green-500" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>
            </div>
          );
        })}

        <div className="mt-4 p-3 rounded-lg bg-primary/5 border border-primary/20">
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <Info className="h-4 w-4 text-primary" />
            How to use
          </h4>
          <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside">
            <li>Bot को group में admin बनाएं (Delete messages permission)</li>
            <li>Bot को channel में admin बनाएं</li>
            <li><code className="bg-background px-1 rounded">/setforcejoin</code> command use करें</li>
            <li>Non-members के messages auto-delete होंगे</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
};
