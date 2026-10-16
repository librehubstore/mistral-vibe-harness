from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int


def test_user_parses_age():
    assert User(name="Ana", age="42").age == 42
