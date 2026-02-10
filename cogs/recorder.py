
import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient
import time
import asyncio
from utils.sinks import AvOpusSink, ensure_ogg_path

class VoiceCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recording = False
        self.current_sink = None
        self._last_packet_time = 0
        self._status_task = None
        self.vc: VoiceRecvClient | None = None
        self._ever_received = False
        self._reconnecting = False

    def mark_packet(self):
        self._last_packet_time = time.time()
        self._ever_received = True

    async def watchdog(self, interaction):
        while self.recording:
            await asyncio.sleep(10)
            # Only attempt reconnects if we have ever received audio; otherwise it may just be silence
            if not self._ever_received:
                continue
            if time.time() - self._last_packet_time > 60:
                print("⚠️ No audio packets received, forcing VC reconnect...")
                try:
                    self._reconnecting = True
                    if not self.vc:
                        # No VC to reconnect, try to join user's channel if possible
                        if interaction.user and interaction.user.voice:
                            channel = interaction.user.voice.channel
                            self.vc = await channel.connect(cls=VoiceRecvClient, self_deaf=False, self_mute=True)
                        else:
                            continue
                    channel = self.vc.channel
                    try:
                        await self.vc.disconnect(force=True)
                    except Exception:
                        pass
                    # Reconnect with self_deaf disabled to ensure we receive audio
                    new_vc = await channel.connect(cls=VoiceRecvClient, self_deaf=False, self_mute=True)
                    self.vc = new_vc
                    if self.current_sink:
                        self.vc.listen(self.current_sink)
                    self._last_packet_time = time.time()
                except Exception as e:
                    print(f"[Watchdog ERROR] {e}")
                finally:
                    self._reconnecting = False

    async def _presence_updater(self, target_display: str):
        while self.recording and self.current_sink is not None:
            try:
                elapsed = 0
                mb = 0.0
                if self.current_sink.start_time:
                    elapsed = int(time.time() - self.current_sink.start_time)
                if self.current_sink.bytes_written:
                    mb = self.current_sink.bytes_written / (1024 * 1024)

                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                dur = f"{h:02d}:{m:02d}:{s:02d}"
                name = f"`someone yapping • {dur} • {mb:.1f} MB`"
                activity = discord.Activity(type=discord.ActivityType.listening, name=name)
                await self.bot.change_presence(activity=activity)
            except Exception:
                pass
            await asyncio.sleep(5)

    @discord.app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ You are not in a voice channel.", ephemeral=True)
        if self.vc and self.vc.is_connected():
            return await interaction.response.send_message("⚠️ Already connected to a voice channel.", ephemeral=True)

        try:
            channel = interaction.user.voice.channel
            # IMPORTANT: do not self-deafen, otherwise no inbound audio is received
            self.vc = await channel.connect(cls=VoiceRecvClient, self_deaf=False, self_mute=True)
            await interaction.response.send_message(f"🔊 Joined {channel.name}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to join voice channel: {str(e)}")

    @discord.app_commands.command(name="startrecord", description="Start recording audio from one user to a file")
    @discord.app_commands.describe(target="The user to record", path="File path or folder where to save the Ogg Opus file")
    async def startrecord(self, interaction: discord.Interaction, target: discord.Member, path: str):
        vc = self.vc
        if not vc or not isinstance(vc, VoiceRecvClient) or not vc.is_connected():
            return await interaction.response.send_message("❌ Not connected with voice receive enabled. Use /join first.", ephemeral=True)
        if self.recording:
            return await interaction.response.send_message("⚠️ Already recording.", ephemeral=True)

        try:
            path = ensure_ogg_path(path, target.id)
            sink = AvOpusSink(target.id, path, cog_ref=self)
            # Mark start immediately so duration displays even before first packet
            sink.start_time = time.time()
            vc.listen(sink)
            self.recording = True
            self.current_sink = sink
            self._last_packet_time = time.time()
            self._ever_received = False
            self.bot.loop.create_task(self.watchdog(interaction))
            await interaction.response.send_message(f"🎙️ Recording started for **{target.display_name}** → `{path}`", ephemeral=True)
            # Start lightweight live feedback updater
            self._status_task = self.bot.loop.create_task(self._presence_updater(target.display_name))
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to start recording: {str(e)}")

    @discord.app_commands.command(name="stoprecord", description="Stop recording audio")
    async def stoprecord(self, interaction: discord.Interaction):
        vc = self.vc
        # Consider 'recording' true if we still have an active sink attached
        if not self.current_sink:
            return await interaction.response.send_message("❌ I'm not recording right now.", ephemeral=True)

        try:
            if vc and vc.is_connected():
                vc.stop_listening()
            self.recording = False

            if self.current_sink and self.current_sink.has_audio:
                saved_path = self.current_sink.save_path
                self.current_sink.cleanup()
                await interaction.response.send_message(
                    f"✅ Recording stopped. Audio saved → `{saved_path}`",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message("⚠️ Nobody has spoken yet, nothing saved.", ephemeral=True)
            self.current_sink = None
            # Stop presence and clear it
            if self._status_task:
                self._status_task.cancel()
                self._status_task = None
            try:
                await self.bot.change_presence(activity=None)
            except Exception:
                pass
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to stop recording: {str(e)}")

    @discord.app_commands.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        vc = self.vc
        if vc:
            try:
                if self.recording:
                    vc.stop_listening()
                    if self.current_sink:
                        self.current_sink.cleanup()
                    self.recording = False
                    self.current_sink = None
                    if self._status_task:
                        self._status_task.cancel()
                        self._status_task = None
                    try:
                        await self.bot.change_presence(activity=None)
                    except Exception:
                        pass

                channel_name = vc.channel.name
                await vc.disconnect(force=True)
                self.vc = None
                await interaction.response.send_message(f"👋 Left {channel_name}")
            except Exception as e:
                await interaction.response.send_message(f"❌ Failed to leave channel: {str(e)}")
        else:
            await interaction.response.send_message("❌ I'm not in a voice channel.")

    @commands.Cog.listener()
    async def on_ready(self):
        # Try to recover the voice client if the bot started up and is already connected
        # (This happens if the session resumes or if we're just syncing state)
        for vc in self.bot.voice_clients:
            if isinstance(vc, VoiceRecvClient):
                self.vc = vc
                print(f"🔄 Recovered voice client connection in {vc.channel.name}")
                break

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id:
            return
            
        # If we're intentionally reconnecting, avoid clearing state here
        if self._reconnecting:
            return

        # If bot left the voice channel
        if before.channel and not after.channel:
            if self.vc and self.vc.is_connected():
                try:
                    self.vc.stop_listening()
                except Exception:
                    pass
            
            if self.recording and self.current_sink:
                self.current_sink.cleanup()
            
            if self._status_task:
                try:
                    self._status_task.cancel()
                except Exception:
                    pass
                self._status_task = None
            
            self.recording = False
            self.current_sink = None
            self.vc = None

        # If bot joined or moved channels, refresh vc reference
        elif after.channel:
            # Prefer the existing reference if still connected
            if not self.vc or not self.vc.is_connected():
                try:
                    # Pull fresh client from guild in case a system reconnect created a new instance
                    new_vc = after.channel.guild.voice_client
                    if isinstance(new_vc, VoiceRecvClient):
                        self.vc = new_vc
                except Exception:
                    pass

async def setup(bot):
    await bot.add_cog(VoiceCommands(bot))
