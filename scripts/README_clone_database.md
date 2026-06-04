# Database clone and backup scripts

## Prerequisites

Install `postgresql-client` so the following commands are available:

- `pg_dump`
- `pg_restore`
- `psql`

Examples:

- Ubuntu or Debian: `sudo apt-get install postgresql-client`
- macOS with Homebrew: `brew install libpq`
- Windows: install the PostgreSQL client tools or use WSL with the package above

## Environment variables

Set these values before running the clone script:

- `OLD_DATABASE_URL`
- `NEW_DATABASE_URL`
- `PGSSLMODE=require`

The backup script uses:

- `DATABASE_URL`

## Clone the database

Run:

```bash
bash scripts/clone_database.sh
```

The script:

- creates a timestamped `greentrack_backup_YYYYMMDD_HHMMSS.dump`
- dumps the source database from `OLD_DATABASE_URL`
- restores the dump into `NEW_DATABASE_URL`
- keeps the backup file on disk
- prints row counts for `usuarios` and `registros` when those tables exist

## Verify the result

Connect with `psql` and inspect the target database:

```bash
psql "$NEW_DATABASE_URL"
```

Useful checks:

```sql
\dt
SELECT COUNT(*) FROM usuarios;
SELECT COUNT(*) FROM registros;
```

## Backup only

Run:

```bash
bash scripts/backup_database.sh
```

## Safety notes

- Do not commit real credentials.
- Do not commit `.dump`, `.sql`, or `.backup` files.
- Do not paste full connection strings into chat or issue trackers.
