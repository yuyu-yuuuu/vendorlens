"""Initialize private local credentials; preserve existing model configuration."""

import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def read_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        key.strip(): value.strip().strip("\"'")
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }


def update(path: Path, values: dict[str, str]) -> None:
    text = path.read_text() if path.exists() else ""
    for key, value in values.items():
        pattern = rf"(?m)^{re.escape(key)}=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _, replacement=replacement: replacement, text)
        else:
            text = text.rstrip("\n") + "\n" + replacement + "\n"
    # Set permissions before writing credentials, including for newly created files.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(text)


def initialize(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        env.touch(mode=0o600)
        env.write_text((root / ".env.example").read_text())
    current = read_values(env)
    keys = (
        "VENDORLENS_POSTGRES_PASSWORD",
        "VENDORLENS_REDIS_PASSWORD",
        "VENDORLENS_API_ACCESS_KEY",
    )
    generated = {key: current.get(key) or secrets.token_hex(32) for key in keys}
    values = dict(generated)
    # Replace only this project's default local dependency URLs; preserve external databases.
    for key, password_key, default_url in (
        (
            "VENDORLENS_DATABASE_URL",
            "VENDORLENS_POSTGRES_PASSWORD",
            "postgresql+psycopg://vendorlens@127.0.0.1:5433/vendorlens",
        ),
        ("VENDORLENS_REDIS_URL", "VENDORLENS_REDIS_PASSWORD", "redis://127.0.0.1:6380/0"),
    ):
        url = urlsplit(current.get(key) or default_url)
        if url.hostname in {"postgres", "redis", "localhost", "127.0.0.1"}:
            host = "127.0.0.1"
            port = 5433 if key == "VENDORLENS_DATABASE_URL" else 6380
            username = url.username or ("vendorlens" if port == 5433 else "")
            authority = f"{username}:{generated[password_key]}@{host}:{port}"
            values[key] = urlunsplit((url.scheme, authority, url.path, url.query, url.fragment))
    if current.get("VENDORLENS_CHROMA_URL") == "http://chroma:8000":
        values["VENDORLENS_CHROMA_URL"] = "http://127.0.0.1:8100"
    if current.get("VENDORLENS_STORAGE_ROOT") == "/data/files":
        values["VENDORLENS_STORAGE_ROOT"] = "data/files"
    update(env, values)
    update(
        root / "apps/web/.env.local",
        {
            "VENDORLENS_API_ACCESS_KEY": generated["VENDORLENS_API_ACCESS_KEY"],
            "VENDORLENS_API_URL": read_values(root / "apps/web/.env.local").get(
                "VENDORLENS_API_URL", "http://127.0.0.1:8000"
            ),
        },
    )


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1])
    print("Local credentials initialized in ignored .env files (permissions 600).")
    print("Existing PostgreSQL volumes need their role password updated before reuse.")
