from urllib.parse import urlencode
from hashlib import sha1
import bencoding
import os
import requests

def get_torrent_metainfo(torrent_file_path: str) -> dict:
    """
    Opens a torrent file and returns its metainfo as a dictionary.

    Args:
        torrent_file_path (str): The path to the torrent file."""

    with open(torrent_file_path, "rb") as torrent_file:
        encoding = torrent_file.read()
        metainfo, _ = bencoding.bdecode_bytes(encoding)

    return metainfo


def build_get_request(metainfo: dict):
    params = {
        "info_hash": sha1(bencoding.bencode_data(metainfo[b"info"])).digest(),
        # 20-byte random identifier
        "peer_id": b"-PY0001-" + os.urandom(12),
        "port": 6882,
        "uploaded": 0,
        "downloaded": 0,
        "left": metainfo[b"info"][b"length"],
        "compact": 1,
    }

    get_request = metainfo[b"announce"].decode()
    get_request += "?" + urlencode(params)

    return get_request


def contact_peers(req):
    res = requests.get(req)

    obj, _ = bencoding.bdecode_bytes(res.content)

    print("Contacting peers...")
    print("request:", req)

    return obj

def get_peers_from_response(res:dict):
    peers_bytes = res[b'peers']
    peers = []

    for i in range(0,len(peers_bytes),6):
        peer = peers_bytes[i:i+6]
        
        host = ".".join(str(byte) for byte in peer[:4])
        port = int.from_bytes(peer[4:], byteorder="big")

        peers.append((host, port))

    return peers

def contact_peers_from_torrent(file_path: str):
    meta_info = get_torrent_metainfo(file_path)
    request = build_get_request(meta_info)

    res = contact_peers(request)
    peers = get_peers_from_response(res)

    print("peers:")
    for i, (host, port) in enumerate(peers):
        print(f"Peer {i} : Host {host} Port {port}")
