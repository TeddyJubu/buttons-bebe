"""graphql_guard.py -- shared GraphQL lexer for read-only safety gates.

Two live callers import this module so the read-only Shopify invariant has one
lexer instead of two drifting copies (report 06, action 6):

  - shopify/shopify.py          policy: query operations only
  - kb/scripts/sync_products.py policy: queries + the one bulk read mutation

Stdlib-only, no imports, so it is loadable from any of the repo's venvs.
Per-caller policy functions stay local to each caller; this file owns only
tokenization and the selection-set finder.
"""


def graphql_tokens(document: str) -> list[tuple[str, str]]:
    """Tokenize enough of GraphQL to identify top-level operations safely.

    This is deliberately a small lexer rather than a regular expression.  It
    skips comments and string/block-string contents, so words such as
    ``mutation`` in customer/product text cannot bypass the operation check.
    """
    if not isinstance(document, str) or not document.strip():
        raise ValueError("GraphQL document must be a non-empty string")

    tokens = []
    i = 0
    length = len(document)
    punctuators = set("!$&():=@[]{|}")

    while i < length:
        char = document[i]
        if char in " \t\n\r,\ufeff":
            i += 1
            continue
        if char == "#":
            i += 1
            while i < length and document[i] not in "\r\n":
                i += 1
            continue
        if document.startswith("...", i):
            tokens.append(("punct", "..."))
            i += 3
            continue
        if char == '"':
            if document.startswith('"""', i):
                i += 3
                while i < length:
                    if document.startswith('\\"""', i):
                        i += 4
                    elif document.startswith('"""', i):
                        i += 3
                        break
                    else:
                        i += 1
                else:
                    raise ValueError("unterminated GraphQL block string")
            else:
                i += 1
                while i < length:
                    if document[i] == "\\":
                        i += 2
                    elif document[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
                else:
                    raise ValueError("unterminated GraphQL string")
            tokens.append(("string", ""))
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < length and (document[i].isalnum() or document[i] == "_"):
                i += 1
            tokens.append(("name", document[start:i]))
            continue
        if char.isdigit() or (char == "-" and i + 1 < length and document[i + 1].isdigit()):
            start = i
            i += 1
            while i < length and (document[i].isalnum() or document[i] in ".+-"):
                i += 1
            tokens.append(("value", document[start:i]))
            continue
        if char in punctuators:
            tokens.append(("punct", char))
            i += 1
            continue
        raise ValueError(f"invalid GraphQL character: {char!r}")

    return tokens


def graphql_selection_start(tokens, start: int) -> int:
    """Find an operation/fragment selection set after its header."""
    paren_depth = 0
    bracket_depth = 0
    for index in range(start, len(tokens)):
        value = tokens[index][1]
        if value == "(":
            paren_depth += 1
        elif value == ")":
            paren_depth -= 1
            if paren_depth < 0:
                raise ValueError("unbalanced GraphQL parentheses")
        elif value == "[":
            bracket_depth += 1
        elif value == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                raise ValueError("unbalanced GraphQL brackets")
        elif value == "{" and paren_depth == 0 and bracket_depth == 0:
            return index
    raise ValueError("GraphQL operation has no selection set")
