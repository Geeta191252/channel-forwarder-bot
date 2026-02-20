import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Bot, Forward, Megaphone, Search, Shield, ShieldOff, AlertCircle, UserPlus, FolderOpen, Users, HelpCircle, Copy, CheckCircle, Ban, Link2Off, MessageSquareOff, Eye, AlertTriangle, RotateCcw, ArrowLeft } from "lucide-react";
import { toast } from "sonner";

const menuButtons = [
  { id: "forward", command: "/forward", label: "📮 Forward", colSpan: 1 },
  { id: "channel", command: "/channel", label: "📢 Channel", colSpan: 1 },
  { id: "filters", command: "/filters", label: "🔍 Filters", colSpan: 1 },
  { id: "moderation", command: "/moderation", label: "📛 Moderation", colSpan: 1, hasSubmenu: true },
  { id: "admin", command: "/admin", label: "🆘 @Admin", colSpan: 1 },
  { id: "joinrequest", command: "/joinrequest", label: "📬 Join Request", colSpan: 1 },
  { id: "filelogo", command: "/filelogo", label: "📁 File Logo", colSpan: 1 },
  { id: "referral", command: "/referral", label: "👥 Referral", colSpan: 1 },
  { id: "help", command: "/help", label: "❓ Help", colSpan: 2 },
];

const moderationCommands = [
  { id: "enablemod", command: "/enablemod", label: "✅ Enable Mod", icon: Shield, color: "text-green-500" },
  { id: "disablemod", command: "/disablemod", label: "❌ Disable Mod", icon: ShieldOff, color: "text-red-500" },
  { id: "blockforward", command: "/blockforward", label: "🚫 Block Forward", icon: Ban, color: "text-orange-500" },
  { id: "blocklinks", command: "/blocklinks", label: "🔗 Block Links", icon: Link2Off, color: "text-yellow-500" },
  { id: "blockbadwords", command: "/blockbadwords", label: "🔞 Block Bad Words", icon: MessageSquareOff, color: "text-pink-500" },
  { id: "modstatus", command: "/modstatus", label: "👁 Mod Status", icon: Eye, color: "text-blue-500" },
  { id: "warnings", command: "/warnings @username", label: "⚠️ Warnings", icon: AlertTriangle, color: "text-amber-500" },
  { id: "resetwarnings", command: "/resetwarnings @username", label: "🔄 Reset Warnings", icon: RotateCcw, color: "text-purple-500" },
];

export const BotMenuButtons = () => {
  const [copied, setCopied] = useState<string | null>(null);
  const [view, setView] = useState<"main" | "moderation">("main");

  const copyCommand = (command: string, id: string) => {
    navigator.clipboard.writeText(command);
    setCopied(id);
    toast.success("Command copied!");
    setTimeout(() => setCopied(null), 2000);
  };

  const handleButtonClick = (btn: typeof menuButtons[0]) => {
    if (btn.hasSubmenu) {
      setView("moderation");
    } else {
      copyCommand(btn.command, btn.id);
    }
  };

  return (
    <Card className="border-primary/20">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Bot className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">
            {view === "main" ? "Bot Menu Commands" : "📛 Moderation Commands"}
          </CardTitle>
        </div>
        <CardDescription>
          {view === "main"
            ? "Bot ke main menu commands — button press karke copy karein"
            : "Group moderation ke liye commands — button press karke copy karein"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {view === "main" ? (
          <div className="grid grid-cols-2 gap-2">
            {menuButtons.map((btn) => (
              <Button
                key={btn.id}
                variant="outline"
                className={`w-full justify-center gap-2 h-12 text-sm font-semibold bg-primary/5 border-primary/20 hover:bg-primary/10 ${
                  btn.colSpan === 2 ? "col-span-2" : ""
                }`}
                onClick={() => handleButtonClick(btn)}
              >
                <span className="truncate">{btn.label}</span>
                {!btn.hasSubmenu && (
                  <span className="shrink-0">
                    {copied === btn.id ? (
                      <CheckCircle className="h-4 w-4 text-green-500" />
                    ) : (
                      <Copy className="h-4 w-4 opacity-60" />
                    )}
                  </span>
                )}
              </Button>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              {moderationCommands.map((cmd) => {
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
            <Button
              variant="outline"
              className="w-full h-12 gap-2 font-semibold bg-primary/5 border-primary/20 hover:bg-primary/10"
              onClick={() => setView("main")}
            >
              <ArrowLeft className="h-5 w-5" />
              ⬅️ Back
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};
