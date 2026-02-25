from __future__ import annotations

from pathlib import Path

from envoi_code.task_api import ResolvedTask, TaskResolveContext
from pydantic import BaseModel, ConfigDict


class PromptParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lang: str = "en"
    target: str = "x86_64-linux"
    impl_lang: str = "rust"
    milestone: str = "M0"


def resolve_prompt_file(task_dir: Path, lang: str) -> Path:
    candidates = [
        task_dir / f"prompt.{lang}.md",
        task_dir / f"prompt-{lang}.md",
        task_dir / "prompt.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No prompt template found in {task_dir}")


async def resolve_task(context: TaskResolveContext) -> ResolvedTask:
    params = PromptParams.model_validate(context.raw_params)
    task_dir = Path(context.task_dir)
    prompt_file = resolve_prompt_file(task_dir, params.lang)
    base_prompt = prompt_file.read_text().strip()

    additions = [
        "",
        "Run configuration (resolved by task.py):",
        f"- implementation_language: {params.impl_lang}",
        f"- target: {params.target}",
        f"- milestone: {params.milestone}",
        f"- prompt_language: {params.lang}",
    ]
    prompt = base_prompt + "\n" + "\n".join(additions)
    return ResolvedTask(
        prompt=prompt,
        task_params={
            "impl_lang": params.impl_lang,
            "target": params.target,
            "milestone": params.milestone,
            "lang": params.lang,
        },
        metadata={
            "prompt_file": prompt_file.name,
            "task_kind": "c_compiler",
        },
    )
