
import asyncio
import os
from dotenv import load_dotenv
from bot import VoiceRecordBot
from utils.patching import apply_patches

# Apply critical fixes before anything else starts
apply_patches()

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("No DISCORD_TOKEN found in .env file")

async def main():
    bot = VoiceRecordBot()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Avoid huge traceback on Ctrl+C
        pass
