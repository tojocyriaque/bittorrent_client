def bencode_data(d) -> str:
    dtype = type(d)

    if dtype == str:
        return f"{len(d)}:{d}"

    elif dtype == int:
        return f"i{d}e"

    elif dtype == dict:
        return "d{}e".format(
            "".join(bencode_data(k) + bencode_data(v) for k, v in sorted(d.items()))
        )
    elif dtype == list:
        return "l{}e".format("".join(bencode_data(v) for v in d))
    else:
        raise Exception(f"Unsupported data type : {dtype}")

def bdecode_str(encoded: str, i:int=0):
    match encoded[i]:
        case "i":  # int
            integer = ""
            i+=1
            while encoded[i] != "e":
                integer += encoded[i]
                i += 1
            return int(integer), i + 1

        case "d":  # dict
            i+=1
            obj = {}
            while encoded[i] != "e":
                name,i = bdecode_str(encoded, i)
                value, i = bdecode_str(encoded, i)
                
                obj[name] = value
            return obj, i+1

        case "l":  # list
            i+=1
            obj = []
            while encoded[i] != "e":
                value, i = bdecode_str(encoded, i)
                obj.append(value)
            return obj, i+1

        case _:  # str
            ls = ""
            while encoded[i] != ":":
                ls += encoded[i]
                i += 1

            st = i + 1
            nxt = st + int(ls)
            return encoded[st:nxt], nxt
