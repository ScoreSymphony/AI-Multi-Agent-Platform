from __future__ import annotations

import argparse
import asyncio


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    return parser.parse_args()


async def _main(host: str, port: int) -> None:
    import websockets

    async def echo(websocket: object) -> None:
        async for message in websocket:  # type: ignore[attr-defined]
            await websocket.send(message)  # type: ignore[attr-defined]

    async with websockets.serve(echo, host, port, compression=None):
        await asyncio.Future()


if __name__ == "__main__":
    args = _args()
    asyncio.run(_main(args.host, args.port))
