# Security

Dedupearr reads your media library and, from phase 3 on, will move files in it, so please report anything security-relevant privately rather than in a public issue: use GitHub's "Report a vulnerability" button on the Security tab of this repository.

What is in place today (phase 1): the container mounts your media read-only and the code has no path that moves, links or deletes a file; no API keys or passwords are stored, so none can leak; the About page shows settings but never a secret; the public demo runs on a fake disk on an internal network with per-visitor rate limits and resets every hour. Every push is scanned with Trivy for known vulnerabilities in the image, and the full test harness is described in `docs/TESTING.md`.

Phase 1 has no sign-in: the page is open to whoever can reach the port. Keep it on your local network or behind your reverse proxy's login. A built-in sign-in arrives with phase 3, before the first button that changes a file.
