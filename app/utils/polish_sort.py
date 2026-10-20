"""Polish alphabetical ordering independent of the database/OS locale."""

_ALPHABET = "aąbcćdeęfghijklłmnńoóprsśtuwyzźż"
_ORDER = {char: index for index, char in enumerate(_ALPHABET)}


def polish_sort_key(value):
    text = str(value or "").casefold()
    return tuple(_ORDER.get(char, len(_ORDER) + ord(char)) for char in text)
