from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class User(BaseModel):
    id: int
    name: str


@app.get("/user")
def get_user() -> User:
    return "This is not a User object"
