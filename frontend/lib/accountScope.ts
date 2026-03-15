"use client";

import { useEffect, useState } from "react";

const KEY = "selected_account_id";
const EVT = "account-scope-changed";

export function setSelectedAccountId(id: string) {
  localStorage.setItem(KEY, id);
  window.dispatchEvent(new CustomEvent(EVT, { detail: id }));
}

export function useSelectedAccountId() {
  const [accountId, setAccountId] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(KEY) || "";
  });

  useEffect(() => {
    const onChange = (e: Event) => {
      const ce = e as CustomEvent<string>;
      setAccountId(ce.detail || localStorage.getItem(KEY) || "");
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) setAccountId(e.newValue || "");
    };
    window.addEventListener(EVT, onChange as EventListener);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVT, onChange as EventListener);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return [accountId, setSelectedAccountId] as const;
}
