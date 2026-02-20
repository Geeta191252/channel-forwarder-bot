import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Bot, Forward, Megaphone, Search, Shield, AlertCircle, UserPlus, FolderOpen, Users, HelpCircle, Copy, CheckCircle } from "lucide-react";
import { toast } from "sonner";

const menuButtons = [
  { id: "forward", command: "/forward", label: "📮 Forward", icon: Forward, colSpan: 1 },
  { id: "channel", command: "/channel", label: "📢 Channel", icon: Megaphone, colSpan: 1 },
  { id: "filters", command: "/filters", label: "🔍 Filters", icon: Search, colSpan: 1 },
  { id: "moderation", command: "/moderation", label: "📛 Moderation", icon: Shield, colSpan: 1 },
  { id: "admin", command: "/admin", label: "🆘 @Admin", icon: AlertCircle, colSpan: 1 },
  { id: "joinrequest", command: "/joinrequest", label: "📬 Join Request", icon: UserPlus, colSpan: 1 },
  { id: "filelogo", command: "/filelogo", label: "📁 File Logo", icon: FolderOpen, colSpan: 1 },
  { id: "referral", command: "/referral", label: "👥 Referral", icon: Users, colSpan: 1 },
  { id: "help", command: "/help", label: "❓ Help", icon: HelpCircle, colSpan: 2 },
];

export const BotMenuButtons = () => {
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
          <Bot className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Bot Menu Commands</CardTitle>
        </div>
        <CardDescription>
          Bot ke main menu commands — button press karke copy karein
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2">
          {menuButtons.map((btn) => (
            <Button
              key={btn.id}
              variant="outline"
              className={`w-full justify-center gap-2 h-12 text-sm font-semibold bg-primary/5 border-primary/20 hover:bg-primary/10 ${
                btn.colSpan === 2 ? "col-span-2" : ""
              }`}
              onClick={() => copyCommand(btn.command, btn.id)}
            >
              <span className="truncate">{btn.label}</span>
              <span className="shrink-0">
                {copied === btn.id ? (
                  <CheckCircle className="h-4 w-4 text-green-500" />
                ) : (
                  <Copy className="h-4 w-4 opacity-60" />
                )}
              </span>
            </Button>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};
