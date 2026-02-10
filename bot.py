
import discord
from discord.ext import commands

class VoiceRecordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        print("Setting up bot...")
        # Load cogs here using extensions
        await self.load_extension("cogs.recorder")
        print("Commands added to bot")

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        print(f"Bot ID: {self.user.id}")
        print(f"Connected to {len(self.guilds)} server(s)")
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s)")
            for cmd in synced:
                print(f"  - /{cmd.name}: {cmd.description}")
        except discord.HTTPException as e:
            print(f"HTTP error syncing commands: {e}")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    async def on_application_command_error(self, interaction: discord.Interaction, error):
        print(f"Command error: {error}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ An error occurred while processing your command.", ephemeral=True)
            except:
                pass
