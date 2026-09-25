# Reliability Monitoring and Retry Queue

## Scope

This phase adds durable retry handling and a compact technical status view. It does not change the existing order allocation rules, payment matching, SMM business rules, or FunPay account model.

## Goals

- Persist recoverable failed operations across bot restarts.
- Retry only idempotent or explicitly deduplicated operations.
- Expose queue health and recent errors to bot administrators.
- Keep failed operations visible instead of silently dropping them.

## Non-goals

- Encrypting existing secrets.
- Backup and restore.
- Rewriting all FunPay operations into a new service layer.
- Automatically retrying an order creation or Steam-account allocation unless an existing operation key proves it has not already completed.

## Data Model

Add a `reliability` storage table containing:

- `queue`: retry records with `id`, `operation_key`, `kind`, `owner_id`, `payload`, `status`, `attempts`, `max_attempts`, `next_retry_at`, `last_error`, `created_at`, `updated_at`, and `completed_at`.
- `events`: bounded technical events with `level`, `kind`, `owner_id`, `message`, and timestamp.
- `health`: per-owner runtime information such as last successful connection, last paid-order scan, last message scan, last SMM scan, and last error.

`operation_key` is unique among active records. Enqueueing the same key while a record is pending, processing, or done must not create a duplicate.

## Retry Policy

- Worker interval: 10 seconds.
- Claim records whose `status` is `pending` and `next_retry_at` is due.
- Retry delays: 30 seconds, 2 minutes, 10 minutes, 30 minutes, then 2 hours.
- Default maximum: 5 attempts.
- On success, mark `done`.
- On an exception, persist the error and schedule the next attempt.
- After the final attempt, mark `failed` and notify the relevant administrator once.
- A retry handler must be registered by operation kind and receive the stored payload.

The first handlers cover delivery-safe operations: FunPay message delivery and FunPay refund. Existing order IDs and notification claim records remain the source of truth for duplicate protection.

## Integration

- Existing synchronous FunPay methods continue to run normally.
- When a delivery or refund operation fails, the operation is enqueued with a stable key instead of being discarded.
- The worker invokes the operation through the owner-specific `FunPayBridge`.
- SMM status checks record health and errors; they do not retry order creation in this phase.
- The existing `processed_order_ids` and processing locks remain unchanged.

## Admin UI

Add an “Operational status” section to the existing admin panel:

- counts for pending, processing, done, and failed records;
- last errors and last successful scans by FunPay workspace;
- list of failed operations with “retry now” and “dismiss” actions;
- refresh button and back navigation.

Only IDs from `ADMIN_IDS` can access this view. User-facing menus remain unchanged.

## Error Handling

- Queue storage failures are logged and never allowed to crash the polling loop.
- Unknown operation kinds become `failed` with an explicit error.
- Missing or invalid owner IDs are rejected when enqueueing.
- Telegram notification failures do not change the queue result.

## Verification

- Unit tests for deduplication, claiming, backoff, success, permanent failure, and restart persistence.
- Tests for isolation between `<id>`, `<id>_2`, and `<id>_3`.
- Smoke test for dispatcher imports and storage migration.
- Existing compile and import checks must remain green.

## Rollout

The migration is additive. Existing `bot.sqlite3` data remains readable. The worker starts alongside existing background workers and performs no work until the queue contains records.
