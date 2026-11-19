"""DNS messages, as much of them as the gate reads and writes."""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from enum import IntEnum
from ipaddress import IPv4Address

ANSWER_TTL = 60                        # seconds; also how long a DoH answer may be cached
MAX_MESSAGE_BYTES = 4096
HEADER_BYTES = 12
SHORTEST_QUERY_BYTES = 17
CLASS_INTERNET = 1
RESPONSE_BIT = 0x80                    # in the third byte of the header
OPCODE_MASK = 0x0F                     # after shifting that byte three bits right
TRUNCATED_BIT = 0x02
RECURSION_DESIRED_BIT = 0x01
COMPRESSION_BITS = 0xC0                # a label length with both bits set is a pointer
ANSWER_FLAGS = 0x8180                  # a response, recursion available, no error
RECURSIVE_QUERY_FLAGS = 0x0100
POINTER_TO_QUESTION = b"\xc0\x0c"      # "the name is the one in the question"
RECORD_HEAD_BYTES = 10                 # type, class, TTL, length of the data
IPV4_BYTES = 4


class RecordType(IntEnum):
    A = 1
    AAAA = 28
    SVCB = 64
    HTTPS = 65


# what a listed name is answered for by the gate itself: its address, or nothing (no IPv6 path, no hints past the proxy)
ANSWERED_TYPES = frozenset({RecordType.A, RecordType.AAAA, RecordType.SVCB, RecordType.HTTPS})


@dataclass(frozen=True, slots=True)
class Question:
    """The one question of a query."""

    name: str
    record_type: int
    end: int                           # where the question ends in the message


@dataclass(frozen=True, slots=True)
class AddressRecord:
    address: IPv4Address
    ttl: int


def parse_question(message: bytes) -> Question | None:
    """The question of a standard single-question query; None for anything else."""
    if len(message) < SHORTEST_QUERY_BYTES or struct.unpack("!H", message[4:6])[0] != 1:
        return None
    if message[2] & RESPONSE_BIT or (message[2] >> 3) & OPCODE_MASK:
        return None
    position = HEADER_BYTES
    labels: list[str] = []
    while True:
        if position >= len(message):
            return None
        length = message[position]
        if length == 0:
            position += 1
            break
        if length & COMPRESSION_BITS:               # compression is not valid in a question
            return None
        labels.append(message[position + 1:position + 1 + length].decode("ascii", "replace").lower())
        position += 1 + length
    if position + 4 > len(message):
        return None
    record_type, record_class = struct.unpack("!HH", message[position:position + 4])
    if record_class != CLASS_INTERNET:
        return None
    return Question(".".join(labels), record_type, position + 4)


def answer(message: bytes, question: Question, address: IPv4Address | None) -> bytes:
    """An authoritative-looking reply built from the query: one A record with `address`, or empty."""
    flags = ANSWER_FLAGS | (message[2] & RECURSION_DESIRED_BIT) << 8
    answers = 1 if address is not None else 0
    reply = message[:2] + struct.pack("!HHHHH", flags, 1, answers, 0, 0) + message[HEADER_BYTES:question.end]
    if address is not None:
        reply += POINTER_TO_QUESTION + struct.pack("!HHIH", RecordType.A, CLASS_INTERNET, ANSWER_TTL, IPV4_BYTES) + address.packed
    return reply


def is_truncated(response: bytes) -> bool:
    return len(response) > 2 and bool(response[2] & TRUNCATED_BIT)


def address_query(name: str, query_id: int) -> bytes:
    """A recursive query for the A records of `name`."""
    header = struct.pack("!HHHHHH", query_id & 0xFFFF, RECURSIVE_QUERY_FLAGS, 1, 0, 0, 0)
    question = b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\0"
    return header + question + struct.pack("!HH", RecordType.A, CLASS_INTERNET)


def _after_name(response: bytes, position: int) -> int:
    while True:
        length = response[position]
        if length == 0:
            return position + 1
        if length & COMPRESSION_BITS:
            return position + 2
        position += 1 + length


def address_records(response: bytes) -> list[AddressRecord]:
    """The A records in the answer section of a response to a single-question query."""
    answers = struct.unpack("!H", response[6:8])[0]
    position = _after_name(response, HEADER_BYTES) + 4
    records: list[AddressRecord] = []
    for _ in range(answers):
        position = _after_name(response, position)
        record_type, _record_class, ttl, length = struct.unpack("!HHIH", response[position:position + RECORD_HEAD_BYTES])
        position += RECORD_HEAD_BYTES
        if record_type == RecordType.A and length == IPV4_BYTES:
            records.append(AddressRecord(IPv4Address(socket.inet_ntoa(response[position:position + IPV4_BYTES])), ttl))
        position += length
    return records
