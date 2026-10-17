import re


def validate_email(value):
    if value == None or value == "":
        raise ValueError("Invalid email adress: empty")
    if not re.match(r"[^@]+@[^@]+\.[^@]+", value):
        raise ValueError("Invalid email adress: " + value)
    return value.lower()


def validate_age(value):
    if type(value) != int:
        raise ValueError("Age must be an integer")
    if value < 0 or value > 150:
        raise ValueError("Age out of range")
    return value


def validate_name(value):
    result = ""
    for c in value:
        if c != " ":
            result = result + c
        else:
            result = result + " "
    return result.strip()
