import hashlib
import json
import secrets
import socket
import threading
import traceback
from threading import Thread
from typing import Tuple, Optional, Any

import colorama
import requests
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.serialization import load_der_public_key

from LayerOne.extra.handler import Handler
from LayerOne.network.conn_wrapper import ConnectionWrapper
from LayerOne.network.packet import Packet
from LayerOne.types.byte_array import ByteArray
from LayerOne.types.common import ProtocolException
from LayerOne.types.native import UShort
from LayerOne.types.string import String
from LayerOne.types.varint import VarInt

Host = Tuple [str, int] # IP, port
ProxyAuth = Tuple [str, str] # UUID, access token

class Server:
    def __init__ (self, quiet: bool = False, host: Host = ("0.0.0.0", 25566), proxy: bool = True, proxy_target: Optional [Host] = ("mc.hypixel.net", 25565), proxy_auth: Optional [ProxyAuth] = None, handler_class: type (Handler) = None):
        self.quiet = quiet
        if not quiet: colorama.init ()

        self.proxy = proxy
        if proxy:
            if proxy_target is None: raise Exception ("target (ip, port) tuple is required when proxy is enabled")
            self.proxy_target = proxy_target
            if proxy_auth is None: raise Exception ("authentication (uuid, access_token) tuple is required when proxy is enabled")
            self.proxy_auth = proxy_auth
        self.handler_class = handler_class
        server = socket.socket (family = socket.AF_INET, type = socket.SOCK_STREAM)
        server.setsockopt (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind (host)
        server.listen ()
        while True:
            connection, address = server.accept ()
            Thread (target = self.handler_serverbound, args = (connection, address)).start ()
    def handler_serverbound (self, native_client_connection: socket.socket, address):
        log_lock = threading.Lock ()
        def c2s_print (message: Any, generic: bool = True, meta: bool = False, force: bool = False) -> None:
            if self.quiet and not force: return
            if generic:
                if meta:
                    out = f"{colorama.Style.DIM}<-- ### {colorama.Fore.GREEN}{message}{colorama.Fore.RESET}{colorama.Style.NORMAL}"
                else:
                    out = f"<-- {colorama.Fore.GREEN}{message}{colorama.Fore.RESET}"
            else:
                out = f"{colorama.Style.BRIGHT}<-- {colorama.Fore.GREEN}s{current_state ['id']} id{hex (packet_id)} {message}{colorama.Fore.RESET}{colorama.Style.NORMAL}"
            with log_lock: print (out)
        def force_c2s_print (*args, **kwargs): c2s_print (*args, **kwargs, force = True)

        c2s_print ("connected", meta = True)
        handler_instance: Optional [Handler] = self.handler_class () if self.handler_class is not None else None

        client_connection = ConnectionWrapper (native_client_connection)

        native_server_connection = socket.socket (family = socket.AF_INET, type = socket.SOCK_STREAM)
        native_server_connection.connect (self.proxy_target)
        server_connection = ConnectionWrapper (native_server_connection)

        current_state = {
            "id": 0,
            "in_login": False,
            "encryption_key": None,
            "compression_threshold": -1
        }
        if self.proxy:
            clientbound_handler_thread = Thread (target = self.handler_clientbound, args = (log_lock, current_state, client_connection, server_connection, handler_instance))
            clientbound_handler_thread.start ()

            def to_client (_packet_id, _data):
                Packet.write (client_connection, _packet_id, _data)
            def to_server (_packet_id, _data):
                Packet.write (server_connection, _packet_id, _data,
                              compression_threshold = current_state ["compression_threshold"])
        else:
            packet_number = 0
            in_status = False

        while True:
            try:
                packet_id, data = Packet.read (client_connection)
                c2s_print (f"data {Server._buffer_to_str (data)}", generic = False)

                if self.proxy:
                    def pass_through (): to_server (packet_id, data)

                    if current_state ["id"] == 0: # Handshaking, here we just need to read the next state to stay up to date
                        if packet_id != 0x00: raise ProtocolException (f"Unrecognized packet ID {packet_id} in Handshaking state")
                        proto_ver, server_addr, server_port, next_state = Packet.decode_fields (data, [VarInt, String, UShort, VarInt])
                        c2s_print (f"proxy detected handshake, switching to state {next_state}")
                        current_state ["id"] = next_state
                        pass_through ()
                    elif current_state ["id"] == 1: # Status, we don't need any special behavior here
                        pass_through ()
                    elif current_state ["id"] == 2: # Login, we need to handle encryption + compression setup
                        if packet_id == 0x00:
                            c2s_print (f"proxy detected login start")
                            current_state ["in_login"] = True
                            pass_through ()
                        else:
                            raise ProtocolException (f"Unhandled login packet ID {packet_id}")
                    elif current_state ["id"] == 3: # Play, just pass through packets
                        if handler_instance is not None:
                            c2s_print ("calling handler for generic play packet")
                            should_pass_through = handler_instance.client_to_server (current_state, force_c2s_print, to_client, to_server, packet_id, data)
                            if should_pass_through:
                                pass_through ()
                        else:
                            c2s_print ("passing through generic play packet")
                            pass_through ()
                    else:
                        raise ProtocolException (f"Unknown state {current_state ['id']}")
                    continue

                if packet_number == 0:
                    # handshake
                    assert packet_id == 0, f"packet {packet_number} is not handshake reeeeeeeeee"
                    handshake = Packet.decode_fields (data, [VarInt, String, UShort, VarInt])
                    c2s_print (f"handshake boi {handshake}")
                    next_state = handshake [3]
                    if next_state == 1:
                        # raise ProtocolException ("status request is gay not happening")
                        in_status = True
                    else:
                        # raise ProtocolException ("login not implemented")
                        c2s_print ("login not implemented")
                        break
                        # print ("ALR WE LOGGING IN LETS GO BOIS")
                        # encoded_fields = Packet.encode_fields (*(list (zip (handshake, [VarInt, String, UShort, VarInt]))))
                        # print (f"Encoded fields: {encoded_fields}")
                elif packet_number == 1:
                    if in_status:
                        # request packet
                        assert packet_id == 0, f"packet {packet_number} is not request reeeee"
                        response_json = {
                            "version": {
                                "name": "1.8.7",
                                "protocol": 47
                            },
                            "players": {
                                "max": 100,
                                "online": 5,
                                "sample": [
                                    {
                                        "name": "thinkofdeath",
                                        "id": "4566e69f-c907-48ee-8d71-d7ba5aa00d20"
                                    }
                                ]
                            },
                            "description": {
                                "text": "Hello world"
                            }
                        }
                        response_data = Packet.encode_fields ((json.dumps (response_json), String))
                        Packet.write (client_connection, 0, response_data)
                else:
                    if in_status:
                        # ping packet
                        assert packet_id == 1, f"packet {packet_number} is not ping reeeee"
                        Packet.write (client_connection, 1, data)
                packet_number += 1
            except EOFError:
                c2s_print ("eof", meta = True)
                break
            except ConnectionResetError:
                c2s_print ("reset", meta = True)
                break
            except BrokenPipeError:
                c2s_print ("broken pipe (exception in other direction?)", meta = True)
                break
            except:
                traceback.print_exc ()
                c2s_print ("other error", meta = True)
                break
        client_connection.ensure_closed ()
        if self.proxy:
            server_connection.ensure_closed ()
            clientbound_handler_thread.join ()
        if current_state ["id"] == 3 and handler_instance is not None: handler_instance.disconnected ()
        if not self.quiet: colorama.deinit ()
    def handler_clientbound (self, log_lock: threading.Lock, current_state: dict, client_connection: ConnectionWrapper, server_connection: ConnectionWrapper, handler_instance: Handler):
        def s2c_print (message: Any, generic: bool = True, meta: bool = False, force: bool = False) -> None:
            if self.quiet and not force: return
            if generic:
                if meta:
                    out = f"{colorama.Style.DIM}--> ### {colorama.Fore.RED}{message}{colorama.Fore.RESET}{colorama.Style.NORMAL}"
                else:
                    out = f"--> {colorama.Fore.RED}{message}{colorama.Fore.RESET}"
            else:
                out = f"{colorama.Style.BRIGHT}--> {colorama.Fore.RED}s{current_state ['id']} id{hex (packet_id)} {message}{colorama.Fore.RESET}{colorama.Style.NORMAL}"
            with log_lock: print (out)
        def force_s2c_print (*args, **kwargs): s2c_print (*args, **kwargs, force = True)

        s2c_print ("connected", meta = True)

        def to_client (_packet_id, _data):
            Packet.write (client_connection, _packet_id, _data)
        def to_server (_packet_id, _data):
            Packet.write (server_connection, _packet_id, _data,
                          compression_threshold = current_state ["compression_threshold"])
        while True:
            try:
                packet_id, data = Packet.read (server_connection, compression_threshold = current_state ["compression_threshold"])
                s2c_print (f"data {Server._buffer_to_str (data)}", generic = False)
                def pass_through (): to_client (packet_id, data)

                if current_state ["id"] == 0: # Handshaking
                    raise ProtocolException ("Clientbound packet received in handshaking state")
                elif current_state ["id"] == 1: # Status, we don't need any special behavior here
                    pass_through ()
                elif current_state ["id"] == 2: # Login, we need to handle encryption + compression setup
                    if packet_id == 0x01:
                        s2c_print (f"proxy detected encryption request")
                        server_id, public_key_data, verify_token = Packet.decode_fields (data, (String, ByteArray, ByteArray))
                        s2c_print (f"public key {public_key_data} (len {len (public_key_data)}) verify token {verify_token} (len {len (verify_token)})")

                        shared_secret = secrets.token_bytes (16)

                        server_hash_obj = hashlib.sha1 ()
                        server_hash_obj.update (server_id.encode ("ascii"))
                        server_hash_obj.update (shared_secret)
                        server_hash_obj.update (public_key_data)
                        server_hash = format (int.from_bytes (server_hash_obj.digest (), byteorder = "big", signed = True), "x")

                        s2c_print ("attempting join request to session server")
                        join_response = requests.post (
                            "https://sessionserver.mojang.com/session/minecraft/join",
                            headers = {"Content-Type": "application/json"},
                            data = json.dumps ({
                                "accessToken": self.proxy_auth [1],
                                "selectedProfile": self.proxy_auth [0],
                                "serverId": server_hash
                            })
                        )
                        if join_response.status_code != 204: raise ProtocolException (f"Session server join request failed, status {join_response.status_code}, body {join_response.text}")
                        s2c_print ("join response succeeded")

                        public_key = load_der_public_key (public_key_data)
                        encrypted_shared_secret = public_key.encrypt (shared_secret, padding = PKCS1v15 ())
                        encrypted_verify_token = public_key.encrypt (verify_token, padding = PKCS1v15 ())
                        encryption_response_data = Packet.encode_fields ((encrypted_shared_secret, ByteArray), (encrypted_verify_token, ByteArray))
                        server_connection.setup_encryption (shared_secret)
                        s2c_print ("encryption enabled")
                        Packet.write (server_connection, 0x01, encryption_response_data, force_dont_encrypt = True)
                    elif packet_id == 3:
                        compression_threshold = Packet.decode_fields (data, (VarInt,)) [0]
                        current_state ["compression_threshold"] = compression_threshold
                        s2c_print (f"compression threshold updated to {compression_threshold}")
                    elif packet_id == 2:
                        s2c_print ("login success packet detected")
                        current_state ["id"] = 3
                        if handler_instance is not None: handler_instance.connected ()
                        pass_through ()
                    else: raise ProtocolException ("unknown login packet")
                elif current_state ["id"] == 3:
                    if packet_id == 0x46:
                        compression_threshold = Packet.decode_fields (data, (VarInt,)) [0]
                        current_state ["compression_threshold"] = compression_threshold
                        s2c_print (f"compression threshold updated to {compression_threshold}")
                    else:
                        s2c_print ("calling handler for generic play packet")
                        if handler_instance is not None:
                            should_pass_through = handler_instance.server_to_client (current_state, force_s2c_print, to_client, to_server, packet_id, data)
                            if should_pass_through: pass_through ()
                        else:
                            s2c_print ("passing through generic play packet")
                            pass_through ()
                else:
                    raise ProtocolException (f"unhandled state {current_state ['id']}")
            except EOFError:
                s2c_print ("eof", meta = True)
                break
            except ConnectionResetError:
                s2c_print ("reset", meta = True)
                break
            except BrokenPipeError:
                s2c_print ("broken pipe (exception in other direction?)", meta = True)
                break
            except:
                traceback.print_exc ()
                s2c_print ("other error", meta = True)
                break
        server_connection.ensure_closed ()
        client_connection.ensure_closed ()
    @staticmethod
    def _buffer_to_str (buffer: bytes, trunc_threshold: int = 100):
        buffer_str = "(truncated)" if len (buffer) >= trunc_threshold else str (buffer)
        return f"{buffer_str} (len {len (buffer)})"