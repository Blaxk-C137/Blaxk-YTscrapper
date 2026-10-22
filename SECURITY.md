# Security

## Important: remove the committed browser profile

An earlier commit included `browser_profile/`, including Chromium cookies and
local login databases. The application now deletes this directory from the
working tree and `.gitignore` prevents it from being committed again, but **that
does not remove it from existing Git history**.

If this repository was ever public, do all of the following immediately:

1. Sign out of the Google/YouTube account on all devices or revoke its active
   sessions from Google Account → Security → Your devices.
2. Change the account password and review recent security activity.
3. Purge the profile from the repository history, then force-push:

   ```bash
   pip install git-filter-repo
   git filter-repo --path browser_profile --invert-paths --force
   git push --force --all
   git push --force --tags
   ```

4. Ask collaborators to re-clone after the history rewrite.
5. Create a fresh session with `python scripts/connect_youtube.py`.

Chrome protects some values with OS-level encryption, but a checked-in browser
profile must still be treated as compromised.

## Session exports

`browser_profile/`, `.runtime/youtube-storage-state.json`, and
`.runtime/youtube-storage-state.b64` can grant access to the connected account.
They are ignored by Git, but must still be stored only on trusted devices and in
Render's secret environment-variable store. Never put them in Vercel variables,
frontend code, logs, screenshots, support messages, or repository files. Rotate
the Google session immediately if one leaks.

## Deployment boundary

Hosted CreatorScope is a private, single-workspace application—not a multi-user
identity system. Set a long random `CREATORSCOPE_API_KEY`; the Vercel frontend
asks each authorized user to enter it in their own browser. Keep CORS restricted
to exact trusted frontend origins and use HTTPS. Health and API documentation
remain public, but creator data and job routes require the key.

For a team or public-facing service, place the Render backend behind a proper
identity-aware proxy instead of sharing one workspace key. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the supported private deployment.

## Reporting

Do not open a public issue containing credentials, cookies, creator contact
exports, or other personal information. Contact the repository owner privately.
