"use client";

import { useState } from "react";
import { useMutation } from "@apollo/client/react";
import { TokenForm } from "@/components/flags-panel";
import { money, when } from "@/lib/format";
import { CHARGE, CREATE_FLEET, FLEETS, TOP_UP, type Wallet } from "@/lib/gql";
import { adminToken, useFleet } from "@/lib/runtime";

const QUICK = [1000, 5000, 10000]; // cents

/** Demo wallet: deposit money into the fleet so a low-funds fleet can charge again. Uses the admin topUp mutation. */
export function Deposit({ wallet }: { wallet: Wallet }) {
  const { fleet } = useFleet();
  const [token, setToken] = useState(adminToken);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [topUp, { loading }] = useMutation(TOP_UP, { context: { headers: { "X-Admin-Token": token } }, refetchQueries: [CHARGE] });
  const deposits = wallet.transactions.filter((t) => t.kind === "TOPUP").slice(0, 3);

  const deposit = async (cents: number) => {
    setError("");
    try {
      await topUp({ variables: { fleetId: fleet, amountCents: cents } });
      setAmount("");
    } catch (e) {
      const m = (e as Error).message;
      setError(m.includes("FORBIDDEN") ? "The admin token was refused. Paste the one for this environment." : m);
    }
  };
  const custom = Math.round(Number(amount) * 100);
  const valid = custom > 0 && custom <= 1_000_000;

  return (
    <section className="panel" aria-labelledby="deposit-h">
      <header>
        <h2 id="deposit-h">Deposit</h2>
        <span className="meta">{fleet}</span>
      </header>
      <div className="balance">
        <span className="meta">Balance</span>
        <span className="kpi-value num">{money(wallet.balanceCents)}</span>
      </div>
      <div className="token">
        {QUICK.map((c) => (
          <button key={c} className="btn" disabled={!token || loading} onClick={() => deposit(c)}>+{money(c).replace(".00", "")}</button>
        ))}
      </div>
      <form className="token" onSubmit={(e) => { e.preventDefault(); if (valid) deposit(custom); }}>
        <input className="input num" inputMode="decimal" placeholder="Other amount, USD" value={amount}
          onChange={(e) => setAmount(e.target.value)} aria-label="Deposit amount in USD" />
        <button className="btn primary" disabled={!token || loading || !valid}>{loading ? "Depositing…" : "Deposit"}</button>
      </form>
      {amount && !valid && <p className="error">Enter an amount above $0 and up to $10,000.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {deposits.length > 0 && (
        <div className="rows" style={{ marginTop: 8 }}>
          {deposits.map((t) => (
            <div className="row" key={t.createdAt}>
              <span className="grow">Deposit<span className="sub">{when(t.createdAt)}</span></span>
              <span className="n num" style={{ color: "var(--positive)" }}>+{money(t.amountCents)}</span>
            </div>
          ))}
        </div>
      )}
      <TokenForm token={token} onToken={setToken} what="deposit" />
    </section>
  );
}

/** Onboard a fleet: a wallet with an optional opening deposit. The new fleet becomes the selected one. */
export function NewFleet() {
  const { setFleet } = useFleet();
  const [token, setToken] = useState(adminToken);
  const [name, setName] = useState("");
  const [opening, setOpening] = useState("100");
  const [error, setError] = useState("");
  const [create, { loading }] = useMutation(CREATE_FLEET, { context: { headers: { "X-Admin-Token": token } }, refetchQueries: [FLEETS], awaitRefetchQueries: true });
  const id = `FLEET-${name.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "-").replace(/^-+|-+$/g, "")}`;
  const cents = Math.round(Number(opening || 0) * 100);
  const valid = /^FLEET-[A-Z0-9-]{2,30}$/.test(id) && cents >= 0 && cents <= 1_000_000;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await create({ variables: { fleetId: id, initialCents: cents } });
      setFleet(id);
      setName("");
    } catch (err) {
      const m = (err as Error).message;
      setError(m.includes("FORBIDDEN") ? "The admin token was refused. Paste the one for this environment." : m);
    }
  };

  return (
    <section className="panel" aria-labelledby="newfleet-h">
      <header><h2 id="newfleet-h">Add a fleet</h2></header>
      <form className="charge" onSubmit={submit}>
        <div className="field">
          <label htmlFor="fleet-name">Fleet name</label>
          <input id="fleet-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Rappi Norte" maxLength={30} />
          {name && <span className="hint">Fleet ID <span className="mono">{id}</span></span>}
        </div>
        <div className="field">
          <label htmlFor="fleet-opening">Opening deposit, USD</label>
          <input id="fleet-opening" className="input num" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} />
        </div>
        {name && !valid && <p className="error">Use 2 to 30 letters or digits, and a deposit from $0 to $10,000.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary block" disabled={!token || !valid || loading}>{loading ? "Creating…" : "Create fleet and switch to it"}</button>
      </form>
      <TokenForm token={token} onToken={setToken} what="add fleets" />
    </section>
  );
}
