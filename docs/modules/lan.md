# LAN Edition

Phase 15. PRD §9.4, §9.5, §9.6, §13 Phase 9, AC-12.

One computer is the **host**. It runs PostgreSQL 16 and the Nickland service, which is the
only thing that ever connects to the database. Every other computer is a **client**: it
opens the host's address in the Nickland window, over TLS, and holds no database
connection and no data of its own. The business rules are the same Python services as the
standalone edition; only the database and the transport differ.

## Installing the host

Everything is done with `nickland-host`, which reads the same `NICKLAND_*` settings as the
service. Put them in the host's environment or in `.env` in the data directory.

| Setting | Value |
| --- | --- |
| `NICKLAND_DEPLOYMENT_MODE` | `lan_host` |
| `NICKLAND_DATABASE_URL` | The **service** role's URL: `postgresql+psycopg://nickland_service:…@localhost/nickland` |
| `NICKLAND_MIGRATION_DATABASE_URL` | The **owner** role's URL, used only to upgrade the schema and for the restore drill |
| `NICKLAND_LAN_PORT` | `8443` unless something else needs it |
| `NICKLAND_POSTGRES_BIN` | The folder holding `pg_dump` and `pg_restore`, if they are not on the path |
| `NICKLAND_BACKUP_INTERVAL_HOURS` | `24` by default; `0` turns scheduled backups off |

1. **Database and roles.** Create the database as the PostgreSQL superuser, then
   `nickland-host roles --owner-password-env A --service-password-env B` prints the
   statements (passwords are read from those environment variables, never typed on a
   command line) and the `pg_hba.conf` lines; `--apply --admin-url …` carries them out.
   Set `listen_addresses = 'localhost'` in `postgresql.conf`: no client machine can reach
   the database at all.
2. **Schema.** `nickland-host upgrade` brings the schema to the current revision as its
   owner.
3. **Moving from a standalone installation** (optional).
   `nickland-host transfer --from C:\…\nickland.db` copies every table into the empty
   database and the attachment store into the host's, then compares each table (row count
   and a digest of every row, column by column) and each file (SHA-256). It refuses a
   database that already holds data.
4. **Certificate.** `nickland-host certificates --name erp-host --address 192.168.1.20`
   creates the host's own certificate authority once, issues a server certificate for the
   names and addresses clients will use, and prints its fingerprint.
5. **Service.** `nickland-host service --platform windows` prints a WinSW definition
   (`--platform linux`, a systemd unit) that starts the host at boot after PostgreSQL and
   restarts it within seconds if it stops. `nickland-host serve` is what it runs.
6. **Check.** `nickland-host status`, and the health screen.
7. **Setting up the business** (unless step 3 brought one across). Started on a database
   with no business yet, `nickland-host serve` prints a one-time setup code on its
   console, and writes it to the host's log for a host run as a service. Open the
   application from any workstation; the setup screen asks for the code before it
   creates the administrator. The code is new each time the host starts, until setup is
   done, and is not needed afterwards. Without it, any computer on the network that
   reached the setup screen first could make itself the administrator.

## Clients

Each client sets `NICKLAND_DEPLOYMENT_MODE=lan_client`, `NICKLAND_HOST_URL`
(`https://erp-host:8443`) and `NICKLAND_HOST_FINGERPRINT` (from step 4), and installs the
host's `authority.pem` as a trusted root (`certutil -addstore -f Root authority.pem`).
Before opening the window the client connects to the host and compares the certificate's
fingerprint with the one pinned; a host presenting any other certificate is refused before
anyone types a password into it. Plain HTTP is refused.

## What protects the data

| Threat | Protection |
| --- | --- |
| A computer on the network claiming a host not yet set up | Setup requires the one-time code the host shows only on its own console and log |
| A client writing to the database | PostgreSQL listens on loopback only; clients have no credentials; every write route of the service requires a signed-in session |
| The service rewriting history or the schema | The service role may change rows only: no `CREATE`, `ALTER` or `DROP`, and no `UPDATE` or `DELETE` on the audit trail |
| Eavesdropping on the network | TLS 1.2 or later; strict transport security; no caching of API replies |
| A host impersonated on the network | The client pins the host certificate's fingerprint |

## Concurrency

Many users write to one database. PostgreSQL runs at **read committed**, and every
invariant concurrent users could break is held by a lock taken *before* it is read, with a
unique constraint behind it (`persistence/locking.py`). Advisory locks are on names, so
they serve where the row does not exist yet, and are released when the transaction ends,
so a client that disconnects can never leave one held.

| Operation | Lock |
| --- | --- |
| Stock: reserve, release, receive, issue, transfer | The product in the warehouse |
| Document numbering | The sequence row (`FOR UPDATE`) |
| Journal numbering | The business and financial year |
| A posting with an idempotency key | The key: a retry waits for the first and finds its entry |
| Posting into a period | The period, shared; a close or reopening takes it exclusively |
| Opening a till shift | The business: shift numbers and one-open-shift-per-cashier |
| Completing a register sale | The sale: a double press posts once |
| A payment callback, a tender's confirmation | The tender: a duplicated callback changes nothing |
| Allocating a receipt to an invoice | The invoice |
| Paying a supplier | The supplier |

Several registers share one dataset: each cashier opens a shift on their own register, and
the stock they sell from is held per product and warehouse.

## Backup and the restore drill

A backup of a PostgreSQL host is a `pg_dump` (custom format) taken from an exported
snapshot, with every table's row count and digest computed in the transaction that holds
that snapshot, the attachment store, and a manifest of every file's digest, in one
archive. It is verified on completion (the dump's table of contents is read; each
attachment is checked against its digest) and pruned to the retention count. The service
takes one whenever the last verified backup is older than the interval.

`nickland-host drill` restores a backup into a scratch database and a scratch folder,
recomputes every table's digest and every file's, compares them with the manifest, and
drops the scratch copy. Nothing in use is touched. A standalone backup is drilled the same
way from its SQLite copy.

## Integrity

`IntegrityService.check` states what "no partial posting" means, and is run after anything
that could cut a transaction short:

* every posted journal entry balances and agrees with its lines;
* a completed register sale has its posting, and one not completed has none;
* every posted goods receipt and supplier invoice has its posting;
* stock positions agree with their movements and cost layers;
* the trial balance balances, and every control account agrees with its subledger.

## Health

| Check | Degraded or failed when |
| --- | --- |
| Encrypted transport | No certificate; expiry within 30 days |
| Database connections | Over 80% of PostgreSQL's limit in use |
| Background work | Scheduled reports or backups have not run in five intervals, or failed on their last pass |
| Backup | No verified backup in 48 hours |
| Network | The host cannot be reached |

## Verification

`tools/verify_phase_15.py` stands up a real host (fresh database, owner and service roles,
the schema by migration, a certificate, `nickland-host serve` in its own process) and
checks each exit criterion: every write route refused without a session over TLS and the
service role unable to change the schema or the audit trail; ten users over HTTPS with the
host killed outright mid-load and restarted, then no duplicate number, no lost reservation
and the integrity check whole; a backup drilled; and the whole automated suite on both
engines. `tests/concurrency/test_load.py`, `tests/integration/test_lan.py` and
`tests/integration/test_resilience.py` cover the same ground inside the suite.
