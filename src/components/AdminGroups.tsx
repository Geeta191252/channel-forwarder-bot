import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, Users, Shield, Crown, Trash2, Ban, Pin, UserPlus, Settings, MessageSquare } from "lucide-react";
import { toast } from "sonner";

interface AdminGroup {
  _id: string;
  chat_id: number;
  chat_title: string;
  chat_type: string;
  member_count: number;
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

export function AdminGroups({ botApiUrl = "" }: AdminGroupsProps) {
  const [groups, setGroups] = useState<AdminGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const fetchGroups = async () => {
    if (!botApiUrl) return;
    
    setLoading(true);
    try {
      const response = await fetch(`${botApiUrl}/admin-groups`);
      const data = await response.json();
      
      if (data.groups) {
        setGroups(data.groups);
      }
    } catch (error) {
      console.error("Failed to fetch admin groups:", error);
    } finally {
      setLoading(false);
    }
  };

  const refreshGroups = async () => {
    if (!botApiUrl) return;
    
    setRefreshing(true);
    try {
      const response = await fetch(`${botApiUrl}/refresh-admin-groups`, {
        method: "POST",
      });
      const data = await response.json();
      
      if (data.success) {
        toast.success(`Refreshed! Found ${data.result.saved} admin groups`);
        await fetchGroups();
      } else {
        toast.error("Failed to refresh groups");
      }
    } catch (error) {
      console.error("Failed to refresh admin groups:", error);
      toast.error("Failed to refresh groups");
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchGroups();
  }, [botApiUrl]);

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
            <CardDescription>
              Groups where bot has admin permissions
            </CardDescription>
          </div>
          <Button 
            variant="outline" 
            size="sm" 
            onClick={refreshGroups}
            disabled={refreshing || !botApiUrl}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing..." : "Refresh"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {!botApiUrl ? (
          <div className="text-center py-8 text-muted-foreground">
            <p>Configure Bot API URL in settings to view admin groups</p>
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
            <p className="text-sm">Click "Refresh" to scan for groups where bot is admin</p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground mb-4">
              Found {groups.length} group{groups.length !== 1 ? "s" : ""} where bot is admin
            </p>
            
            {groups.map((group) => (
              <div
                key={group._id}
                className="p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
              >
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <h4 className="font-medium flex items-center gap-2">
                      <MessageSquare className="h-4 w-4 text-primary" />
                      {group.chat_title}
                    </h4>
                    <p className="text-sm text-muted-foreground">
                      ID: {group.chat_id}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Users className="h-4 w-4" />
                    {group.member_count > 0 ? group.member_count.toLocaleString() : "N/A"}
                  </div>
                </div>
                
                <div className="flex flex-wrap gap-1.5 mt-3">
                  <Badge variant="outline" className="text-xs">
                    {group.chat_type}
                  </Badge>
                  {group.permissions && getPermissionBadges(group.permissions).map((badge, idx) => (
                    <Badge key={idx} variant={badge.variant} className="text-xs flex items-center gap-1">
                      <badge.icon className="h-3 w-3" />
                      {badge.label}
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}