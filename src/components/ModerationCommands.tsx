import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Shield, ShieldOff, Ban, Link2Off, MessageSquareOff, Eye, AlertTriangle, RotateCcw, Copy, CheckCircle } from "lucide-react";
import { toast } from "sonner";

const commands = [
  { id: "blockforward", command: "/blockforward", label: "Block Forward", icon: Ban, color: "text-orange-500", description: "Block forwarded messages" },
  { id: "blocklinks", command: "/blocklinks", label: "Block Links", icon: Link2Off, color: "text-yellow-500", description: "Block links & usernames" },
  { id: "blockbadwords", command: "/blockbadwords", label: "Block Bad Words", icon: MessageSquareOff, color: "text-pink-500", description: "Block 18+ content" },
  { id: "modstatus", command: "/modstatus", label: "Mod Status", icon: Eye, color: "text-blue-500", description: "View mod settings" },
  { id: "warnings", command: "/warnings @username", label: "Warnings", icon: AlertTriangle, color: "text-amber-500", description: "Check user warnings" },
  { id: "resetwarnings", command: "/resetwarnings @username", label: "Reset Warnings", icon: RotateCcw, color: "text-purple-500", description: "Reset user warnings (admin)" },
];

export const ModerationCommands = () => {
  const [copied, setCopied] = useState<string | null>(null);

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
          <CardTitle className="text-lg">Moderation Commands</CardTitle>
        </div>
        <CardDescription>
          Group moderation ke liye commands — button press karke copy karein
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2">
          {commands.map((cmd) => {
            const Icon = cmd.icon;
            return (
              <Button
                key={cmd.id}
                variant="outline"
                className="w-full justify-start gap-2 h-12 text-sm bg-primary/5 border-primary/20 hover:bg-primary/10"
                onClick={() => copyCommand(cmd.command, cmd.id)}
              >
                <Icon className={`h-5 w-5 shrink-0 ${cmd.color}`} />
                <span className="font-medium truncate">{cmd.label}</span>
                <span className="ml-auto shrink-0">
                  {copied === cmd.id ? (
                    <CheckCircle className="h-4 w-4 text-green-500" />
                  ) : (
                    <Copy className="h-4 w-4 opacity-60" />
                  )}
                </span>
              </Button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
};
