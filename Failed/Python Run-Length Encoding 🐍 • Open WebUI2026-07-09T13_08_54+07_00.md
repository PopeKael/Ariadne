---
title: "Python Run-Length Encoding 🐍 • Open WebUI"
source: "http://localhost:3000/c/7232d1fa-7dc4-44b1-9646-e2520bb579b4"
author:
published:
created: 2026-07-09
description: "Open WebUI"
tags:
  - "clippings"
---
Write a Python function that performs run-length encoding on a string (e.g. "aaabbc" becomes "a3b2c1").

gpt-oss:20b

Thought for 7 seconds

Here’s a straightforward implementation of run‑length encoding (RLE) in Python:

```python
def rle_encode(s: str) -> str:
    """
    Perform run-length encoding on the input string \`s\`.

    Parameters
    ----------
    s : str
        The string to encode.  Consecutive identical characters are replaced
        by a single instance followed by the count of repetitions.

    Returns
    -------
    str
        The RLE‑encoded representation, e.g. 'aaabbc' → 'a3b2c1'.

    Notes
    -----
    * Empty strings return an empty string.
    * This implementation is case‑sensitive; 'A' and 'a' are treated as
      distinct characters.
    """
    if not s:
        return ""

    result = []          # use a list for efficient concatenation
    prev_char = s[0]
    count = 1

    for ch in s[1:]:
        if ch == prev_char:
            count += 1
        else:
            result.append(f"{prev_char}{count}")
            prev_char = ch
            count = 1

    # append the final run
    result.append(f"{prev_char}{count}")

    return "".join(result)
```

### Example usage

```python
>>> rle_encode("aaabbc")
'a3b2c1'

>>> rle_encode("")
''

>>> rle_encode("xyz")
'x1y1z1'
```

Feel free to tweak the function (e.g., to omit the count when it’s 1) depending on your exact requirements.

Follow up

---

Voice Typing

Play Alt A Save to Library Alt L Dictate Alt S

Settings