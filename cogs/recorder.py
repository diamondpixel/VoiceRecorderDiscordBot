import asyncio
import time

import discord
from discord.ext import commands
from discord.ext.voice_recv import VoiceRecvClient

from utils.sinks import DaveOggSink, MultiUserSink, ensure_ogg_path


class VoiceCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recording = False
        self.current_sink = None
        self.recording_sinks: dict[int, DaveOggSink] = {}
        self._status_task = None
        self.vc: VoiceRecvClient | None = None

    def _on_listen_finished(self, error):
        if error is not None:
            print(f"[Recorder] Voice receive listener stopped with error: {error!r}")

    async def _presence_updater(self):
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
                name = f"`recording {self.current_sink.tracked_users} user(s) | {dur} | {mb:.1f} MB`"
                activity = discord.Activity(type=discord.ActivityType.listening, name=name)
                await self.bot.change_presence(activity=activity)
            except Exception:
                pass
            await asyncio.sleep(5)

    @discord.app_commands.command(name="join", description="Join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            return await interaction.response.send_message("You are not in a voice channel.", ephemeral=True)
        if self.vc and self.vc.is_connected():
            return await interaction.response.send_message("Already connected to a voice channel.", ephemeral=True)

        try:
            channel = interaction.user.voice.channel
            self.vc = await channel.connect(
                cls=VoiceRecvClient,
                self_deaf=False,
                self_mute=False,
                reconnect=False,
                timeout=20.0,
            )
            if hasattr(self.vc, "set_davey"):
                self.vc.set_davey(True)
            await interaction.response.send_message(f"Joined {channel.name}")
        except Exception as e:
            await interaction.response.send_message(f"Failed to join voice channel: {str(e)}")

    @discord.app_commands.command(name="startrecord", description="Start or extend a multi-user recording session")
    @discord.app_commands.describe(target="The user to record", path="File path or folder where to save the Ogg file")
    async def startrecord(self, interaction: discord.Interaction, target: discord.Member, path: str):
        vc = self.vc
        if not vc or not isinstance(vc, VoiceRecvClient) or not vc.is_connected():
            return await interaction.response.send_message(
                "Not connected with voice receive enabled. Use /join first.",
                ephemeral=True,
            )

        try:
            path = ensure_ogg_path(path, target.id)
            sink = DaveOggSink(target.id, path, vc)

            if self.current_sink is None:
                self.recording_sinks = {}
                self.current_sink = MultiUserSink(self.recording_sinks)
            elif not isinstance(self.current_sink, MultiUserSink):
                return await interaction.response.send_message(
                    "Active recorder is in an unexpected state. Use /stoprecord and try again.",
                    ephemeral=True,
                )

            if self.current_sink.has_target(target.id):
                return await interaction.response.send_message(
                    f"{target.display_name} is already being recorded.",
                    ephemeral=True,
                )

            self.recording_sinks[target.id] = sink
            self.current_sink.add_target(target.id, sink)

            if not self.recording:
                vc.listen(self.current_sink, after=self._on_listen_finished)
                self.recording = True
                self._status_task = self.bot.loop.create_task(self._presence_updater())

            await interaction.response.send_message(
                f"Recording {target.display_name} -> `{path}`\nTracked users in this session: {self.current_sink.tracked_users}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"Failed to start recording: {str(e)}")

    @discord.app_commands.command(name="stoprecord", description="Stop recording audio")
    async def stoprecord(self, interaction: discord.Interaction):
        vc = self.vc
        if not self.current_sink:
            return await interaction.response.send_message("I'm not recording right now.", ephemeral=True)

        try:
            sink = self.current_sink
            saved_paths = list(sink.saved_paths)

            if vc and vc.is_connected():
                vc.stop_listening()
            self.recording = False

            sink.cleanup()
            self.current_sink = None
            self.recording_sinks = {}

            if self._status_task:
                self._status_task.cancel()
                self._status_task = None
            try:
                await self.bot.change_presence(activity=None)
            except Exception:
                pass

            if saved_paths:
                saved_lines = "\n".join(f"- `{saved_path}`" for saved_path in saved_paths)
                await interaction.response.send_message(
                    f"Recording stopped. Audio saved:\n{saved_lines}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("Nobody has spoken yet, nothing saved.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Failed to stop recording: {str(e)}")

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
                    self.recording_sinks = {}
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
                await interaction.response.send_message(f"Left {channel_name}")
            except Exception as e:
                await interaction.response.send_message(f"Failed to leave channel: {str(e)}")
        else:
            await interaction.response.send_message("I'm not in a voice channel.")

    @commands.Cog.listener()
    async def on_ready(self):
        for vc in self.bot.voice_clients:
            if isinstance(vc, VoiceRecvClient):
                self.vc = vc
                break

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id:
            return

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
            self.recording_sinks = {}
            self.vc = None

        elif after.channel:
            if not self.vc or not self.vc.is_connected():
                try:
                    new_vc = after.channel.guild.voice_client
                    if isinstance(new_vc, VoiceRecvClient):
                        self.vc = new_vc
                except Exception:
                    pass


async def setup(bot):
    await bot.add_cog(VoiceCommands(bot))