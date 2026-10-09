from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


LogCallback = Callable[[str], None]


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


class SumoNotFoundError(RuntimeError):
    pass


class SumoRunner:
    TOOLS = ("netconvert", "od2trips", "duarouter", "sumo-gui")

    def __init__(self, sumo_bin: str | Path | None = None, log: LogCallback | None = None):
        self.sumo_bin = Path(sumo_bin) if sumo_bin else None
        self.log = log or (lambda _: None)

    def executable(self, name: str) -> str:
        exe = f"{name}.exe" if os.name == "nt" else name
        if self.sumo_bin:
            candidate = self.sumo_bin / exe
            if candidate.exists():
                return str(candidate)
        return exe

    def check_tools(self) -> list[str]:
        missing: list[str] = []
        for tool in self.TOOLS:
            try:
                self._run_version(tool)
            except (FileNotFoundError, RuntimeError):
                missing.append(tool)
        return missing

    def _run_version(self, tool: str) -> None:
        exe = self.executable(tool)
        completed = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"{tool} unavailable")

    def run(self, args: Sequence[str], cwd: Path | None = None) -> CommandResult:
        command = list(args)
        self.log("$ " + " ".join(self._quote(x) for x in command))

        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            universal_newlines=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        output_lines: list[str] = []
        if process.stdout is not None:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    output_lines.append(line)
                    self.log(line)

        returncode = process.wait()
        output = "\n".join(output_lines)
        return CommandResult(command, returncode, output, "")

    @staticmethod
    def _quote(value: str) -> str:
        return f'"{value}"' if " " in value else value
