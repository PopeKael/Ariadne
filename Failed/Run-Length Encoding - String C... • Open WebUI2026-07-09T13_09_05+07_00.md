---
title: "Run-Length Encoding - String C... • Open WebUI"
source: "http://192.168.1.150:3000/c/9bae2eea-05ec-443e-b3db-a8d9fdd2180d"
author:
published:
created: 2026-07-09
description: "Open WebUI"
tags:
  - "clippings"
---
Write a Python function that performs run-length encoding on a string (e.g. "aaabbc" becomes "a3b2c1").

deepseek-r1:1.5b

Thought for 24 seconds

To solve this problem, we need to write a Python function that performs run-length encoding on a given string. Run-length encoding is a simple data compression technique where consecutive identical characters are replaced by their count of occurrences followed by the character itself. This helps in reducing the amount of data that needs to be transmitted or stored.

### Approach

The approach involves iterating through each character of the input string and grouping them into runs of the same character. Here's a step-by-step breakdown:

1. **Initialization**: Start by checking if the input string is empty. If it is, return an empty result immediately.
2. **Tracking Current Character and Count**: Initialize variables to keep track of the current character being processed and its count.
3. **Iterate Through Characters**: Loop through each character in the string starting from the second character (index 1). For each character:
	- If it matches the current character, increment the count.
		- If it does not match, append the current run to the result list, then update the current character and reset the count.
4. **Finalize the Result**: After the loop, ensure that the last group of characters is added to the result list before joining it into a string.

### Solution Code

```python
def run_length_encode(s):
    if not s:
        return ""
    result = []
    current_char = s[0]
    count = 1
    for c in s[1:]:
        if c == current_char:
            count += 1
        else:
            result.append((current_char, count))
            current_char = c
            count = 1
    result.append((current_char, count))
    return ' '.join([f"{char}{count}" for char, count in result])
```

### Explanation

- **Initialization**: The function starts by checking if the input string is empty and returns an empty result if true.
- **Tracking Current Character and Count**: `current_char` keeps track of the current character being processed, starting with the first character. `count` is initialized to 1 since we start processing from the second character.
- **Iterating Through Characters**: For each subsequent character, it checks if it matches `current_char`. If it does, the count is incremented. If not, the current run (including its characters and count) is added to the result list, and new variables are initialized for the new character.
- **Finalizing the Result**: After processing all characters, the last run is added to ensure no data is lost.

This approach efficiently processes each character in a single pass, resulting in a time complexity of O(n), where n is the length of the input string. The space complexity is also O(n) due to the storage required for the result list.

Follow up

---

Voice Typing

Play Alt A Save to Library Alt L Dictate Alt S

Settings