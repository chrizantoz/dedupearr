# 🚀 Releasing

One command does the whole checklist:

```bash
scripts/release.sh v0.1.0 --prerelease        # drop --prerelease once out of beta
```

What it does, in order:

1. **Checks**: you are on `main`, the tree is clean and in sync with GitHub, CI is green on that commit, the tag is free.
2. **Notes**: the top section of `CHANGELOG.md` becomes the release notes; its heading is renamed to the version and today's date. So: write the changelog first, release second.
3. **Screenshots**: the browser-bot screenshots from that green CI run become `docs/screenshots/`, so the README always shows the version you just shipped.
4. **Commit, tag, push**: a `release vX.Y.Z` commit, an annotated tag, both pushed. CI builds `ghcr.io/chrizantoz/dedupearr:X.Y.Z`, `X.Y` and `latest` from that commit, and stamps the image with the version (the About tab shows it).
5. **GitHub release** with the notes.
6. **Boot check**: waits for the tag's CI run, then starts the tagged image once (locally with Docker, or over ssh with `RELEASE_VERIFY_SSH=user@host`) and checks it reports the version.

Flags: `--dry-run` shows what would change and stops before committing; `--no-screenshots` keeps the current ones.

Identity: the release commit uses your git config, or `RELEASE_GIT_NAME` / `RELEASE_GIT_EMAIL` if set.

Afterwards, update the container where you run it (Unraid: Docker tab → check for updates; compose: `docker compose pull && up -d`).

Version numbers: `vMAJOR.MINOR.PATCH`. Patch for fixes, minor for features, major when something changes that needs a note before upgrading. Phase 1 ships as `v0.1.x`, phase 2 as `v0.2.x`, and so on; beta releases are marked pre-release.
