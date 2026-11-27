"""Tiny made-up disc images for tests: a few sectors of ISO 9660 holding the files given. No game data."""
SECTOR = 2048


def tiny_iso(files: dict[str, bytes]) -> bytes:
    """A volume descriptor at sector 16, a root folder at sector 18 and the files after it."""
    extents, at = {}, 19
    for name, body in files.items():
        extents[name] = at
        at += max(1, -(-len(body) // SECTOR))
    root = bytearray()
    records = [(b"\0", 18, SECTOR, 2)] + [(f"{n};1".encode(), extents[n], len(b), 0) for n, b in files.items()]
    for ident, extent, size, flags in records:
        record = bytearray(33 + len(ident))
        record[0] = len(record) + len(record) % 2
        record[2:6], record[6:10] = extent.to_bytes(4, "little"), extent.to_bytes(4, "big")
        record[10:14], record[14:18] = size.to_bytes(4, "little"), size.to_bytes(4, "big")
        record[25], record[32] = flags, len(ident)
        record[33:] = ident
        root += record.ljust(record[0], b"\0")
    image = bytearray(at * SECTOR)
    image[16 * SECTOR:16 * SECTOR + 7] = b"\x01CD001\x01"
    image[16 * SECTOR + 156:16 * SECTOR + 190] = bytes(root[:34])
    image[18 * SECTOR:18 * SECTOR + len(root)] = root
    for name, body in files.items():
        image[extents[name] * SECTOR:extents[name] * SECTOR + len(body)] = body
    return bytes(image)


def ps2_disc(boot: bytes = b"BOOT2 = cdrom0:\\SLUS_203.12;1\r\nVER = 1.00\r\n") -> bytes:
    return tiny_iso({"SYSTEM.CNF": boot})


def psp_disc(umd: bytes = b"ULUS-10509|0000|0001|G|\n") -> bytes:
    return tiny_iso({"UMD_DATA.BIN": umd})
