import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Shield, UserPlus, Trash2, Info, Copy, CheckCircle } from "lucide-react";
import { toast } from "sonner";

export const ForceJoin = () => {
  const [copied, setCopied] = useState<string | null>(null);

  const copyCommand = (command: string, id: string) => {
    navigator.clipboard.writeText(command);
    setCopied(id);
    toast.success("Command copied!");
    setTimeout(() => setCopied(null), 2000);
  };

  const CopyIcon = ({ id }: { id: string }) =>
    copied === id ? (
      <CheckCircle className="h-4 w-4 text-green-500" />
    ) : (
      <Copy className="h-4 w-4 opacity-60" />
    );

  return (
    <Card className="border-primary/20">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Force Join Settings</CardTitle>
        </div>
        <CardDescription>
          Group में message भेजने से पहले users को channel या group join करना होगा
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {/* Full width button - Set Force Join */}
        <Button
          variant="outline"
          className="w-full justify-center gap-2 h-12 text-sm bg-primary/5 border-primary/20 hover:bg-primary/10"
          onClick={() => copyCommand("/setforcejoin @channel_or_group|Name|https://t.me/+invite", "setforcejoin")}
        >
          <UserPlus className="h-5 w-5 text-green-500" />
          <span className="font-medium">Set Force Join</span>
          <CopyIcon id="setforcejoin" />
        </Button>

        {/* Two buttons in a row - Remove & Info */}
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="outline"
            className="w-full justify-center gap-2 h-12 text-sm bg-primary/5 border-primary/20 hover:bg-primary/10"
            onClick={() => copyCommand("/removeforcejoin", "removeforcejoin")}
          >
            <Trash2 className="h-5 w-5 text-red-500" />
            <span className="font-medium">Remove</span>
            <CopyIcon id="removeforcejoin" />
          </Button>
          <Button
            variant="outline"
            className="w-full justify-center gap-2 h-12 text-sm bg-primary/5 border-primary/20 hover:bg-primary/10"
            onClick={() => copyCommand("/forcejoininfo", "forcejoininfo")}
          >
            <Info className="h-5 w-5 text-blue-500" />
            <span className="font-medium">Info</span>
            <CopyIcon id="forcejoininfo" />
          </Button>
        </div>

        {/* How to use section */}
        <div className="mt-4 p-3 rounded-lg bg-primary/5 border border-primary/20">
          <h4 className="font-medium text-foreground mb-2 flex items-center gap-2">
            <Info className="h-4 w-4 text-primary" />
            How to use
          </h4>
          <ol className="text-sm text-muted-foreground space-y-1 list-decimal list-inside">
            <li>Bot को group में admin बनाएं (Delete messages permission)</li>
            <li>Bot को target channel/group में admin बनाएं</li>
            <li>For private groups: Chat ID और invite link use करें</li>
            <li><code className="bg-background px-1 rounded">/setforcejoin</code> command use करें</li>
            <li>Non-members के messages auto-delete होंगे</li>
          </ol>
        </div>
      </CardContent>
    </Card>
  );
};
