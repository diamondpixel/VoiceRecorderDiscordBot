import discord.opus
from discord.voice_client import VoiceClient
from discord.ext.voice_recv.opus import PacketDecoder


_original_decode_packet = PacketDecoder._decode_packet
_logged_ssrcs: set[int] = set()


def _voiceclient_decrypt(self, usr_id: int, mtype, data: bytes) -> bytes:
    return self._connection.dave_session.decrypt(usr_id, mtype, data)


def _voiceclient_set_davey(self, val: bool) -> None:
    self._connection.dave_session.set_passthrough_mode(val, 10)


def _decode_packet_resilient(self, packet):
    try:
        return _original_decode_packet(self, packet)
    except discord.opus.OpusError as e:
        error_text = str(e).lower()
        if "corrupted stream" in error_text or "invalid argument" in error_text:
            ssrc = getattr(self, "ssrc", -1)
            if ssrc not in _logged_ssrcs:
                print(f"[Patching] Dropping bad opus packet for ssrc={ssrc}: {e}")
                _logged_ssrcs.add(ssrc)
            return packet, None
        raise


def apply_patches():
    print("[Patching] Applying resilient Opus decoder patch...")
    PacketDecoder._decode_packet = _decode_packet_resilient
    print("[Patching] Applying VoiceClient DAVE helpers...")
    VoiceClient.decrypt = _voiceclient_decrypt
    VoiceClient.set_davey = _voiceclient_set_davey