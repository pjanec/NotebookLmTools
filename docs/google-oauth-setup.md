# Creating the Google OAuth client (once, ~10 minutes)

You need this before `setup.ps1` can reach Google Drive. Written from an actual run
through the console on 2026-09-05, including the parts that waste time.

## Why this is necessary

Google needs two separate things to allow API access to your Drive:

- **Your Google account** — *who* the files belong to. You already have this.
- **An OAuth client ID** — *which program* is asking. This is the missing piece.

That is what the browser consent screen shows: "**‹app name›** wants access to your Google
Drive." The app's identity comes from the client ID. You are registering the *tool*, not
creating a second account or a second Drive.

On its own the client ID grants nothing. Access happens only when you approve it in the
browser, and the resulting token is the combination of this app + your account + the
`drive.file` scope.

Tools that appear to skip this step are lending you *their* registration. rclone did that,
and is retiring it: *"rclone's shared Google Drive client_id is being retired and will stop
working during 2026."* Note what is retiring — the **shared client id**, not rclone. rclone
is still what moves the files; it just needs your credentials instead of its own.

## What this is not

- **Not a separate Drive.** Files land in your ordinary personal Google Drive, visible and
  deletable in the web UI exactly as if you had dragged them in.
- **Not a service account.** Those are a robot identity with *their own* Drive, so files
  would land where NotebookLM cannot see them. On the Clients page, do not create one.
- **Not an API key.** Those cannot access user data at all.
- **Not billable.** The Drive API is free; no billing account is needed. Only the stored
  files count against your normal 15 GB account quota.

## Where things live in the console

The console was reorganised and most guides on the web are out of date. What used to be
*APIs & Services → Credentials* is now **Google Auth Platform**, whose left nav has
*Overview, Branding, Audience, Clients, Data Access, Verification Center*.

## Steps

1. **Create a project.** <https://console.cloud.google.com/>, project picker at the top,
   *New project*. Any name; `notebooklm-tools` does fine.

2. **Enable the Drive API.** *APIs & Services → Library*, search **Google Drive API**,
   open it, **Enable**.

3. **Branding.** App name, your own email as user support contact, your own email as
   developer contact. That is all.

   **Leave "App domain" and "Authorized domains" empty.** Authorized domains are only
   required if you enter a home page, privacy policy or terms URL — enter none, and there
   is nothing to authorize.

4. **Audience.** User type **External**.

   *Internal* is offered only if the account belongs to a Google Workspace organisation.
   On a personal `@gmail.com` there is no organisation, so External is forced.

5. **Add yourself as a test user.** *Audience → Test users → Add users*, your own Gmail
   address.

   **This is the step people miss.** Without it the browser flow fails with `access_denied`
   even though everything else is correct.

6. **Create the client.** *Clients → Create client → Application type: **Desktop app*** →
   any name → *Create*. You are shown a **Client ID** and a **Client secret**.

7. **Hand them to the installer**, from the repository root:

   ```
   powershell -f setup.ps1 -DriveClientId <the client id> -DriveClientSecret <the secret>
   ```

   A browser opens. You will see an **"unverified app"** warning — expected for a
   Testing-status app. Click **Advanced → Go to ‹app name› (unsafe)** and approve.

   Both values are stored in Windows Credential Manager, so you pass them once. Later runs
   are just `powershell -f setup.ps1`.

> **Never paste the client secret into a chat, a config file, a commit message, or any file
> in this repository.** Pass it on the command line and let it go into Credential Manager.
> See `CLAUDE.md`.

## What to ignore

**"Publishing status: your app's OAuth configuration is incomplete… Please visit the
Branding page"**, with the publish button greyed out.

Ignore it. Publishing to production is **not required** to create a client or to use the
API. It is only worth doing to avoid the token expiry below, and for a personal account the
price is too high: publishing demands a home page and privacy policy URL on a domain you
own *and* have verified in Search Console. For a single-user tool that is not worth it.

The verification warnings exist because Google reviews apps requesting *sensitive* scopes.
`drive.file` is non-sensitive — an app can only touch files it created — so there is
nothing to review, and nothing here needs your attention.

## The cost of staying in Testing

Google expires the refresh token **7 days** after it is issued for an app in Testing
status. So roughly weekly, a run fails with:

```
FAILED  load  (DRIVE_AUTH, exit 10)
  ...
  -> run setup.ps1 to reauthorize rclone
```

Recovery is one command and a browser click:

```
powershell -f setup.ps1
```

The client ID and secret stay in Credential Manager, so there is nothing to re-type. The
failure is clean — it happens before anything in the notebook is touched, so no load is
left half-finished.

## What the tools can see

The `drive.file` scope grants access **only to files the app itself creates**. It cannot
read the rest of your Drive. That is why the tools create and own their own folder
(`/NotebookLmTools/...`) instead of pointing at a folder you made by hand.

## Moving to another machine

Windows Credential Manager is bound to your Windows account on one machine, so nothing
sensitive travels with the repository. On a new machine, run `setup.ps1` with the same
client ID and secret and approve the browser prompt once. The Cloud project does not need
recreating — one client serves every machine you use.

## If it goes wrong

| Symptom | Cause |
|---|---|
| `access_denied` at the consent screen | you are not in *Audience → Test users* |
| "unverified app" warning | expected in Testing; *Advanced → Go to ‹app name›* |
| `DRIVE_AUTH` after about a week | the 7-day Testing token expiry; re-run `setup.ps1` |
| publish button greyed out | expected and irrelevant; see "What to ignore" |
| `Drive API has not been used in project…` | step 2 was skipped |
