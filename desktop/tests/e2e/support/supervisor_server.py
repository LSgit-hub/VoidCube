"""Minimal Supervisor process used by Playwright browser tests."""

from __future__ import annotations

import argparse
import asyncio
from typing import Optional

from voidcube.systems.supervisor.config_models import SupervisorConfig
from voidcube.systems.supervisor.supervisor import Supervisor


class PlaywrightSupervisor(Supervisor):
    async def register_with_gateway(self) -> Optional[str]:
        return None

    async def _start_periodic_tasks(self) -> None:
        return None

    async def _stop_periodic_tasks(self) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6002)
    args = parser.parse_args()
    config = SupervisorConfig(
        host=args.host,
        port=args.port,
        ui_auto_open=False,
    )
    asyncio.run(PlaywrightSupervisor(config).start())


if __name__ == "__main__":
    main()
