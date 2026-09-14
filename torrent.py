from ftplib import MSG_OOB
from enum import Enum
import socket
from urllib.parse import urlencode
from hashlib import sha1
import bencoding
import os
import requests


class ClientStatus(Enum):
    DISCONNCTED = 0
    CONNECTING = 1
    IN_PROGRESS = 2
    CONNECTED = 3


class TorrentClient:
    socket_conn: socket.socket | None
    peer_host: str
    peer_port: int
    connection_status: ClientStatus = ClientStatus.DISCONNCTED
    info_hash: bytes
    peer_id: bytes

    def __init__(self):
        # Tracker discovery does not need a TCP peer socket.  Deferring its
        # creation lets one client span scan -> connect without allocating
        # disposable sockets along the way.
        self.socket_conn = None
        self._socket_is_closed = False

    # ============================ TRACKS PEERS =========================
    def get_torrent_metainfo(self, torrent_file_path: str) -> dict:
        """
        Opens a torrent file and returns its metainfo as a dictionary.

        Args:
            torrent_file_path (str): The path to the torrent file."""

        with open(torrent_file_path, "rb") as torrent_file:
            encoding = torrent_file.read()
            metainfo, _ = bencoding.bdecode_bytes(encoding)

        return metainfo

    def build_get_request(self, metainfo: dict) -> str:
        self.info_hash = sha1(bencoding.bencode_data(metainfo[b"info"])).digest()
        self.peer_id = b"-PY0001-" + os.urandom(12)

        params = {
            "info_hash": self.info_hash,
            # 20-byte random identifier
            "peer_id": self.peer_id,
            "port": 6882,
            "uploaded": 0,
            "downloaded": 0,
            "left": metainfo[b"info"][b"length"],
            "compact": 1,
        }

        get_request = metainfo[b"announce"].decode()
        get_request += "?" + urlencode(params)

        return get_request

    def contact_peers(self, req: str) -> dict:
        res = requests.get(req)

        obj, _ = bencoding.bdecode_bytes(res.content)

        print("Contacting peers...")
        print("request:", req)

        return obj

    def contact_peers_from_torrent(
        self, file_path: str, debug=False
    ) -> list[tuple[str, int]]:
        meta_info = self.get_torrent_metainfo(file_path)
        request = self.build_get_request(meta_info)

        res = self.contact_peers(request)

        peers_bytes = res[b"peers"]
        peers = []

        if debug:
            print("peers:")

        for i in range(0, len(peers_bytes), 6):
            peer = peers_bytes[i : i + 6]

            host = ".".join(str(byte) for byte in peer[:4])
            port = int.from_bytes(peer[4:], byteorder="big")

            if debug:
                print(f"Peer {i}: Host {host} Port {port}")

            peers.append((host, port))

        return peers

    # ============================ PEER WIRE PROTOCOL =======================
    # === TCP / uTP CONNECTION ===
    def connect_to_peer(self, host: str, port: int, debug=True) -> None:
        if self.socket_conn is None or self._socket_is_closed:
            self.socket_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket_is_closed = False
        self.peer_host = host
        self.peer_port = port

        if debug:
            print("Connect to peer:", self.peer_host, "at port", self.peer_port, "...")

        self.socket_conn.connect((self.peer_host, self.peer_port))
        self.connection_status = ClientStatus.CONNECTED

        if debug:
            print(f"Connected to peer {self.peer_host}:{self.peer_port}")

    def disconnect(self):
        if self.socket_conn is not None and not self._socket_is_closed:
            self.socket_conn.close()
            self._socket_is_closed = True
        self.connection_status = ClientStatus.DISCONNCTED

    def recv_exact(self, size: int):
        if self.socket_conn is None:
            raise ConnectionError("No peer is connected")
        dbytes = b""
        while len(dbytes) < size:
            chunk = self.socket_conn.recv(size - len(dbytes))
            if not chunk:
                raise ConnectionError("Connection closed by peer")
            dbytes += chunk
        return dbytes

    # === HANDSHAKE WITH PEER ===
    """
    0        pstrlen       1 byte
    1-19     protocol      19 bytes
    20-27    reserved      8 bytes
    28-47    info_hash     20 bytes
    48-67    peer_id       20 bytes
    """
    def build_handshake(self):
        protocol = b"BitTorrent protocol"
        return b"".join(
            [
                bytes([len(protocol)]),
                protocol,
                b"\x00" * 8,
                self.info_hash,
                self.peer_id,
            ]
        )

    def send_handshake(self):
        handshake = self.build_handshake()
        if self.socket_conn is None:
            raise ConnectionError("No peer is connected")
        self.socket_conn.sendall(handshake)

    def receive_handshake(self):
        """
        Receiving the 68 bytes handshake from peer
        """
        handshake = self.recv_exact(68)

        return {
            "protocol length": handshake[0],
            "protocol": handshake[1:20],
            "reserved": handshake[20:28],
            "info hash": handshake[28:48],
            "peer id": handshake[48:68],
        }

    def verify_info(self, handshake:dict):

        if handshake["info hash"] != self.info_hash:
            raise ValueError("Peer has a different info hash")

        print("Info hashes match !!")

    # === PEER WIRE MESSAGES ===
    """
    message length     4 bytes
    message ID         1 byte
    payload            N bytes
    """
    def send_msg(self):
        pass

    """
    0 - choke
    1 - unchoke
    2 - interested
    3 - not interested
    4 - have
    5 - bitfield
    6 - request
    7 - piece
    8 - cancel
    """
    def decode_payload(self, msg_id:int, payload: bytes):
        match msg_id:
            case 4: # have
                if len(payload) != 4:
                    raise ValueError("Invalid have payload")

                return int.from_bytes(payload, byteorder='big')

            case 5: # bitfield
                dec = []
                for b in payload:
                    piece = [int(b&(1<<k) != 0) for k in range(7, -1,-1)]
                    dec.append(piece)

                return dec

            case 6, 8: # request, cancel
                if len(payload) != 12:
                    raise ValueError("Invalid request payload")

                index, begin, length = [
                    int.from_bytes(payload[k:k+4], byteorder='big')
                    for k in range(0,len(payload), 4)
                ]

                return index, begin, length

            case 7:
                index = int.from_bytes(payload[:4], byteorder='big')
                begin = int.from_bytes(payload[4:8], byteorder='big')

                return index, begin, payload[8:]

            case 0,1,2,3: # choke, unchoke, interested, not interested
                if payload:
                    raise ValueError("Message must not have a payload")

                return None

            case _:
                raise ValueError(f"Unknown message ID: {msg_id}")

    def recv_msg(self):
        # receive message length
        msg_len_bytes = self.recv_exact(4)
        msg_len = int.from_bytes(msg_len_bytes, byteorder="big")

        if msg_len == 0: # keep alive
            return None

        # receive message
        dbytes = self.recv_exact(msg_len)

        msg_id = dbytes[0]
        payload = dbytes[1:]

        return msg_id, payload

    def encode_msg(self, msg_id:int, msg):
        match msg_id:
            case 4: # have
                return int.to_bytes(4, msg, byteorder='big')

            case 5: # bitfield
                enc = []
                for piece in msg:
                    piece_int = sum(piece[k] << (7-k) for k in range(8))
                    enc.append(piece_int)

                return bytes(enc)

            case 6, 8: # request, cancel
                index, begin, length = msg
                return b"".join([
                    int.to_bytes(4, index, byteorder="big"),
                    int.to_bytes(4, begin, byteorder="big"),
                    int.to_bytes(4, length, byteorder="big"),
                ])

            case 7:
                index, begin, block = msg
                return b"".join([
                    int.to_bytes(4, index, byteorder='big'),
                    int.to_bytes(4, begin, byteorder='big'),
                    block
                ])

            case 0,1,2,3: # choke, unchoke, interested, not interested
                if msg:
                    raise ValueError("Message must not have a message")

                return b""

            case _: # choke, unchoke, interested, not interested
                raise ValueError(f"Unknown message ID {msg_id}")

    def send_msg(self, msg_id:int, msg):
        block = self.encode_msg(msg_id, msg)
        length = len(block) + 1

        payload = b"".join([
            int.to_bytes(4, length, byteorder='big'),
            bytes([msg_id]),
            block
        ])

        self.socket_conn.sendall(payload)
