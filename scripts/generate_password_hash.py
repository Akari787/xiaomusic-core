from __future__ import annotations

import argparse
import getpass

import bcrypt


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate bcrypt password hash")
    parser.add_argument("--password", help="Plaintext password; if omitted, prompt securely")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise SystemExit("Password cannot be empty")

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    print(hashed.decode("utf-8"))


if __name__ == "__main__":
    main()
