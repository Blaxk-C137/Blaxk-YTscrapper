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

## Deployment boundary

CreatorScope is a private/local workspace and does not include user
authentication. Do not expose port 8000 directly to the public internet. For a
shared deployment, put it behind an authenticated HTTPS reverse proxy or add an
identity layer first.

## Reporting

Do not open a public issue containing credentials, cookies, creator contact
exports, or other personal information. Contact the repository owner privately.
