import threading

import uvicorn

from engine.api import app
from engine.config import load_config
from engine.scheduler import Scheduler


def main() -> None:
    config = load_config()

    scheduler = Scheduler(config)

    api_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": config.api_host, "port": config.api_port, "log_level": "info"},
        daemon=True,
    )
    api_thread.start()

    scheduler.start()


if __name__ == "__main__":
    main()
