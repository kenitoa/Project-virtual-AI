import argparse
import asyncio
import json

from virtual_ai.app import configure_console_output
from virtual_ai.health import RECOVERY, diagnose, report_data


def main():
    configure_console_output()
    parser = argparse.ArgumentParser(description="Non-speaking startup diagnostics")
    parser.add_argument("--config", default="configs/app.example.yaml")
    args = parser.parse_args()
    checks = asyncio.run(diagnose(args.config))
    print(json.dumps(report_data(checks), ensure_ascii=False, indent=2))
    return 1 if any(c.status == RECOVERY for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
