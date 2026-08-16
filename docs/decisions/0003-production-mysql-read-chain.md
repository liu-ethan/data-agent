# Production analytical reads use an explicit MySQL reader

Status: Accepted

The production composition root constructs `MySQLDataRepository` from the
`mysql.accounts.reader` configuration and injects it into `ReadGateway`.
`ReadGateway` and `RuntimeGraph` have no default data adapter.

The reader adapter verifies `CURRENT_USER()` and `SHOW GRANTS`, rejects write or
administrative privileges, enables MySQL read-only transaction mode and a
server-side execution timeout, and validates the eight-table business schema
during health checks. It never creates tables or inserts seed rows.

SQLite and deterministic seed data live only under explicit test composition.
Local MySQL seed data is installed by an operator-run script, not application
startup. Real integration evidence is run with `make test-mysql`.
