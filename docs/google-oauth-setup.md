# Creating the Google OAuth client (once, ~10 minutes)

You need this before `setup.ps1` can reach Google Drive. It is a one-time job, and the
resulting credentials work on any other machine you run these tools on.

## Why this is necessary

Any program that writes to your Drive through the API needs an OAuth **client id**. There
is no way around it.

Tools that appear to skip this step are lending you *their* registration. rclone did
exactly that, and is now retiring it: *"rclone's shared Google Drive client_id is being
retired and will stop working during 2026."* Note what is being retired — the **shared
client id**, not rclone. rclone remains actively maintained and is what these tools use to
move files; it simply needs to be given your own credentials rather than borrowing its
own. Continuing on the shared id only postpones this setup until it breaks mid-run.

## What this is not

- **It is not a separate Drive.** Files land in your ordinary personal Google Drive,
  visible and deletable in the web UI exactly as if you had dragged them in.
- **It is not a service account.** Those own files in their own Drive, which NotebookLM
  cannot read; the tools sign in as you.
- **It does not cost anything.** No billing account, no quota purchase.

The Cloud project is a registration slip, nothing more. It owns no files.

## Steps

1. **Create a project.** Go to <https://console.cloud.google.com/>, open the project
   picker at the top, and create a new project. Name it anything — `notebooklm-tools`
   does fine.

2. **Enable the Drive API.** *APIs & Services → Library*, search for **Google Drive API**,
   open it, and click **Enable**.

3. **Configure the consent screen.** *APIs & Services → OAuth consent screen* (recent
   console versions call this *Google Auth Platform → Branding*).
   - User type: **External**.
   - Fill in the app name, your own email as user support contact, and your own email as
     developer contact. Nothing else is required.
   - **Scopes: add nothing.** The scope is requested at run time, and adding it here
     changes nothing.

4. **Publish the app.** On the consent screen (or *Audience*), set the publishing status
   to **In production**.

   Do not skip this. An app left in *Testing* has its **refresh tokens expired by Google
   after 7 days**, so Drive access would break every week. Publishing normally triggers a
   verification review, but only for sensitive scopes — `drive.file` is classified
   non-sensitive precisely because an app can only touch files it created, so there is
   nothing to review.

5. **Create the client id.** *APIs & Services → Credentials → Create credentials → OAuth
   client ID*.
   - Application type: **Desktop app**.
   - Name it anything.
   - Click Create. You are shown a **client ID** and a **client secret**.

6. **Hand them to the installer**, from the repository root:

   ```
   powershell -f setup.ps1 -DriveClientId <the client id> -DriveClientSecret <the secret>
   ```

   A browser opens; approve access for your normal Google account. Both values are stored
   in Windows Credential Manager, so you only pass them once — later runs are just
   `powershell -f setup.ps1`.

> **Do not paste the client secret into a chat, a config file, a commit message, or any
> file in this repository.** Pass it on the command line to `setup.ps1` and let it store
> the value in Credential Manager. See `CLAUDE.md`.

## What the tools can see

The `drive.file` scope grants access **only to files the app itself creates**. It cannot
read the rest of your Drive. That is why the tools create and own their own folder
(`/NotebookLmTools/...`) rather than pointing at a folder you made by hand.

## Moving to another machine

Windows Credential Manager is bound to your Windows account on one machine, so nothing
sensitive travels. On a new machine, run `setup.ps1` with the same client id and secret
and approve the browser prompt once. The Cloud project does not need recreating.
