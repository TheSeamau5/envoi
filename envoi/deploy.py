from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from .constants import DEFAULT_IMAGE_NAME, DEFAULT_PORT


def deploy(
    path: str = ".",
    *,
    module: str | None = None,
    port: int = DEFAULT_PORT,
    image_name: str = DEFAULT_IMAGE_NAME,
    container_name: str | None = None,
    build: bool = True,
    detach: bool = True,
) -> dict[str, Any]:
    target_path = Path(path).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Environment path not found: {target_path}")

    environment_dir, module_filename = resolve_environment_target(
        target_path=target_path,
        module=module,
    )

    project_root = Path(__file__).resolve().parent.parent

    if build:
        build_runtime_image(project_root, image_name, environment_dir)

    environment_mount = f"{environment_dir}:/environment:ro"
    environment_file = f"/environment/{module_filename}"
    runtime_url = f"http://localhost:{port}"

    run_command = ["docker", "run", "--rm"]
    if detach:
        run_command.append("-d")
    if container_name:
        run_command.extend(["--name", container_name])

    run_command.extend(["-p", f"{port}:{port}"])
    run_command.extend(["-v", "/tmp/envoi-sessions:/tmp"])
    run_command.extend(["-v", environment_mount])
    run_command.append(image_name)
    run_command.extend(["python3", "-m", "envoi.runtime"])
    run_command.extend(["--file", environment_file])
    run_command.extend(["--host", "0.0.0.0"])
    run_command.extend(["--port", str(port)])

    if detach:
        result = run_command_checked(run_command, capture_output=True)
        return {
            "container_id": result.stdout.strip(),
            "container_name": container_name,
            "image": image_name,
            "url": runtime_url,
        }

    run_command_checked(run_command)
    return {
        "container_id": None,
        "container_name": container_name,
        "image": image_name,
        "url": runtime_url,
    }


def build_runtime_image(project_root: Path, image_name: str, environment_dir: Path) -> None:
    custom_dockerfile = environment_dir / "Dockerfile"
    if custom_dockerfile.is_file():
        build_command = [
            "docker",
            "build",
            "-t",
            image_name,
            "-f",
            str(custom_dockerfile),
            str(project_root),
        ]
        run_command_checked(build_command)
        return

    base_dockerfile = project_root / "envoi" / "Dockerfile.base"
    if not base_dockerfile.is_file():
        raise FileNotFoundError(f"Missing base Dockerfile: {base_dockerfile}")

    build_command = ["docker", "build"]
    build_command.extend(["-t", image_name])
    build_command.extend(["-f", str(base_dockerfile)])
    build_command.append(str(project_root))
    run_command_checked(build_command)


def run_command_checked(command: list[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m envoi.deploy")
    parser.add_argument(
        "--path",
        default=".",
        help="Path to environment folder or environment Python file (default: .)",
    )
    parser.add_argument(
        "--module",
        default=None,
        help="When --path is a folder, Python module filename to run (for example: polish_notation.py)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--image", default=DEFAULT_IMAGE_NAME)
    parser.add_argument("--name", default=None)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    args = parser.parse_args()

    result = deploy(
        path=args.path,
        module=args.module,
        port=args.port,
        image_name=args.image,
        container_name=args.name,
        build=not args.no_build,
        detach=not args.foreground,
    )
    if result["container_id"]:
        print(f"Started container: {result['container_id']}")
    print(f"Runtime URL: {result['url']}")

def resolve_environment_target(
    *,
    target_path: Path,
    module: str | None,
) -> tuple[Path, str]:
    if target_path.is_file():
        return target_path.parent, target_path.name

    if not target_path.is_dir():
        raise ValueError(f"Expected file or directory path, got: {target_path}")

    if module:
        explicit_module = target_path / module
        if not explicit_module.is_file():
            raise FileNotFoundError(
                f"Module '{module}' was not found in environment folder: {target_path}"
            )
        return target_path, explicit_module.name

    default_module = target_path / "environment.py"
    if default_module.is_file():
        return target_path, default_module.name

    candidates = sorted(
        file.name
        for file in target_path.iterdir()
        if file.is_file()
        and file.suffix == ".py"
        and file.name != "__init__.py"
    )
    if len(candidates) == 1:
        return target_path, candidates[0]

    if not candidates:
        raise ValueError(
            "No Python environment module found in folder. "
            "Pass --module <filename.py>."
        )

    raise ValueError(
        "Multiple Python modules found in folder. "
        "Pass --module <filename.py>. "
        f"Candidates: {', '.join(candidates)}"
    )


if __name__ == "__main__":
    main()
