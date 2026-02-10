
import struct
import binascii
from discord.ext.voice_recv import rtp
from discord.ext.voice_recv.opus import PacketDecoder

def _parse_bede_header_patch(self, data: bytes, length: int) -> None:
    # RFC 5285: length is a 16-bit count of 32-bit words, excluding the header
    end_offset = 4 + (length * 4)
    offset = 4

    while offset < end_offset:
        if offset >= len(data):
            break

        next_byte = data[offset : offset + 1]
        
        # Padding
        if next_byte == b'\x00':
            offset += 1
            continue

        header = next_byte[0]
        
        element_id = header >> 4
        element_len = 1 + (header & 0b0000_1111)

        if offset + 1 + element_len > end_offset:
            break

        self.extension_data[element_id] = data[offset + 1 : offset + 1 + element_len]
        offset += 1 + element_len

_original_decode_packet = PacketDecoder._decode_packet

def _decode_packet_debug(self, packet):
    # Only decode if it looks like Opus (payload type dynamic, usually 120 or similar for Discord)
    # However, sometimes Discord changes this.
    # We will log the payload type and SSRC first.
    pt = getattr(packet, 'payload', -1)
    
    # Common payload types:
    # 50, 51: RTCP (though should be handled separately)
    # 100-110: Video (VP8/H264)
    # 120: Opus Audio
    
    if pt != 120 and pt != -1:
         # Return empty bytes for non-opus
         return packet, None # Skip decoding entirely
         
    try:
        return _original_decode_packet(self, packet)
    except Exception as e:
        print(f"[DEBUG] Decode Error in ssrc {self.ssrc} (pt={pt}): {e}")
        # Log minimal info on further errors
        raise e

def apply_patches():
    print("[Patching] Applying RTP Packet monkeypatch...")
    rtp.RTPPacket._parse_bede_header = _parse_bede_header_patch
    
    print("[Patching] Applying Opus Decoder monkeypatch...")
    PacketDecoder._decode_packet = _decode_packet_debug
