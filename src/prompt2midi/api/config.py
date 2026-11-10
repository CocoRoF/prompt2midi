"""Runtime settings of the web app (environment variables, with injectable clock/sleep for tests)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


def _default_web_dir() -> Path:
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "web", Path.cwd() / "web"):
        if (cand / "index.html").is_file():
            return cand
    return here.parents[3] / "web"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class AppSettings:
    data_dir: Path = field(default_factory=lambda: Path("instance"))
    secret_key: str | None = None  # P2M_SECRET_KEY: encrypts secrets.json when set
    web_dir: Path = field(default_factory=_default_web_dir)
    midi_ttl: float = 3600.0  # seconds a generated MIDI stays downloadable
    midi_max: int = 200  # most MIDI files kept at once
    session_ttl: float = 12 * 3600.0
    login_max_failures: int = 5  # per IP per window
    login_window: float = 600.0
    login_delay: float = 0.5  # seconds slept after a wrong password
    trust_proxy: bool = False  # P2M_TRUST_PROXY=1: client IP = last X-Forwarded-For entry
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls) -> "AppSettings":
        env = os.environ
        s = cls(
            data_dir=Path(env.get("P2M_DATA_DIR", "instance")),
            secret_key=env.get("P2M_SECRET_KEY") or None,
            midi_ttl=_env_float("P2M_MIDI_TTL", 3600.0),
            trust_proxy=env.get("P2M_TRUST_PROXY") == "1",
        )
        if env.get("P2M_WEB_DIR"):
            s.web_dir = Path(env["P2M_WEB_DIR"])
        return s
