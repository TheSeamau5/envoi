"""Check if a sandbox is still running.

Usage: python3 check-sandbox-status.py <provider> <sandbox_id>
Output: JSON {"running": true/false, "exitCode": null/int}
"""

import json
import sys


def check_modal(sandbox_id: str) -> dict:
    import modal

    sandbox = modal.Sandbox.from_id(sandbox_id)
    exit_code = sandbox.poll()
    return {
        "running": exit_code is None,
        "exitCode": exit_code,
    }


def check_e2b(sandbox_id: str) -> dict:
    try:
        from e2b_code_interpreter import AsyncSandbox

        import asyncio

        async def check():
            try:
                sandbox = await AsyncSandbox.connect(sandbox_id)
                await sandbox.is_running()
                return {"running": True, "exitCode": None}
            except Exception:
                return {"running": False, "exitCode": None}

        return asyncio.run(check())
    except ImportError:
        return {"running": False, "exitCode": None, "error": "e2b not installed"}


def main() -> None:
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: check-sandbox-status.py <provider> <sandbox_id>"}))
        sys.exit(1)

    provider = sys.argv[1]
    sandbox_id = sys.argv[2]

    try:
        if provider == "modal":
            result = check_modal(sandbox_id)
        elif provider == "e2b":
            result = check_e2b(sandbox_id)
        else:
            result = {"running": False, "error": f"Unknown provider: {provider}"}
    except Exception as error:
        result = {"running": False, "error": str(error)}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
