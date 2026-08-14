from __future__ import annotations

import ast
import struct
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / "locale"


def _unquote_po(value: str) -> str:
    return ast.literal_eval(value)


def _read_po(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    current_id: list[str] | None = None
    current_str: list[str] | None = None
    state: str | None = None

    def flush() -> None:
        if current_id is not None and current_str is not None:
            entries["".join(current_id)] = "".join(current_str)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("msgid "):
            flush()
            current_id = [_unquote_po(line[6:].strip())]
            current_str = []
            state = "msgid"
            continue

        if line.startswith("msgstr "):
            current_str = [_unquote_po(line[7:].strip())]
            state = "msgstr"
            continue

        if line.startswith('"') and state == "msgid" and current_id is not None:
            current_id.append(_unquote_po(line))
            continue

        if line.startswith('"') and state == "msgstr" and current_str is not None:
            current_str.append(_unquote_po(line))

    flush()
    return entries


def _write_mo(messages: dict[str, str], path: Path) -> None:
    keys = sorted(messages)
    ids = b""
    strs = b""
    offsets = []

    for key in keys:
        msgid = key.encode("utf-8")
        msgstr = messages[key].encode("utf-8")
        offsets.append((len(msgid), len(ids), len(msgstr), len(strs)))
        ids += msgid + b"\0"
        strs += msgstr + b"\0"

    count = len(keys)
    header_size = 7 * 4
    original_table_offset = header_size
    translation_table_offset = original_table_offset + count * 8
    string_offset = translation_table_offset + count * 8
    translated_string_offset = string_offset + len(ids)

    output = [
        struct.pack(
            "Iiiiiii",
            0x950412DE,
            0,
            count,
            original_table_offset,
            translation_table_offset,
            0,
            0,
        )
    ]

    output.extend(
        struct.pack("ii", msgid_len, string_offset + msgid_offset)
        for msgid_len, msgid_offset, _, _ in offsets
    )
    output.extend(
        struct.pack("ii", msgstr_len, translated_string_offset + msgstr_offset)
        for _, _, msgstr_len, msgstr_offset in offsets
    )
    output.append(ids)
    output.append(strs)

    path.write_bytes(b"".join(output))


def main() -> None:
    for po_path in LOCALE_DIR.glob("*/LC_MESSAGES/django.po"):
        mo_path = po_path.with_suffix(".mo")
        _write_mo(_read_po(po_path), mo_path)
        print(f"Compiled {po_path.relative_to(BASE_DIR)} -> {mo_path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
