# Agenda Worker Limit Design

## Goal

Bound agenda collection resource use on the production runner while retaining
parallel collection. A franchise-wide agenda job must never have more than two
portal-slot collectors actively using Playwright browser contexts at once.

## Design

Define `MAX_CONCURRENT_AGENDA_WORKERS = 2` as a module-level constant in
`scraper/agenda.py`. Each agenda job creates one `asyncio.Semaphore` from that
constant and shares it with every student task in the job.

Each configured, agenda-capable portal slot must acquire the shared semaphore
before opening its isolated browser context. It holds the permit through login,
agenda retrieval, normalization, and browser-context cleanup, then releases the
permit. Waiting tasks remain asynchronous and may proceed as permits become
available.

The limit counts active portal-slot collectors rather than active students. A
student with two agenda-capable portals can therefore consume both permits, but
additional students cannot open more browser contexts until a permit is
released. Direct `fetch_agenda` calls outside the franchise job retain the same
bounded behavior.

## Behavior Preserved

- Portal 1 and Portal 2 remain fixed as `agenda1` and `agenda2`.
- Slot results for one student remain all-or-nothing.
- Results are posted as each student's collection finishes.
- Lease failure and task cancellation continue to stop pending work.
- The CLI arguments and database boundary do not change.

## Failure and Cancellation

Semaphore permits are managed with an asynchronous context manager so normal
completion, collection failure, and cancellation all release permits. A task
cancelled while waiting for a permit never opens a browser context.

## Testing

Add a regression test that starts more eligible portal-slot collectors than the
constant allows, blocks them inside the fake collection boundary, and records
the peak number active. The test must demonstrate that the peak never exceeds
`MAX_CONCURRENT_AGENDA_WORKERS` while all students still complete and post their
results. Existing agenda boundary tests must continue to pass.
