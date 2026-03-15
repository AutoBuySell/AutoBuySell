"use client";

import { useEffect, useState } from "react";

const KEY = "selected_account_id";
const EVT = "account-scope-changed";

export function setSelectedAccountId(id: string) {
  localStorage.setItem(KEY, id);
  window.dispatchEvent(new CustomEvent(EVT, { detail: id }));
}

export function useSelectedAccountId() {
  const [accountId, setAccountId] = useState<string>("");

  useEffect(() => {
    const cur = localStorage.getItem(KEY) || "";
    setAccountId(cur);

    const onChange = (e: Event) => {
      const ce = e as CustomEvent<string>;
      setAccountId(ce.detail || localStorage.getItem(KEY) || "");
    };
    window.addEventListener(EVT, onChange as EventListener);
    return () => window.removeEventListener(EVT, onChange as EventListener);
  }, []);

  return [accountId, setSelectedAccountId] as const;
}
