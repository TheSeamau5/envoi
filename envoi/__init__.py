from .client import (
    Client,
    Session,
    connect,
    connect_session,
)
from .environment import (
    action,
    clear_environment,
    observe,
    setup,
    teardown,
    test,
)
from .utils import Documents, RunResult, run

__all__ = [
    "Client",
    "Session",
    "Documents",
    "RunResult",
    "test",
    "setup",
    "teardown",
    "observe",
    "action",
    "run",
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
