
import os
import time
import asyncio
import av
import numpy as np
from fractions import Fraction
from discord.ext.voice_recv import AudioSink

# -------- Utility --------
def ensure_ogg_path(path: str, user_id: int) -> str:
    """
    Ensure that the provided path is a valid .ogg (Opus-in-Ogg) file path.
    - If it's a directory, auto-generate a filename.
    - If it doesn't end with .ogg, append one.
    """
    if os.path.isdir(path):
        filename = f"recording_{user_id}_{int(time.time())}.ogg"
        return os.path.join(path, filename)

    if not path.lower().endswith(".ogg"):
        base, _ = os.path.splitext(path)
        path = base + ".ogg"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


# -------- Sink class for saving Opus --------
class OpusSink(AudioSink):
    def __init__(self, target_user_id: int, save_path: str, cog_ref=None):
        super().__init__()
        self._spoken = False
        self.target_user_id = target_user_id
        self.save_path = save_path
        self.file = None
        self.cog_ref = cog_ref

    def write(self, user, data):
        try:
            # Mark any inbound packet for watchdog heartbeat
            if self.cog_ref:
                self.cog_ref.mark_packet()

            if user.id != self.target_user_id:
                return

            self._spoken = True
            if self.file is None:
                os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
                self.file = open(self.save_path, "wb")  # store raw opus frames

            self.file.write(data.opus)

        except Exception as e:
            print(f"[OpusSink ERROR] {e}")

    def cleanup(self):
        if self.file:
            try:
                self.file.close()
            except Exception as e:
                print(f"[OpusSink Cleanup ERROR] {e}")
            self.file = None

    def wants_opus(self) -> bool:
        return True  # request opus frames from discord

    @property
    def has_audio(self):
        return self._spoken


# -------- Sink class for saving Opus-in-Ogg via PyAV --------
class AvOpusSink(AudioSink):
    def __init__(self, target_user_id: int, save_path: str, cog_ref=None, channels: int = 2, rate: int = 48000, bitrate: int = 48000):
        super().__init__()
        self._spoken = False
        self.target_user_id = target_user_id
        self.save_path = save_path
        self.cog_ref = cog_ref
        self.channels = channels
        self.rate = rate
        self.bitrate = bitrate  # in bits per second

        self.container = None
        self.stream = None
        self._samples_written = 0
        self._closed = False
        self.start_time = None
        self.bytes_written = 0
        self.packets_written = 0

    def _open_encoder(self):
        os.makedirs(os.path.dirname(self.save_path), exist_ok=True)
        self.container = av.open(self.save_path, mode="w")
        self.stream = self.container.add_stream("opus", rate=self.rate)
        self.stream.layout = "stereo" if self.channels == 2 else "mono"
        self.stream.bit_rate = self.bitrate
        # Use 1/sample_rate time base for stable long recordings
        self.stream.time_base = Fraction(1, self.rate)
        if self.start_time is None:
            self.start_time = time.time()

    def write(self, user, data):
        try:
            # Mark any inbound packet for watchdog heartbeat
            if self.cog_ref:
                self.cog_ref.mark_packet()

            if user is None or user.id != self.target_user_id:
                return

            if self.container is None:
                # Auto-detect channels from first buffer length
                # 2 bytes per sample per channel. If divisible by 4 -> likely stereo, else if divisible by 2 -> mono
                try:
                    if (len(data.pcm) % 4) == 0:
                        self.channels = 2
                    elif (len(data.pcm) % 2) == 0:
                        self.channels = 1
                    else:
                        # Fallback to mono if unexpected size
                        self.channels = 1
                except Exception:
                    self.channels = 1
                self._open_encoder()

            self._spoken = True

            # Interpret incoming PCM as int16 little-endian, interleaved
            frame_count = len(data.pcm) // (2 * self.channels)
            if frame_count == 0:
                return
            # First parse as (samples, channels)
            pcm_np = np.frombuffer(data.pcm, dtype=np.int16).reshape(-1, self.channels)
            # Convert to planar (channels, samples) and ensure C-contiguous
            pcm_planar = pcm_np.T.copy()

            # Provide planar int16 data: shape (channels, samples), use format s16p
            audio_frame = av.AudioFrame.from_ndarray(pcm_planar, format="s16p", layout=("stereo" if self.channels == 2 else "mono"))
            audio_frame.sample_rate = self.rate
            audio_frame.pts = self._samples_written
            audio_frame.time_base = Fraction(1, self.rate)
            self._samples_written += frame_count

            for packet in self.stream.encode(audio_frame):
                self.container.mux(packet)
                # Track output size for feedback
                if packet is not None and packet.size is not None:
                    self.bytes_written += int(packet.size)
                self.packets_written += 1

        except Exception as e:
            print(f"[AvOpusSink ERROR] {e}")

    def cleanup(self):
        if self._closed:
            return
        try:
            if self.stream is not None and self.container is not None:
                try:
                    packets = list(self.stream.encode(None))
                except Exception:
                    # Expected when flushing at end; ignore
                    packets = []
                # Mux any remaining packets if container still open
                for packet in packets:
                    if self.container is None:
                        break
                    try:
                        self.container.mux(packet)
                    except Exception:
                        # Ignore mux errors during shutdown
                        break
        finally:
            if self.container is not None:
                try:
                    self.container.close()
                except Exception as e:
                    print(f"[AvOpusSink Close ERROR] {e}")
            self.container = None
            self.stream = None
            self._closed = True

    def wants_opus(self) -> bool:
        # Receive decoded PCM from the library to encode with PyAV
        return False

    @property
    def has_audio(self):
        return self._spoken
