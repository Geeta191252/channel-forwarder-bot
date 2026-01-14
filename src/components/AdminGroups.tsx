import { useState, useEffect, useMemo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  RefreshCw,
  Users,
  Shield,
  Crown,
  Trash2,
  Ban,
  Pin,
  UserPlus,
  Settings,
  MessageSquare,
  ExternalLink,
  Copy,
  Link as LinkIcon,
} from "lucide-react";
import { toast } from "sonner";

interface AdminGroup {
  _id: string;
  chat_id: number;
  chat_title: string;
  chat_type: string;
  member_count: number;
  username?: string | null;
  invite_link?: string | null;
  permissions: {
    is_owner: boolean;
    can_delete_messages: boolean;
    can_restrict_members: boolean;
    can_promote_members: boolean;
    can_change_info: boolean;
    can_invite_users: boolean;
    can_pin_messages: boolean;
    can_manage_chat: boolean;
  };
  updated_at: string;
}

interface AdminGroupsProps {
  botApiUrl?: string;
}

function normalizeBotApiUrl(raw: string) {
  return (raw || "").trim().replace(/\/+$/, "");
}

function normalizeTelegramLink(raw: string): string {
  const v = (raw || "").trim();
  if (!v) return "";

  // Most reliable for opening Telegram from web/mobile.
  if (/^https?:\/\//i.test(v)) return v;

  // Sometimes APIs/store return "t.me/..." without scheme.
  if (/^(t\.me|telegram\.me)\//i.test(v)) return `https://${v}`;

  // Rare: stored as "@username".
  if (v.startsWith("@")) return `https://t.me/${v.slice(1)}`;

  return v;
}

function getGroupLink(group: AdminGroup): string {
  const invite = normalizeTelegramLink(group.invite_link || "");
  if (invite) return invite;

  const username = (group.username || "").trim().replace(/^@/, "");
  if (username) return `https://t.me/${username}`;

  return "";
}

export function AdminGroups({ botApiUrl = "" }: AdminGroupsProps) {
  const apiBase = useMemo(() => normalizeBotApiUrl(botApiUrl), [botApiUrl]);

  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [generatingFor, setGeneratingFor] = useState<number | null>(null);

  const hasInsecureUrl = Boolean(apiBase) && apiBase.startsWith("http://");

  const fetchGroups = async () => {
    if (!apiBase) return;

    setLoading(true);
    try {
      const response = await fetch(`${apiBase}/admin-groups`);
      const data = await response.json();

      if (Array.isArray(data.groups)) {
        setGroups(data.groups);
      } else {
        setGroups([]);
      }
    } catch (error) {
      console.error("Failed to fetch admin groups:", error);
      toast.error("Admin groups load failed (API not reachable / blocked)");
    } finally {
      setLoading(false);
    }
  };

  const refreshGroups = async () => {
    if (!apiBase) return;

    setRefreshing(true);
    try {
      const response = await fetch(`${apiBase}/refresh-admin-groups`, {
        method: "POST",
      });
      const data = await response.json();

      if (data.success) {
        toast.success(`Refreshed! Checked ${data.result.total_seen} groups`);
        await fetchGroups();
      } else {
        toast.error(data.error || "Failed to refresh groups");
      }
    } catch (error) {
      console.error("Failed to refresh admin groups:", error);
      toast.error("Failed to refresh groups");
    } finally {
      setRefreshing(false);
    }
  };

  const generateLink = async (chatId: number) => {
    if (!apiBase) return;

    setGeneratingFor(chatId);
    try {
      const res = await fetch(`${apiBase}/generate-invite-link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId }),
      });

      const data = await res.json();

      if (data?.success && data?.link) {
        toast.success("Link generated");
        await fetchGroups();
      } else {
        const hint =
          data?.can_invite_users === false
            ? "Bot ko group admin settings me 'Invite users' permission ON karo"
            : "Link generate nahi ho paa raha (bot permissions / group type)";

        toast.error(data?.error ? `${data.error}` : hint);
      }
    } catch (e) {
      console.error(e);
      toast.error("Link generate request failed");
    } finally {
      setGeneratingFor(null);
    }
  };

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Copy failed");
    }
  };

  useEffect(() => {
    fetchGroups();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  const getPermissionBadges = (permissions: AdminGroup["permissions"]) => {
    const badges = [];

    if (permissions.is_owner) {
      badges.push({ icon: Crown, label: "Owner", variant: "default" as const });
    }
    if (permissions.can_delete_messages) {
      badges.push({ icon: Trash2, label: "Delete", variant: "secondary" as const });
    }
    if (permissions.can_restrict_members) {
      badges.push({ icon: Ban, label: "Ban", variant: "secondary" as const });
    }
    if (permissions.can_pin_messages) {
      badges.push({ icon: Pin, label: "Pin", variant: "secondary" as const });
    }
    if (permissions.can_invite_users) {
      badges.push({ icon: UserPlus, label: "Invite", variant: "secondary" as const });
    }
    if (permissions.can_manage_chat) {
      badges.push({ icon: Settings, label: "Manage", variant: "secondary" as const });
    }

    return badges;
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Admin Groups
            </CardTitle>
            <CardDescription>Groups where bot has admin permissions</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={refreshGroups} disabled={refreshing || !apiBase}>
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing..." : "Refresh"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {!apiBase ? (
          <div className="text-center py-8 text-muted-foreground">
            <p>Bot API URL dal ke save karo (settings me)</p>
          </div>
        ) : hasInsecureUrl ? (
          <div className="rounded-lg border p-4 text-sm text-muted-foreground">
            <p className="font-medium text-foreground mb-1">HTTP URL block ho sakta hai</p>
            <p>Bot API URL ko https:// me rakho, warna browser requests block kar deta hai.</p>
          </div>
        ) : loading ? (
          <div className="text-center py-8 text-muted-foreground">
            <RefreshCw className="h-8 w-8 animate-spin mx-auto mb-2" />
            <p>Loading groups...</p>
          </div>
        ) : groups.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <Shield className="h-12 w-12 mx-auto mb-3 opacity-50" />
            <p className="mb-2">No admin groups found</p>
            <p className="text-sm">"Refresh" karke scan karo</p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground mb-4">
              Found {groups.length} group{groups.length !== 1 ? "s" : ""} where bot is admin
            </p>

            {groups.map((group) => {
              const link = getGroupLink(group);
              const canInvite = Boolean(group.permissions?.can_invite_users);
              const showNoLinkHint = !link && group.chat_type !== "channel";

              return (
                <div key={group._id} className="p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0">
                      <h4 className="font-medium flex items-center gap-2">
                        <MessageSquare className="h-4 w-4 text-primary" />
                        {link ? (
                          <a
                            href={link}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 hover:underline truncate"
                            title={link}
                          >
                            <span className="truncate">{group.chat_title}</span>
                            <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                          </a>
                        ) : (
                          <span className="truncate">{group.chat_title}</span>
                        )}
                      </h4>
                      <p className="text-sm text-muted-foreground">ID: {group.chat_id}</p>
                      {showNoLinkHint ? (
                        <p className="text-xs text-muted-foreground mt-1">
                          Private group link tabhi aayegi jab bot ke admin permissions me <span className="font-medium">Invite users</span> ON hoga.
                        </p>
                      ) : null}
                    </div>

                    <div className="flex items-center gap-2 text-sm text-muted-foreground flex-shrink-0">
                      <Users className="h-4 w-4" />
                      {group.member_count > 0 ? group.member_count.toLocaleString() : "N/A"}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 mt-3">
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="outline" className="text-xs">
                        {group.chat_type}
                      </Badge>
                      {group.permissions &&
                        getPermissionBadges(group.permissions).map((badge, idx) => (
                          <Badge key={idx} variant={badge.variant} className="text-xs flex items-center gap-1">
                            <badge.icon className="h-3 w-3" />
                            {badge.label}
                          </Badge>
                        ))}
                    </div>

                    <div className="ml-auto flex items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => copyText(String(group.chat_id), "Chat ID")}
                      >
                        <Copy className="h-4 w-4 mr-2" />
                        Copy ID
                      </Button>

                      {link ? (
                        <Button type="button" variant="outline" size="sm" onClick={() => copyText(link, "Link")}>
                          <LinkIcon className="h-4 w-4 mr-2" />
                          Copy link
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={generatingFor === group.chat_id}
                          onClick={() => generateLink(group.chat_id)}
                          title={canInvite ? "Generate invite link" : "Enable 'Invite users' permission for the bot"}
                        >
                          <LinkIcon className={`h-4 w-4 mr-2 ${generatingFor === group.chat_id ? "animate-pulse" : ""}`} />
                          {generatingFor === group.chat_id ? "Generating..." : "Generate link"}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
