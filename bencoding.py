def bencode_data(d) -> bytes:
    """
    encode data using BENCODING

    Args:
        d (dictionnary | list | int | bytes): the data to encode."""

    dtype = type(d)

    if dtype is bytes or dtype is str:
        if dtype is str:
            d = d.encode()
            
        len_bytes = str(len(d)).encode()
        return len_bytes + b":" + d
    
    elif dtype is int:
        return f"i{d}e".encode()

    elif dtype is dict:
        return  b"d" + \
                b"".join(bencode_data(k) + bencode_data(v) for k, v in sorted(d.items())) + \
                b"e"
    
    elif dtype is list:
        return  b"l" + \
                b"".join(bencode_data(v) for v in d) + \
                b"e"

    else:
        raise Exception(f"Unsupported data type : {dtype}")

def bdecode_bytes(encoded: bytes, i:int=0):
    """
    decode BENCODED data string

    Args:
        encoded (bytes): the encoded data """

    match encoded[i]:
        case 105:  # int (i)
            i+=1
            st = i
            while encoded[i] != 101:
                i += 1
            return int(encoded[st:i]), i + 1

        case 100:  # dict (d)
            i+=1
            obj = {}
            while encoded[i] != 101: # e
                name,i = bdecode_bytes(encoded, i)
                value, i = bdecode_bytes(encoded, i)
                
                obj[name] = value
            return obj, i+1

        case 108:  # list (l)
            i+=1
            obj = []
            while encoded[i] != 101: # e
                value, i = bdecode_bytes(encoded, i)
                obj.append(value)
            return obj, i+1

        case _:  # str
            ls = b""
            while encoded[i] != 58: # :
                ls += bytes([encoded[i]])
                i += 1

            st = i + 1
            nxt = st + int(ls)
            return encoded[st:nxt], nxt
