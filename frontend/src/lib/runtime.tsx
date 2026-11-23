"use client";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

// One build serves every env: the deploy writes /config.json = {apiUrl, env} next to the static files (SPEC §12).
export type Config = { apiUrl: string; env: string };

const ConfigContext = createContext<Config>({ apiUrl: "", env: "" });
export const useConfig = () => useContext(ConfigContext);

const ADMIN_KEY = "aegisvolt.adminToken";
export const adminToken = () => (typeof window === "undefined" ? "" : localStorage.getItem(ADMIN_KEY) ?? "");
export const setAdminToken = (t: string) => localStorage.setItem(ADMIN_KEY, t);

/** The fleet the operator acts for (kept per browser), and the sagas this tab started (the toasts follow them). */
type FleetState = { fleet: string; setFleet: (f: string) => void; watched: Set<string>; watch: (id: string) => void };
const FleetContext = createContext<FleetState>({ fleet: "", setFleet: () => {}, watched: new Set(), watch: () => {} });
export const useFleet = () => useContext(FleetContext);
const FLEET_KEY = "aegisvolt.fleet";

function FleetProvider({ children, fallback }: { children: React.ReactNode; fallback: string }) {
  const [fleet, setFleetState] = useState(() => localStorage.getItem(FLEET_KEY) || fallback);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const setFleet = useCallback((f: string) => { localStorage.setItem(FLEET_KEY, f); setFleetState(f); }, []);
  const watch = useCallback((id: string) => setWatched((w) => new Set(w).add(id)), []);
  return <FleetContext.Provider value={{ fleet, setFleet, watched, watch }}>{children}</FleetContext.Provider>;
}

/** Wall clock that re-renders every second: countdowns and progress bars. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return now;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{ config: Config; client: ApolloClient } | { error: string } | null>(null);

  useEffect(() => {
    fetch("/config.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`config.json ${r.status}`))))
      .then((config: Config) => {
        const client = new ApolloClient({
          link: new HttpLink({ uri: config.apiUrl }),
          cache: new InMemoryCache({
            typePolicies: {
              SagaStep: { keyFields: false },
              WalletTxn: { keyFields: false },
              ServiceHealth: { keyFields: false },
              Health: { keyFields: false },
              Flag: { keyFields: ["name"] },
              Wallet: { keyFields: ["fleetId"] },
            },
          }),
        });
        setState({ config, client });
      })
      .catch((e: Error) => setState({ error: e.message }));
  }, []);

  if (state && "error" in state) {
    return <p className="boot">Couldn&apos;t load /config.json ({state.error}). The deploy writes it next to the page.</p>;
  }
  if (!state) return null;
  return (
    <ConfigContext.Provider value={state.config}>
      <ApolloProvider client={state.client}>
        <FleetProvider fallback="FLEET-ANDINA">{children}</FleetProvider>
      </ApolloProvider>
    </ConfigContext.Provider>
  );
}
