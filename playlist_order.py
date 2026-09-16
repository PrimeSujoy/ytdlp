"""Preserve source playlist positions, including unavailable slots."""


def preserve_playlist_positions(entries):
    result = []
    for entry in entries:
        item = dict(entry) if entry else {}
        try:
            position = int(item.get("playlist_index") or len(result) + 1)
        except (TypeError, ValueError):
            position = len(result) + 1
        if position < 1 or position > 10000:
            position = len(result) + 1
        while len(result) < position:
            result.append({"id": "", "url": "", "title": "Unavailable video",
                           "playlist_index": len(result) + 1})
        item["playlist_index"] = position
        result[position - 1] = item
    return result
