"""S5 · the handoff.

**Botica issues no fiscal document.** It hands a complete, correct sale to
whatever invoicing system the client already runs, exactly once, and records
whatever comes back (§8, A9). It allocates no fiscal number, generates no CUDE,
signs no XML and speaks no DIAN protocol.

`document` is the canonical payload -- the stage's real deliverable; `service` is
the sale handoff service S4 calls and the only writer of `fiscal_documents`;
`delivery` is one attempt and the rules that make it safe to repeat; `targets`,
`mappings` and `transports` are the boundary no other module reaches through;
`export` is the file target; `settings` is the `invoicing` key group; `secrets`
is where a credential lives and `tenants.settings` is where it never does;
`api` is the HTTP surface; `demo` is this stage's fixture.

**With no target configured none of it runs**, which is the default and the state
every demo opens in.
"""
