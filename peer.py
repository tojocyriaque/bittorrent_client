"""Contact BitTorrent peers from a torrent file."""

import argparse
from pathlib import Path

import torrent


def main():
	parser = argparse.ArgumentParser(
		description="Contact peers listed in a BitTorrent torrent file.",
		usage="%(prog)s TORRENT_FILE",
	)
	parser.add_argument(
		"torrent_file",
		metavar="TORRENT_FILE",
		help="path to the .torrent file",
	)
	args = parser.parse_args()

	torrent_file = Path(args.torrent_file).expanduser()
	if not torrent_file.is_file():
		parser.error(f"torrent file does not exist or is not a file: {torrent_file}")
	if torrent_file.stat().st_size == 0:
		parser.error(f"torrent file is empty: {torrent_file}")
	if torrent_file.suffix.lower() != ".torrent":
		parser.error(f"expected a .torrent file: {torrent_file}")

	torrent.contact_peers_from_torrent(str(torrent_file))

if __name__ == "__main__":
	main()
