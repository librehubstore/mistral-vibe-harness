def capitalize_words(text):
    return " ".join(w.capitalize() for w in text.split())


def truncate(text, size):
    return text if len(text) <= size else text[: size - 1] + "…"
