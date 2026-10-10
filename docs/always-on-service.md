# Running it as an always-on service

How to leave these tools running on a machine that is always on, so an **LLM can ask the
corpus questions** without a human in the loop.

Two things are separable, and conflating them is the usual mistake:

- **The service** — a background agent that polls for work and runs it. Section 2.
- **The transport** — how an LLM's question reaches that agent. Section 5, and the answer
  depends entirely on *where the LLM runs*, not on preference.

Prerequisite: the tools installed and verified on this machine per
[`bootstrap-new-machine.md`](bootstrap-new-machine.md), and at least one project configured
per [`onboarding-a-project.md`](onboarding-a-project.md).

---

## 1. What the service actually is

A single Python process in a loop: fetch the private ops repo, run any job it finds, push
the result, sleep 20 seconds. **Nothing listens.** It makes outbound git connections and
nothing else, so there is no port to open, no tunnel, no certificate, and nothing to find by
scanning. The design and the trust boundary are in
[`remote-control.md`](remote-control.md); this document is the operations side.

One rule, and it is the one that bites: **exactly one agent may run against one ops repo.**
Job identity comes from the filename, so two agents both pick up the same job and both
execute it — an `ask` asked twice, a `refresh` dumped and reloaded twice.

---

## 2. Install it as a background service

### 2.1 The wrapper script

A `.cmd` wrapper rather than invoking Python directly, so the exit code and the log survive
the Task Scheduler. Write it outside the repository — it names machine-specific paths:

`%USERPROFILE%\nlm-agent.cmd`

```bat
@echo off
setlocal
set "PYTHON=D:\WORK\NotebookLM\.venv\Scripts\python.exe"
set "CONFIG=%USERPROFILE%\ops-agent.json"
set "LOG=%USERPROFILE%\nlm-ops-agent.log"

if not exist "%PYTHON%" (
    echo [%date% %time%] cannot find %PYTHON% -- run setup.ps1 first>>"%LOG%"
    exit /b 2
)
if not exist "%CONFIG%" (
    echo [%date% %time%] cannot find %CONFIG%>>"%LOG%"
    exit /b 2
)

echo.>>"%LOG%"
echo [%date% %time%] agent starting>>"%LOG%"

rem No pipes here: piping would hide the exit code behind the pipe's.
"%PYTHON%" -m nlmtools.ops.agent --config "%CONFIG%" >>"%LOG%" 2>&1

echo [%date% %time%] agent exited with %errorlevel%>>"%LOG%"
exit /b %errorlevel%
```

### 2.2 The scheduled task

```powershell
$action  = New-ScheduledTaskAction -Execute "$env:USERPROFILE\nlm-agent.cmd"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "NotebookLM relay agent" `
    -Action $action -Trigger $trigger -Settings $settings
```

Each setting earns its place:

| Setting | Why |
|---|---|
| `-AtLogOn -User <you>` | **It must run as you.** The Drive token is in *your* Credential Manager, and DPAPI is per-user — another account cannot read it |
| `MultipleInstances IgnoreNew` | the direct guard against two agents on one queue |
| `RestartCount 999`, 1-minute interval | a crash resumes by itself; the agent is a poll loop, so restarting is always safe |
| `ExecutionTimeLimit 0` | unlimited. The default would kill a long-running agent after three days |
| battery flags | a laptop must not silently stop polling on battery |

**Surviving reboots without a logon.** `-AtLogOn` means the agent starts when you log in,
so an unattended reboot leaves it down until someone signs in. If that matters, re-register
with `-LogonType Password` (which prompts for your password and stores it with the task) —
**not** `-LogonType S4U`: S4U runs without the password, and DPAPI then cannot unlock your
stored secrets, so the agent starts and every Drive call fails. If you take the `-AtLogOn`
route instead, enable Windows automatic sign-in, or just accept that a reboot needs a logon.

### 2.3 Prove it is running

```powershell
(Get-ScheduledTask -TaskName "NotebookLM relay agent").State      # Running
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*nlmtools.ops.agent*" } |
    Select-Object ProcessId, CreationDate                          # exactly ONE row
Get-Content "$env:USERPROFILE\nlm-ops-agent.log" -Tail 3           # "agent watching ... every 20s"
```

Then submit a job to itself — the same call the remote LLM will make:

```
D:\WORK\NotebookLM\.venv\Scripts\python.exe -m nlmtools.ops.client ^
  --ops-repo %USERPROFILE%\nlm-ops --project <project> status
```

A result within a poll interval or two means the whole chain works.

---

## 3. Operating it

| | |
|---|---|
| log | `%USERPROFILE%\nlm-ops-agent.log` — appended, every start and exit |
| audit | `%USERPROFILE%\nlm-ops-audit.jsonl` — one line per job, **refusals included** |
| answers | `%USERPROFILE%\nlm-answers\` — full transcripts, outside every git tree, because they quote proprietary source |
| pause | create a file named `PAUSED` in the ops repo; delete it to resume |

### Restarting it — read this before you do

```powershell
Stop-ScheduledTask -TaskName "NotebookLM relay agent"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*nlmtools.ops.agent*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName "NotebookLM relay agent"
```

⚠ **`Stop-ScheduledTask` does not reliably kill the agent.** The task runs the `.cmd`
wrapper, and the `python.exe` beneath it can survive — still polling, still on the old code
— while the task reports `Ready`. Starting the task then gives you **two agents on one
queue**, which is the one thing that breaks exactly-once. Observed here twice. Always kill
the process explicitly and count the rows before starting.

### Deactivating it — and why shutting the machine down is not that

```powershell
Unregister-ScheduledTask -TaskName "NotebookLM relay agent" -Confirm:$false
```

That also stops the running instance, unlike `Stop-ScheduledTask`; confirm no `python.exe`
remains, as in the restart block above.

⚠ **Powering the machine off does not deactivate the relay.** The trigger is *at logon*, so
a machine that is merely shut down is one boot away from polling again and picking up
whatever is in the queue. Measured: a machine off for a month resumed watching the queue
within seconds of being booted, with nobody asking it to. **Moving the relay to another
machine therefore means unregistering the task on the old one** — otherwise the first time
it boots you have two agents on one queue, which is the failure this whole section warns
about.

⚠ **The queue has no age cutoff.** A newly started agent's first pass runs *every* job in
`jobs/` with no matching result, however old — the test is "no result file", not "recent".
So after any gap in service, compare the two directories before starting, and delete stale
requests you no longer want answered:

```bash
cd ~/nlm-ops && git fetch -q origin && git reset -q --hard origin/main
comm -23 <(ls jobs/*.json | xargs -n1 basename | sort) <(ls results/*.json | xargs -n1 basename | sort)
```

While nothing is serving the queue, a caller simply waits: `client.py` times out rather
than failing fast, and the job stays pending until an agent appears.

### Health, on two separate clocks

`nlmt doctor` checks both credentials and names which one failed. They do not expire
together:

| | Lifetime | Recovery |
|---|---|---|
| NotebookLM session | hours | renewed silently; exit 11 means a human runs `.venv\Scripts\nlm.exe login` |
| Drive refresh token | **7 days** (Testing-mode OAuth app) | `powershell -f setup.ps1 -ReauthDrive` — a browser sign-in, so a human |

**Consequence for an always-on box: asking survives unattended, loading does not.** The
NotebookLM side renews itself, so questions keep being answered for weeks with nobody
present. Drive dies weekly, and `refresh` dies with it, while `ask` carries on against the
corpus that is already loaded. Plan refreshes around someone being available, and read
`doctor` before blaming the service.

---

## 4. What a complete installation looks like

| | |
|---|---|
| `D:\WORK\NotebookLmTools` | the tools and their `.venv` |
| `%USERPROFILE%\ops-agent.json` | which projects, which repos, which notebooks are allowed |
| `%USERPROFILE%\nlm-agent.cmd` | the wrapper |
| `%USERPROFILE%\nlm-ops` | clone of the private ops repo — the queue |
| `%USERPROFILE%\nlm-answers` | transcripts |
| scheduled task | `NotebookLM relay agent` |
| Credential Manager | rclone config password, Drive client id and secret |
| `%APPDATA%\rclone\rclone.conf` | encrypted; the Drive token |
| the `nlm` browser profile | the NotebookLM session |

Nothing in that list except the tools is in any repository. See
[`configuration.md`](configuration.md) for the contents of each file.

---

## 5. How an LLM asks — the transport depends on where it runs

### An LLM somewhere else (a Claude cloud VM, another machine): the git queue

This is what the service above exists for. The LLM pushes a job file to the private ops
repo and polls for the result; the agent here does the work. No inbound connection, no
credentials on the LLM's side, and GitHub's own authentication is the access control.

The LLM needs two things, both covered elsewhere:

- a clone of the ops repo — `client.py` comes with it, published by the agent so the client
  always matches the protocol ([`remote-control.md`](remote-control.md))
- a pointer in its project's `CLAUDE.md` to
  [`consulting-the-architect.md`](consulting-the-architect.md), which is the whole
  procedure: how to shape a question, how to keep the snapshot current, and what standing an
  answer has

### An LLM on this machine (Claude Desktop, Claude Code, a local agent): just call `nlmt`

It is a CLI with a machine-readable interface, which is all a local agent needs:

```
nlmt --ai                 the complete reference in one shot, written for an agent
nlmt --help --json        the interface as a JSON schema
nlmt ask --notebook "<n>" --question-file q.md --json
```

Every failure carries an exit code and a `hint` naming the corrective action, so an agent
that has read nothing can still recover. For most local setups this is strictly simpler than
MCP, and it is the same code path the queue uses.

---

## 6. MCP: where it fits, and where it must not be used

**There is no MCP server in this project.** Two things follow, and the second is the
important one.

**It could not serve a remote LLM anyway.** The MCP server that ships with the installed
`notebooklm-mcp-cli` runs over stdio, or over HTTP bound to loopback — it refuses to bind a
non-loopback address outright, and warns that it has **no authentication** of any kind. So
it can only ever serve a process on this machine. For a cloud VM the git queue is not a
workaround for missing MCP; it is the only shape that works.

⚠ **Do not register that server for corpus work.** Its tool surface contradicts the one
rule this project exists to enforce, in two specific and checkable ways:

- `source_add(source_type="file", ...)` is exactly the **local file upload** that silently
  strips function bodies out of bundled source. An agent holding that tool will reach for it,
  because it is the obvious thing to do with a local `.txt`.
- `resolve_drive_mime_type()` maps only `doc`, `slides`, `sheets` and `pdf`, and **falls back
  to the Google Doc type for anything else** — so a plain-text Drive source added through
  that path is converted to a Google Doc. A converted 1 MB file failed to import at all in
  testing. This is why these tools drive the library directly with
  `mime_type="text/plain"` instead of going through that layer.

Both failures are silent: the sources appear, the answers read fluently, the content is
gutted. That is the entire reason this project exists — see `CLAUDE.md`.

**If you do want MCP**, the safe shape is a thin stdio server wrapping *these* verbs —
`ask`, `status`, `load` — so the dangerous operations are simply not expressible, and the
plain-text and Drive-routing guarantees hold by construction. That is a small piece of work
and it does not exist yet; the CLI above covers the same ground today.

---

## 7. When it is not answering

| Symptom | Cause |
|---|---|
| no result at all | the agent is stopped, or `PAUSED` exists in the ops repo |
| a job ran twice | two agents on one queue — see the restart warning |
| `DRIVE_AUTH` / `invalid_grant` | the weekly Drive token expiry; `setup.ps1 -ReauthDrive` |
| exit 11 | the NotebookLM session could not be renewed; `nlm login`, with a human |
| `unknown project` | missing config entry, or the agent was not restarted after editing it |
| `has uncommitted changes` | someone is working in the clone the agent syncs |
| the log stops but the task says `Running` | check for the orphaned `python.exe`; the task state is not evidence |
