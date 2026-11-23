"use client";

// Ported from Taller-PatronSaga dev-drawer.tsx: one switch per flag, wired to flags / setFlag.
// setFlag needs X-Admin-Token; the token is kept in this browser's localStorage only.
import { useState } from "react";
import { useMutation } from "@apollo/client/react";
import { FLAG_LABEL, when } from "@/lib/format";
import { SET_FLAG, type Flag } from "@/lib/gql";
import { adminToken, setAdminToken, useConfig } from "@/lib/runtime";

export function FlagsPanel({ flags }: { flags: Flag[] }) {
  const { env } = useConfig();
  const [token, setToken] = useState(adminToken);
  const [error, setError] = useState("");
  const [setFlag, { loading }] = useMutation(SET_FLAG);

  const toggle = async (f: Flag) => {
    setError("");
    try {
      await setFlag({ variables: { name: f.name, enabled: !f.enabled }, context: { headers: { "X-Admin-Token": token } } });
    } catch (e) {
      setError((e as Error).message.includes("FORBIDDEN") ? "The admin token was refused. Paste the one for this environment." : (e as Error).message);
    }
  };

  return (
    <section className="panel" aria-labelledby="flags-h">
      <header>
        <h2 id="flags-h">Feature flags</h2>
        <span className="meta">{flags.filter((f) => f.enabled).length} of {flags.length} on in {env}</span>
      </header>
      <div className="rows">
        {flags.map((f) => (
          <label className="row" key={f.name}>
            <span className="grow">
              {FLAG_LABEL[f.name] ?? f.name}
              <span className="sub">{f.description}{f.updatedAt ? ` · changed ${when(f.updatedAt)}` : ""}</span>
            </span>
            <input type="checkbox" role="switch" className="switch" checked={f.enabled} disabled={!token || loading}
              onChange={() => toggle(f)} aria-label={`${FLAG_LABEL[f.name] ?? f.name} in ${env}`} />
          </label>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <TokenForm token={token} onToken={setToken} what="change flags" />
    </section>
  );
}

/** The admin token box: paste once per browser; every admin mutation (flags, station owner) sends it. */
export function TokenForm({ token, onToken, what }: { token: string; onToken: (t: string) => void; what: string }) {
  const [draft, setDraft] = useState("");
  return !token ? (
    <form className="token" onSubmit={(e) => { e.preventDefault(); setAdminToken(draft); onToken(draft); }}>
      <input className="input" type="password" placeholder={`Admin token to ${what}`} value={draft}
        onChange={(e) => setDraft(e.target.value)} aria-label="Admin token" />
      <button className="btn" disabled={!draft}>Use token</button>
    </form>
  ) : (
    <button className="text-btn meta" onClick={() => { setAdminToken(""); onToken(""); }}>Forget admin token</button>
  );
}
