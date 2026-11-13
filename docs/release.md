# Releasing

Releases are cut from `main` by pushing an annotated tag `vX.Y.Z`. The tag must
equal `project.version` in `pyproject.toml`.

## Before tagging

1. Update `version` in `pyproject.toml` and `__version__` in
   `buildscope/__init__.py`.
2. Add a `## [X.Y.Z] - YYYY-MM-DD` section to `CHANGELOG.md`. Its body becomes
   the GitHub release notes.
3. Merge to `main` and wait for CI to pass on Ubuntu and macOS.
4. On a clean checkout of `main`:

   ```bash
   python -m buildscope.release preflight --tag vX.Y.Z
   ```

   Preflight fails on any of:

   - a dirty working tree, including untracked files;
   - tracked databases, models, pickles, keys or `.env` files;
   - `pyproject.toml`, `buildscope.__version__` and the tag disagreeing;
   - a missing `LICENSE` or `SECURITY.md`, or no non-empty changelog section;
   - a wheel without the dashboard assets, or with private files in it.

   It builds a wheel to check the last item; pass `--wheel PATH` to inspect an
   existing one instead.

## Tagging

```bash
git tag -a vX.Y.Z -m "BuildScope X.Y.Z"
git push origin vX.Y.Z
```

`.github/workflows/release.yml` then:

1. refuses to continue unless the tag equals `v` + `project.version`;
2. builds the wheel and sdist and reruns preflight against that wheel;
3. installs the wheel in a fresh virtual environment and smoke-tests the CLI and
   the dashboard;
4. writes `SHA256SUMS`;
5. creates the GitHub release with the wheel, sdist and `SHA256SUMS`, using the
   changelog section as the notes.

## Verifying a release

```bash
gh release download vX.Y.Z --dir release-check
cd release-check
shasum -a 256 -c SHA256SUMS
```

Never move or reuse a published tag. Fix problems in a new patch release.

## Database backups

Back up before upgrading BuildScope or moving a database:

```bash
python -m buildscope.release db-check --database data/buildscope.db
python -m buildscope.release backup --database data/buildscope.db \
  --output data/backups/buildscope-$(date +%Y%m%d).db
python -m buildscope.release restore-test --database data/buildscope.db \
  --backup data/backups/buildscope-$(date +%Y%m%d).db
```

- `db-check` runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check` and
  confirms the schema is at the packaged migration head.
- `backup` copies the database with SQLite's online backup API, so workers can
  keep running. It reopens the copy, runs the same checks, writes
  `<backup>.sha256`, and refuses to overwrite anything.
- `restore-test` verifies the checksum, restores the backup into a temporary file
  and compares its schema and per-table row counts with the source. Run it before
  new runs are recorded, or it will report the rows written since the backup.

To restore, stop the API and workers, copy the backup over the database file and
run `db-check`.
