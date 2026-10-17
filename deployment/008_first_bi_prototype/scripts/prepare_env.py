"""Append missing local credentials, preserving existing passwords and values."""

import fcntl
import os
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    added = 0
    with os.fdopen(fd, "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        original = stream.read()
        present = {
            line.split("=", 1)[0].strip()
            for line in original.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }
        if original and not original.endswith("\n"):
            stream.write("\n")
        for key in (
            "SUPERSET_SECRET_KEY",
            "POSTGRES_PASSWORD",
            "SUPERSET_ADMIN_PASSWORD",
            "MYSQL_SOURCE_ROOT_PASSWORD",
            "MYSQL_SOURCE_READER_PASSWORD",
            "CRM_OWNER_PASSWORD",
            "CRM_READER_PASSWORD",
        ):
            if key not in present:
                stream.write(f"{key}={secrets.token_hex(32)}\n")
                added += 1
    print(f"Added {added} missing credentials; existing values preserved; no secrets printed.")


if __name__ == "__main__":
    main()
