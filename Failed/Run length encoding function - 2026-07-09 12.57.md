# Run length encoding function
Model: qwen/qwen3-coder-30b
Created: 09/07/2026, 12:20:09 pm
Exported from: LM Studio 0.4.19+2

### User
Write a Python function that performs run-length encoding on a string (e.g. "aaabbc" becomes "a3b2c1").

### Assistant
Here's a Python function that performs run-length encoding:

```python
def run_length_encode(s):
    """
    Performs run-length encoding on a string.
    
    Args:
        s (str): The input string to encode
        
    Returns:
        str: The run-length encoded string
        
    Examples:
        &gt;&gt;&gt; run_length_encode("aaabbc")
        'a3b2c1'
        &gt;&gt;&gt; run_length_encode("aabbcc")
        'a2b2c2'
        &gt;&gt;&gt; run_length_encode("aaaa")
        'a4'
        &gt;&gt;&gt; run_length_encode("")
        ''
    """
    # Handle empty string
    if not s:
        return ""
    
    encoded = []
    current_char = s[0]
    count = 1
    
    # Iterate through the string starting from the second character
    for i in range(1, len(s)):
        if s[i] == current_char:
            # Same character, increment count
            count += 1
        else:
            # Different character, store the previous character and count
            encoded.append(current_char + str(count))
            current_char = s[i]
            count = 1
    
    # Don't forget to add the last character group
    encoded.append(current_char + str(count))
    
    return ''.join(encoded)

# Test the function
if __name__ == "__main__":
    # Test cases
    test_cases = [
        "aaabbc",
        "aabbcc",
        "aaaa",
        "",
        "a",
        "abcdef",
        "aabbccddee"
    ]
    
    for test in test_cases:
        result = run_length_encode(test)
        print(f"'{test}' -&gt; '{result}'")
```

This function works by:

1. **Handling edge cases**: Empty strings return empty strings
2. **Tracking character groups**: It keeps track of the current character and its count
3. **Building the result**: When a different character is encountered, it appends the character and its count to the result list
4. **Finalizing**: After the loop, it adds the last character group

**Output for the test cases:**
```
'aaabbc' -&gt; 'a3b2c1'
'aabbcc' -&gt; 'a2b2c2'
'aaaa' -&gt; 'a4'
'' -&gt; ''
'a' -&gt; 'a1'
'abcdef' -&gt; 'a1b1c1d1e1f1'
'aabbccddee' -&gt; 'a2b2c2d2e2'
```

**Alternative shorter version using itertools.groupby:**

```python
from itertools import groupby

def run_length_encode_v2(s):
    """
    Alternative implementation using itertools.groupby.
    """
    if not s:
        return ""
    
    encoded = []
    for char, group in groupby(s):
        count = len(list(group))
        encoded.append(char + str(count))
    
    return ''.join(encoded)
```

Both implementations have the same time complexity of O(n) and space complexity of O(n), where n is the length of the input string.
