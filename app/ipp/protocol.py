"""Binary IPP message encoding/decoding (RFC 8010), just enough for a
printer: requests are parsed into groups of attributes, responses are
built from (tag, name, values) tuples."""

import struct
from dataclasses import dataclass, field

# Delimiter (group) tags
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED_GROUP = 0x05

# Out-of-band value tags
TAG_UNSUPPORTED_VALUE = 0x10
TAG_UNKNOWN = 0x12
TAG_NO_VALUE = 0x13

# Value tags
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTET_STRING = 0x30
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGE = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_TEXT_LANG = 0x35
TAG_NAME_LANG = 0x36
TAG_END_COLLECTION = 0x37
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_URI_SCHEME = 0x46
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME_TYPE = 0x49
TAG_MEMBER_NAME = 0x4A

# Operations
OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_CREATE_JOB = 0x0005
OP_SEND_DOCUMENT = 0x0006
OP_CANCEL_JOB = 0x0008
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_JOBS = 0x000A
OP_GET_PRINTER_ATTRIBUTES = 0x000B
OP_CLOSE_JOB = 0x003B
OP_IDENTIFY_PRINTER = 0x003C

# Status codes
STATUS_OK = 0x0000
STATUS_OK_IGNORED = 0x0001
STATUS_BAD_REQUEST = 0x0400
STATUS_NOT_FOUND = 0x0406
STATUS_NOT_POSSIBLE = 0x0404
STATUS_DOCUMENT_FORMAT_NOT_SUPPORTED = 0x040A
STATUS_INTERNAL_ERROR = 0x0500
STATUS_OPERATION_NOT_SUPPORTED = 0x0501
STATUS_VERSION_NOT_SUPPORTED = 0x0503

INT_TAGS = {TAG_INTEGER, TAG_ENUM}
STRING_TAGS = {
    TAG_TEXT, TAG_NAME, TAG_KEYWORD, TAG_URI, TAG_URI_SCHEME,
    TAG_CHARSET, TAG_LANGUAGE, TAG_MIME_TYPE, TAG_MEMBER_NAME,
}


class IppError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message)
        self.status = status


@dataclass
class Attr:
    tag: int
    name: str
    values: list  # ints / bools / strs / bytes / tuples / list[Attr] for collections


@dataclass
class Request:
    version: tuple[int, int]
    operation: int
    request_id: int
    groups: list[tuple[int, list[Attr]]] = field(default_factory=list)
    data: bytes = b""  # document payload following the attributes

    def group(self, tag: int) -> dict[str, Attr]:
        merged: dict[str, Attr] = {}
        for group_tag, attrs in self.groups:
            if group_tag == tag:
                merged.update({a.name: a for a in attrs})
        return merged

    def value(self, name: str, default=None, tag: int = TAG_OPERATION):
        attr = self.group(tag).get(name)
        return attr.values[0] if attr and attr.values else default


# --- Decoding ---------------------------------------------------------------


def _decode_value(tag: int, raw: bytes):
    if tag in INT_TAGS:
        return struct.unpack(">i", raw)[0]
    if tag == TAG_BOOLEAN:
        return raw != b"\x00"
    if tag in STRING_TAGS:
        return raw.decode("utf-8", "replace")
    if tag == TAG_RESOLUTION:
        return struct.unpack(">iib", raw)
    if tag == TAG_RANGE:
        return struct.unpack(">ii", raw)
    if tag in (TAG_TEXT_LANG, TAG_NAME_LANG):
        lang_len = struct.unpack(">H", raw[:2])[0]
        text_len = struct.unpack(">H", raw[2 + lang_len:4 + lang_len])[0]
        return raw[4 + lang_len:4 + lang_len + text_len].decode("utf-8", "replace")
    return raw  # octetString, dateTime, out-of-band, unknown


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise IppError(STATUS_BAD_REQUEST, "truncated IPP message")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def read_value(self) -> tuple[int, str, bytes]:
        tag = self.u8()
        name = self.take(self.u16()).decode("utf-8", "replace")
        raw = self.take(self.u16())
        return tag, name, raw

    def read_collection(self) -> list[Attr]:
        members: list[Attr] = []
        current: Attr | None = None
        while True:
            tag, _, raw = self.read_value()
            if tag == TAG_END_COLLECTION:
                return members
            if tag == TAG_MEMBER_NAME:
                current = Attr(tag=0, name=raw.decode("utf-8", "replace"), values=[])
                members.append(current)
                continue
            if current is None:
                raise IppError(STATUS_BAD_REQUEST, "collection value without member name")
            current.tag = tag
            current.values.append(self.read_collection() if tag == TAG_BEGIN_COLLECTION else _decode_value(tag, raw))


def decode_request(data: bytes) -> Request:
    r = _Reader(data)
    major, minor = r.u8(), r.u8()
    operation = r.u16()
    request_id = struct.unpack(">I", r.take(4))[0]
    req = Request(version=(major, minor), operation=operation, request_id=request_id)

    attrs: list[Attr] | None = None
    while True:
        tag = r.u8()
        if tag == TAG_END:
            break
        if tag < 0x10:  # new attribute group
            attrs = []
            req.groups.append((tag, attrs))
            continue
        if attrs is None:
            raise IppError(STATUS_BAD_REQUEST, "attribute outside of a group")
        r.pos -= 1
        tag, name, raw = r.read_value()
        value = r.read_collection() if tag == TAG_BEGIN_COLLECTION else _decode_value(tag, raw)
        if name:
            attrs.append(Attr(tag=tag, name=name, values=[value]))
        elif attrs:  # additional value of the previous attribute
            attrs[-1].values.append(value)
    req.data = data[r.pos:]
    return req


# --- Encoding ---------------------------------------------------------------


def _encode_value(tag: int, value) -> bytes:
    if tag in INT_TAGS:
        return struct.pack(">i", value)
    if tag == TAG_BOOLEAN:
        return b"\x01" if value else b"\x00"
    if tag in STRING_TAGS:
        return value.encode("utf-8")
    if tag == TAG_RESOLUTION:
        return struct.pack(">iib", *value)
    if tag == TAG_RANGE:
        return struct.pack(">ii", *value)
    if tag in (TAG_UNSUPPORTED_VALUE, TAG_UNKNOWN, TAG_NO_VALUE):
        return b""
    if isinstance(value, bytes):
        return value
    raise ValueError(f"can't encode value tag 0x{tag:02x}")


def _encode_attr(attr: Attr) -> bytes:
    out = bytearray()
    name = attr.name.encode("utf-8")
    values = attr.values or [None]
    for i, value in enumerate(values):
        attr_name = name if i == 0 else b""
        out += struct.pack(">BH", attr.tag, len(attr_name)) + attr_name
        if attr.tag == TAG_BEGIN_COLLECTION:
            out += struct.pack(">H", 0)
            for member in value:
                member_name = member.name.encode("utf-8")
                out += struct.pack(">BHH", TAG_MEMBER_NAME, 0, len(member_name)) + member_name
                # Member values are encoded like a nameless attribute.
                out += _encode_attr(Attr(member.tag, "", member.values))
            out += struct.pack(">BHH", TAG_END_COLLECTION, 0, 0)
        else:
            raw = _encode_value(attr.tag, value)
            out += struct.pack(">H", len(raw)) + raw
    return bytes(out)


def encode_response(
    version: tuple[int, int],
    status: int,
    request_id: int,
    groups: list[tuple[int, list[Attr]]],
) -> bytes:
    out = bytearray(struct.pack(">BBHI", version[0], version[1], status, request_id))
    for group_tag, attrs in groups:
        out.append(group_tag)
        for attr in attrs:
            out += _encode_attr(attr)
    out.append(TAG_END)
    return bytes(out)


def collection(**members: tuple[int, object]) -> list[Attr]:
    """Builds a collection value: collection(x_dimension=(TAG_INTEGER, 21000))
    — underscores in names become dashes."""
    result = []
    for name, (tag, value) in members.items():
        # A nested collection's value is itself a list of Attr — one value.
        values = [value] if tag == TAG_BEGIN_COLLECTION or not isinstance(value, list) else value
        result.append(Attr(tag, name.replace("_", "-"), values))
    return result
