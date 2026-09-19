"""
Example plugin. Drop a .py file like this into plugins/ and it's auto-loaded
at startup -- no changes to the core project needed.

A plugin defines either:
  - a module-level TOOLS list of (schema, function) pairs (multiple tools), or
  - a module-level SCHEMA dict + run() function (a single tool), as shown here.

This one is a stub (no real weather API call) just to show the shape.
"""

SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_word_length",
        "description": "Return the number of characters in a word or phrase. Toy example plugin.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}


def run(text: str) -> str:
    return f"'{text}' has {len(text)} characters"
