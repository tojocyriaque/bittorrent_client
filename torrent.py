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
    socket_conn: socket.socket
    peer_host: str
    peer_port: int
    handshake: str
    connection_status: ClientStatus = ClientStatus.DISCONNCTED
    info_hash: bytes
    peer_id: bytes


    def __init__(self):
        self.socket_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    #============================ TRACKS PEERS =========================
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

    def contact_peers_from_torrent(self, file_path: str, debug = False) -> list[tuple[str, int]]:
        meta_info = self.get_torrent_metainfo(file_path)
        request = self.build_get_request(meta_info)

        res = self.contact_peers(request)

        peers_bytes = res[b'peers']
        peers = []

        if debug:
            print("peers:")
        
        for i in range(0,len(peers_bytes),6):
            peer = peers_bytes[i:i+6]
            
            host = ".".join(str(byte) for byte in peer[:4])
            port = int.from_bytes(peer[4:], byteorder="big")

            if debug:
                print(f"Peer {i}: Host {host} Port {port}")
        
            peers.append((host, port))

        return peers

    #============================ PEER WIRE PROTOCOL =======================
    def connect_to_peer(self, host: str, port:int, debug = True) -> None:
        self.peer_host = host
        self.peer_port = port

        if debug:
            print("Connect to peer:",self.peer_host,"at port", self.peer_port,"...")

        self.socket_conn.connect((self.peer_host, self.peer_port))
        self.connection_status = ClientStatus.CONNECTED

        if debug:
            print(f"Connected to peer {self.peer_host}:{self.peer_port}")
    
    def disconnect(self):
        self.socket_conn.close()
        self.connection_status = ClientStatus.DISCONNCTED

    def build_handshake(self):
        protocol = b"BitTorrent protocol"
        return b"".join([
            bytes([len(protocol)]),
            protocol,
            b"\x00"*8,
            self.info_hash,
            self.peer_id
        ])
 