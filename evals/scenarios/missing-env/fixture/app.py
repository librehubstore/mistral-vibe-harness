import sys
from pathlib import Path

REQUIRED = ["DATABASE_PATH", "API_KEY"]


def load_env(path=".env"):
    env = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def main():
    env = load_env()
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        sys.exit(f"Missing required env var: {', '.join(missing)}")
    print(f"App OK (db={env['DATABASE_PATH']}, port={env.get('PORT', '8000')})")


if __name__ == "__main__":
    main()
