import os
import time
from fractions import Fraction

import av
import discord
import numpy as np
from davey import MediaType
from discord.ext.voice_recv import AudioSink


def ensure_ogg_path(path: str, user_id: int) -> str:
    if os.path.isdir(path):
        filename = f"recording_{user_id}_{int(time.time())}.ogg"
        return os.path.join(path, filename)

    if not path.lower().endswith(".ogg"):
        base, _ = os.path.splitext(path)
        path = base + ".ogg"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


class DaveOggSink(AudioSink):
    def __init__(self, target_user_id: int, save_path: str, vc, channels: int = 2, rate: int = 48000, bitrate: int = 48000):
        super().__init__()
        self.target_user_id = target_user_id
        self.save_path = save_path
        self.vc = vc
        self.channels = channels
        self.rate = rate
        self.bitrate = bitrate
        self.decoder = discord.opus.Decoder()

        self.container = None
        self.stream = None
        self._samples_written = 0
        self._closed = False
        self._spoken = False
        self.start_time = None
        self.bytes_written = 0

    def _open_encoder(self):
        os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
        self.container = av.open(self.save_path, mode="w")
        self.stream = self.container.add_stream("opus", rate=self.rate)
        self.stream.layout = "stereo"
        self.stream.bit_rate = self.bitrate
        self.stream.time_base = Fraction(1, self.rate)
        if self.start_time is None:
            self.start_time = time.time()

    def wants_opus(self) -> bool:
        return True

    def write(self, user, data):
        try:
            if user is None or user.id != self.target_user_id or not getattr(data, "opus", None):
                return

            decrypted_opus = self.vc.decrypt(user.id, MediaType.audio, bytes(data.opus))
            if not decrypted_opus:
                return

            pcm_bytes = self.decoder.decode(decrypted_opus, fec=False)
            if not pcm_bytes:
                return

            if self.container is None:
                self._open_encoder()

            frame_count = len(pcm_bytes) // (2 * self.channels)
            if frame_count <= 0:
                return

            pcm_np = np.frombuffer(pcm_bytes, dtype=np.int16).reshape(-1, self.channels)
            pcm_planar = pcm_np.T.copy()

            audio_frame = av.AudioFrame.from_ndarray(pcm_planar, format="s16p", layout="stereo")
            audio_frame.sample_rate = self.rate
            audio_frame.pts = self._samples_written
            audio_frame.time_base = Fraction(1, self.rate)
            self._samples_written += frame_count

            for packet in self.stream.encode(audio_frame):
                self.container.mux(packet)
                if packet is not None and packet.size is not None:
                    self.bytes_written += int(packet.size)

            self._spoken = True
        except Exception as e:
            print(f"[DaveOggSink ERROR] {e}")

    def cleanup(self):
        if self._closed:
            return
        try:
            if self.stream is not None and self.container is not None:
                try:
                    packets = list(self.stream.encode(None))
                except Exception:
                    packets = []
                for packet in packets:
                    try:
                        self.container.mux(packet)
                    except Exception:
                        break
        finally:
            if self.container is not None:
                try:
                    self.container.close()
                except Exception as e:
                    print(f"[DaveOggSink Close ERROR] {e}")
            self.container = None
            self.stream = None
            self._closed = True

    @property
    def has_audio(self):
        return self._spoken


class MultiUserSink(AudioSink):
    def __init__(self, sinks_by_user: dict[int, DaveOggSink]):
        super().__init__()
        self._sinks = sinks_by_user
        self.start_time = None

    def add_target(self, target_user_id: int, sink: DaveOggSink):
        self._sinks[target_user_id] = sink
        if self.start_time is None:
            self.start_time = time.time()

    def has_target(self, target_user_id: int) -> bool:
        return target_user_id in self._sinks

    def write(self, user, data):
        if user is None:
            return
        sink = self._sinks.get(user.id)
        if sink is not None:
            sink.write(user, data)

    def cleanup(self):
        for sink in list(self._sinks.values()):
            sink.cleanup()

    def wants_opus(self) -> bool:
        return True

    @property
    def has_audio(self):
        return any(sink.has_audio for sink in self._sinks.values())

    @property
    def bytes_written(self):
        return sum(sink.bytes_written for sink in self._sinks.values())

    @property
    def tracked_users(self):
        return len(self._sinks)

    @property
    def saved_paths(self):
        return [sink.save_path for sink in self._sinks.values() if sink.has_audio]