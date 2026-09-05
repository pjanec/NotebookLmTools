"""Credential access (design.md 4.3).

Nothing sensitive is stored in plaintext on disk and nothing sensitive is ever written
inside the repository. The rclone config file is encrypted; its password lives in Windows
Credential Manager (DPAPI, bound to this Windows account) and reaches rclone only through
an environment variable on the child process.

The one rule this module exists to enforce: a secret is fetched at the moment it is
needed, handed to exactly one child process, and never logged, printed, or placed in the
JSON envelope.
"""

from __future__ import annotations

SERVICE = "NotebookLmTools"
RCLONE_PASSWORD_ACCOUNT = "rclone-config-password"


def _keyring():
    try:
        import keyring
    except ImportError as error:  # pragma: no cover - environment problem
        from .exits import INTERNAL, ToolError

        raise ToolError(
            INTERNAL,
            "the keyring package is not installed",
            hint="run setup.ps1 to repair the virtual environment",
        ) from error
    return keyring


def get(account: str) -> str | None:
    return _keyring().get_password(SERVICE, account)


def set(account: str, value: str) -> None:  # noqa: A001 - deliberate symmetry with get
    _keyring().set_password(SERVICE, account, value)


def delete(account: str) -> None:
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, account)
    except Exception:  # noqa: BLE001 - absence is not an error
        pass


def rclone_password() -> str:
    """The rclone config password, or a legible failure if setup has not run."""
    from .exits import DRIVE_AUTH, ToolError

    password = get(RCLONE_PASSWORD_ACCOUNT)
    if not password:
        raise ToolError(
            DRIVE_AUTH,
            "no rclone config password found in Windows Credential Manager",
            hint="run setup.ps1 to configure Google Drive access on this machine",
            detail={"service": SERVICE, "account": RCLONE_PASSWORD_ACCOUNT},
        )
    return password


def rclone_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for an rclone child process, carrying the config password.

    Returned for immediate use in one `subprocess` call. Never log this dict, never merge
    it into anything that gets serialised, and never put it in the envelope.
    """
    import os

    env = dict(base if base is not None else os.environ)
    env["RCLONE_CONFIG_PASS"] = rclone_password()
    return env
