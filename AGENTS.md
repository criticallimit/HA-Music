# Repository workflow

- Publish authorized, validated changes to `main`; do not leave completed work only on a draft branch.
- Before every change to `main`, read its current commit and create a new backup branch pointing to that exact commit. Use `backup/main-YYYY-MM-DD-<purpose>-<short-sha>` (add a suffix if needed). Verify the backup ref before changing `main`.
- Preserve existing backup branches. Report the backup link and commit with the result. A passing CI is evidence for the repository checks, not proof that every device integration works live.
- Run the checks required by `.github/workflows/check.yml`, then verify CI on the resulting `main` commit. Use expected-head checks to avoid overwriting concurrent work.
- Restore a backup through a new commit containing its tree after inspecting subsequent changes and backing up the current `main` first. Preserve history; do not force-reset `main` or delete backups.
- Updating `main` does not authorize a release, version increase, or installation/deployment. These require the user's explicit request.

Current recovery points are documented in `README.md`.

