"use client";

import { useEffect, useState } from "react";
import { accountsApi, type BrokerAccount } from "@/lib/api";
import { useSelectedAccountId } from "@/lib/accountScope";

export default function AccountScopeBar() {
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useSelectedAccountId();

  useEffect(() => {
    const run = async () => {
      const list = await accountsApi.list(true);
      setAccounts(list);
      if (!selectedAccountId && list.length > 0) setSelectedAccountId(list[0].id);
    };
    run();
  }, [selectedAccountId, setSelectedAccountId]);

  return (
    <div className="border-b border-border px-4 py-2 bg-background">
      <div className="max-w-[1400px] mx-auto flex items-center gap-3">
        <div className="text-xs text-muted-foreground">Account</div>
        <select
          value={selectedAccountId}
          onChange={(e) => setSelectedAccountId(e.target.value)}
          className="w-full md:w-[420px] px-2 py-1 border rounded text-sm bg-background text-foreground"
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>{a.name} ({a.broker_type})</option>
          ))}
        </select>
      </div>
    </div>
  );
}
