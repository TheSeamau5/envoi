from .client import (
    Client,
    Session,
    connect,
    connect_session,
)
from .environment import (
    clear_environment,
    setup,
    suite,
    teardown,
    test,
)
from .utils import Documents, RunResult, run, session_path

__all__ = [
    "Client",
    "Session",
    "Documents",
    "RunResult",
    "test",
    "suite",
    "setup",
    "teardown",
    "run",
    "session_path",
    "clear_environment",
    "connect",
    "connect_session",
    "deploy",
    "hello",
]


def hello() -> None:
    print("Hello from envoi!")


def deploy(*args, **kwargs):
    from .deploy import deploy as deploy_function

    return deploy_function(*args, **kwargs)
