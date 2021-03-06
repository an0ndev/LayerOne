import io, zlib

from typing import Tuple, Any

from LayerOne.network.conn_wrapper import DummySocket, ConnectionWrapper
from LayerOne.network.utils import Utils
from LayerOne.types.common import ProtocolException
from LayerOne.types.varint import VarInt

class Packet:
    @staticmethod
    def read (conn_wrapper: ConnectionWrapper, compression_threshold: int = 0) -> (int, bytes):
        length, length_length = VarInt.read (conn_wrapper)
        if compression_threshold <= 0:
            return Utils.read_id_and_data (conn_wrapper, length)
        else:
            packet_length = length
            data_length, data_length_length = VarInt.read (conn_wrapper)
            compressed_length = packet_length - data_length_length
            if data_length == 0: # packet is uncompressed
                uncompressed_length = compressed_length
                packet = conn_wrapper.read (uncompressed_length)
            else: # data_length is the size of the uncompressed packet
                uncompressed_length = data_length
                if uncompressed_length < compression_threshold: raise ProtocolException ("Packet was sent compressed even though its length is below the compression threshold")
                compressed_packet = conn_wrapper.read (compressed_length)
                packet = zlib.decompress (compressed_packet)
                if len (packet) != uncompressed_length: raise ProtocolException ("Actual length of uncompressed packet does not match provided length")
            return Utils.read_id_and_data (packet, uncompressed_length)
    @staticmethod
    def decode_fields (data: bytes, spec: Tuple) -> (list, int):
        all_decoded = []
        total_length = 0
        remainder: bytes = data
        for spec_item in spec:
            decoded, length = spec_item.read (ConnectionWrapper (DummySocket (io.BytesIO (remainder))))
            remainder = remainder [length:]
            all_decoded.append (decoded)
            total_length += length
        if len (remainder) > 0: raise ProtocolException (f"Data was left after decoding fields: {data}")
        return all_decoded# , total_length
    @staticmethod
    def encode_fields (*natives_and_specs: Tuple [Any, Any]) -> bytes:
        all_encoded = bytearray ()
        for native, spec in natives_and_specs:
            encoded_holder = io.BytesIO ()
            spec.write (ConnectionWrapper (DummySocket (encoded_holder)), native)
            encoded = encoded_holder.getvalue ()
            for byte in encoded: all_encoded.append (byte)
        return bytes (all_encoded)
    @staticmethod
    def write (conn_wrapper: ConnectionWrapper, packet_id: int, data: bytes, compression_threshold: int = 0, force_dont_encrypt: bool = False):
        def _write_data (_data: bytes): conn_wrapper.write (_data, force_dont_encrypt = force_dont_encrypt)

        packet_id_buffer, packet_id_length = Utils.int_to_varint_buffer (packet_id)
        uncompressed_source = packet_id_buffer + data
        uncompressed_source_length = packet_id_length + len (data)

        if compression_threshold <= 0:
            length_buffer, length_length = Utils.int_to_varint_buffer (uncompressed_source_length)

            _write_data (length_buffer)
            _write_data (uncompressed_source)
        else:
            if len (data) < compression_threshold:
                data_length_buffer, data_length_length = Utils.int_to_varint_buffer (0)
                packet = packet_id_buffer + data
            else:
                data_length_buffer, data_length_length = Utils.int_to_varint_buffer (uncompressed_source_length)
                packet = zlib.compress (uncompressed_source)
            packet_length_buffer, packet_length_length = Utils.int_to_varint_buffer (data_length_length + len (packet))
            _write_data (packet_length_buffer)
            _write_data (data_length_buffer)
            _write_data (packet)
