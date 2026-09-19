"""Runtime settings: paths and environment (see DESIGN.md §2).

Everything lives under ``X32MCP_HOME`` (default: the repo root, i.e. the parent of ``src/``)
so the same layout works on Windows and under Termux. No Windows-specific APIs here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    # src/x32mcp/config.py -> src/x32mcp -> src -> repo root
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Settings:
    """Immutable view of the environment. Build with :meth:`Settings.from_env`."""

    home: Path
    device_yaml: Path
    snapshot_dir: Path
    report_dir: Path
    patch_dir: Path
    webui_dir: Path
    x32_host: str | None
    x32_port: int
    dash_host: str
    dash_port: int
    dash_enabled: bool
    log_level: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        home = Path(e.get("X32MCP_HOME") or _repo_root()).expanduser().resolve()
        device_yaml = Path(e.get("X32MCP_DEVICE_YAML") or (home / "device.yaml"))
        host = (e.get("X32_HOST") or "").strip() or None

        def _int(name: str, default: int) -> int:
            raw = e.get(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _bool(name: str, default: bool) -> bool:
            raw = e.get(name)
            if raw is None or raw.strip() == "":
                return default
            return raw.strip().lower() not in {"0", "false", "no", "off"}

        return cls(
            home=home,
            device_yaml=device_yaml,
            snapshot_dir=home / "snapshots",
            report_dir=home / "ringout_reports",
            patch_dir=home / "patches",
            webui_dir=home / "webui",
            x32_host=host,
            x32_port=_int("X32_PORT", 10023),
            dash_host=(e.get("X32MCP_DASH_HOST") or "0.0.0.0").strip(),
            dash_port=_int("X32MCP_DASH_PORT", 8032),
            dash_enabled=_bool("X32MCP_DASH", True),
            log_level=(e.get("X32MCP_LOG") or "INFO").strip().upper(),
        )

    def ensure_dirs(self) -> None:
        for p in (self.snapshot_dir, self.report_dir, self.patch_dir):
            p.mkdir(parents=True, exist_ok=True)
