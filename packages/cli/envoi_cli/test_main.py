from envoi_cli.main import normalize_code_argv


def test_normalize_code_path_shorthand() -> None:
    argv = ["code", "examples/gameboy_emulator", "--max-parts", "5"]
    assert normalize_code_argv(argv) == [
        "code",
        "run",
        "--example",
        "examples/gameboy_emulator",
        "--max-parts",
        "5",
    ]


def test_normalize_code_path_with_trailing_slash() -> None:
    argv = ["code", "examples/gameboy_emulator/", "--max-parts", "5"]
    assert normalize_code_argv(argv) == [
        "code",
        "run",
        "--example",
        "examples/gameboy_emulator/",
        "--max-parts",
        "5",
    ]


def test_normalize_code_graph_passthrough() -> None:
    argv = ["code", "graph", "trajectory-123"]
    assert normalize_code_argv(argv) == argv


def test_normalize_code_run_with_path() -> None:
    argv = ["code", "run", "examples/gameboy_emulator"]
    assert normalize_code_argv(argv) == [
        "code",
        "run",
        "--example",
        "examples/gameboy_emulator",
    ]


def test_normalize_code_option_head_passthrough() -> None:
    argv = ["code", "--example", "examples/gameboy_emulator"]
    assert normalize_code_argv(argv) == argv
