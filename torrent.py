import bencoding

def get_torrent_metainfo(torrent_file_path:str)->dict:
    """
    Opens a torrent file and returns its metainfo as a dictionary.

    Args:
        torrent_file_path (str): The path to the torrent file."""

    with open(torrent_file_path, "rb") as torrent_file:
        encoding = torrent_file.read()
        metainfo = bencoding.bdecode_bytes(encoding)

    return metainfo
    
