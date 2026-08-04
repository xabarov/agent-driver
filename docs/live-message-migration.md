# Live-message contract v1 migration

The v1 migration is additive: the existing `command_queue` table remains, its
JSON payload gains typed semantic/phase/generation fields, and stores add
`live_message_runs` plus `control_schema_meta`. Do not delete or rewrite
populated queue tables during rollout.

## Forward cutover

1. Stop live-message admission in the host and let current consumers reach a
   safe boundary.
2. Inventory every Agent Driver importer and verify that all will load the same
   immutable package/wheel identity.
3. Start one v1 process against the existing schema. Idempotent DDL creates the
   run-state and schema-version tables.
4. Call `quarantine_legacy_rows()`. Ambiguous pre-v1 message rows with `NEXT`
   priority become terminal `failed` rows with `legacy_unresolved`; their JSON
   payload remains present for operator reconciliation and is never executed
   under v1 semantics.
5. Recreate all importers together. Reject capability unless package identity,
   required public symbols, `contract_schema_version() == 1`, Postgres store,
   and host adapter version agree.
6. Re-enable admission only after the capability manifest returns all four
   live-message features as true.

Old readers use Pydantic's fail-closed extra-field validation and cannot safely
consume v1 payloads. Therefore rolling mixed-version message consumption is
unsupported even though the DDL is additive.

## Rollback

Before any v1 row is admitted, stop the new processes and restore the previous
immutable package identity; the additive empty tables may remain.

After a v1 row exists, do not run an older consumer, downgrade the schema, or
delete rows. Disable admission and forward-recover all importers to the exact v1
release. A host may keep read-only diagnostics available while capability
remains false.

## Crash recovery

- Boundary and hard-redirect claims retain `claimed_by`/`claimed_at`. Fence the
  dead process before releasing its claim.
- NEXT dispatch retries with the same durable claimant and `handoff_id`. The
  host must enforce unique `handoff_id -> destination_turn_id` and unique user
  message provenance.
- A command accepted before terminal is recovered as promoted NEXT. A request
  first observed after terminal is rejected and creates no row.
- Never retry uncertain work under a new dedupe key or queue identity.
