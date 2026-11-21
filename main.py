import asyncio
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

import bot as b


async def health(request):
    return web.Response(text="ok")


async def run():
    b.init_db()
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    tg = Bot(b.TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(b.router)
    asyncio.create_task(b.rent_watcher())
    await dp.start_polling(tg)


asyncio.run(run())
