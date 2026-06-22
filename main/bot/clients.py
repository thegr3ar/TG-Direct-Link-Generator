# This file is a part of TG-Direct-Link-Generator

import asyncio
import logging
from ..vars import Var
from pyrogram import Client
from pyrogram.errors import RPCError
from main.utils import TokenParser
from . import multi_clients, work_loads, StreamBot


async def initialize_clients():
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    all_tokens = TokenParser().parse_from_env()
    if not all_tokens:
        print("No additional clients found, using default client")
        return
    
    async def start_client(client_id, token):
        try:
            if len(token) >= 351:
                name=token
                bot_token=None
                print(f"Starting - Client {client_id} using Session Strings")
            else:
                name=f"client_{client_id}"
                bot_token=token
                print(f"Starting - Client {client_id} using Bot Token")
            if client_id == len(all_tokens):
                await asyncio.sleep(2)
                print("This will take some time, please wait...")

            client = Client(
                name=name,
                api_id=Var.API_ID,
                api_hash=Var.API_HASH,
                bot_token=bot_token,
                sleep_threshold=Var.SLEEP_THRESHOLD,
                no_updates=True,
                in_memory=True if len(token) < 351 else False
            )

            # Retry logic for additional clients
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await client.start()
                    break
                except RPCError as e:
                    if "msg_id" in str(e).lower() and attempt < max_retries - 1:
                        await asyncio.sleep(2)
                        continue
                    raise e

            work_loads[client_id] = 0
            return client_id, client
        except Exception:
            logging.error(f"Failed starting Client - {client_id} Error:", exc_info=True)
            return None
    
    results = await asyncio.gather(*[start_client(i, token) for i, token in all_tokens.items()])
    for res in results:
        if res:
            cid, cl = res
            multi_clients[cid] = cl

    if len(multi_clients) != 1:
        Var.MULTI_CLIENT = True
        print("Multi-Client Mode Enabled")
    else:
        print("No additional clients were initialized, using default client")
