from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import http.client
from urllib.parse import urlparse
import json
import re
import random
import string
import httpx

app = FastAPI()
# Assuming you have a custom database client that mimics Redis commands over HTTP
class InMemoryDBClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    def checkConnection(self):
        url = urlparse(self.base_url)
        connection = http.client.HTTPConnection(url.hostname, url.port or 80)
        connection.request("GET", "/health")
        
        response = connection.getresponse()
        if response.status == 200:
            return True
        else:
            raise Exception("Failed to reach database")

    async def post(self, endpoint: str, data: dict):
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.base_url}{endpoint}", json=data)
            return response.json()

# Initialize the in-memory database client
db_client = InMemoryDBClient(base_url="http://localhost:8082")
print("DiceDB is running" if db_client.checkConnection() else "DiceDB is not running")


def generate_game_code(length=6) -> str:
    """Generate a random game code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


async def fetch_questions() -> list:
    """Fetch questions from the Open Trivia Database API."""
    url = "https://opentdb.com/api.php?amount=5&type=multiple"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()  # Raises an error for bad responses
            data = response.json()

            if data["response_code"] != 0:
                raise HTTPException(status_code=500, detail="Failed to fetch trivia questions")

            return data["results"]

        except httpx.RequestError as req_err:
            raise HTTPException(status_code=500, detail=f"Request error: {str(req_err)}")
        except httpx.HTTPStatusError as http_err:
            # Log the response content for debugging
            print(f"HTTP error: {http_err.response.status_code} - {http_err.response.text}")
            raise HTTPException(status_code=http_err.response.status_code, detail=f"HTTP error: {str(http_err)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@app.post("/create-game/")
async def create_game():
    game_code = generate_game_code()

    # Fetch questions
    try:
        all_questions = await fetch_questions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching questions: {str(e)}")

    # convert the list of questions to a string before storing
    all_questions = json.dumps(all_questions)
    # Store questions in the database
    response = await db_client.post("/set", {"key": f"game:{game_code}:questions", "value": all_questions})
    # Initialize the players in the in-memory database
    response = await db_client.post("/set", {"key": f"game:{game_code}:players", "value": {}})
    
    if response != "OK":
        raise HTTPException(status_code=500, detail="Failed to initialize players in the game session")
    
    return {
        "game_code": game_code, 
        "message": "Game session created successfully", 
        "questions_fetched": len(all_questions)
    }


# Pydantic model for user joining the game
class JoinGameRequest(BaseModel):
    username: str

@app.post("/join-game/{game_code}/")
async def join_game(game_code: str, join_request: JoinGameRequest):
    # Check if the game session exists
    gameSessionResponse = await db_client.post("/keys", {"value": f"game:*:players"})
    print("gameSessionResponse: ", gameSessionResponse)
    if len(gameSessionResponse) == 0:
        raise HTTPException(status_code=404, detail="No game sessions found")

    game_sessions = json.loads(gameSessionResponse)

    if f'game:{game_code}:players' not in game_sessions:
        raise HTTPException(status_code=404, detail="Game session not found")

    # Check if the username is unique within this game session
    response = await db_client.post("/get", {"key": f"game:{game_code}:players"})
    if response["status"] != "ok":
        raise HTTPException(status_code=404, detail="Game session not found")

    players = json.loads(response)

    # Check if the username is unique within this game session
    for player in players:
        if player["username"] == join_request.username:
            raise HTTPException(status_code=400, detail="Username already taken")

    # Add the new player to the list of players        
    new_player = {"username": join_request.username, "score": 0}
    players.append(new_player)

    players = json.dumps(players)

    response = await db_client.post("/set", {
        "key": f"game:{game_code}:players", 
        "value": players
    })

    if response["status"] != "ok":
        raise HTTPException(status_code=500, detail="Failed to join the game")
        
    return {"message": f"User '{join_request.username}' joined the game successfully", "game_code": game_code}


# Define a Pydantic model for the question format
class Question(BaseModel):
    question: str
    options: list[str]
    correct_answer: str

def parse_questions(questions: str) -> List[Question]:
    # in db all stored as json.dumps
    questions = json.loads(questions)
    parsed_questions = []
    for q in questions:
        options = q["incorrect_answers"] + [q["correct_answer"]]
        random.shuffle(options)
        parsed_questions.append(Question(question=q["question"], options=options, correct_answer=q["correct_answer"]))
    return parsed_questions


# returns list of questions
async def fetch_questions_from_db(game_code: str) -> list:
    response = await db_client.post("/get", {"key": f"game:{game_code}:questions"})
    if response == "(nil)":
        raise HTTPException(status_code=404, detail=f"No questions found for game session: {game_code}")
    questions = parse_questions(response)
    return questions

    

@app.get("/question/{game_code}/")
async def get_question(game_code: str):
    # Fetch the list of questions
    questions = await fetch_questions_from_db(game_code)
    return questions
