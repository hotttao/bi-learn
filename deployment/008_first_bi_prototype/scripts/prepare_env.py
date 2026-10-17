"""Generate ignored local credentials without overwriting an existing file."""

import os
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(".env already exists; left unchanged.")
        return
    with os.fdopen(fd, "w") as stream:
        for key in (
            "SUPERSET_SECRET_KEY",
            "POSTGRES_PASSWORD",
            "SUPERSET_ADMIN_PASSWORD",
        ):
            stream.write(f"{key}={secrets.token_hex(32)}\n")
    print("Created .env with mode 0600; credentials were not printed.")


if __name__ == "__main__":
    main()
