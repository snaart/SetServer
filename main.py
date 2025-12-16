import asyncio
import secrets
import hashlib
import random
from typing import List, Dict, Optional, Any, Tuple
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field


# ==================== Data Models ====================

class Card(BaseModel):
    id: int
    count: int
    shape: int
    fill: int
    color: int

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, Card) and self.id == other.id


# --- Request Models ---

class RegisterRequest(BaseModel):
    nickname: str
    password: str


class BaseTokenRequest(BaseModel):
    accessToken: str


class EnterRoomRequest(BaseTokenRequest):
    gameId: int


class PickRequest(BaseTokenRequest):
    cards: List[int]


# --- Response Models ---

class ExceptionResponse(BaseModel):
    message: str


class BaseResponse(BaseModel):
    success: bool
    exception: Optional[ExceptionResponse] = None


class RegisterResponse(BaseResponse):
    nickname: Optional[str] = None
    accessToken: Optional[str] = None


class CreateRoomResponse(BaseResponse):
    gameId: Optional[int] = None


class ListRoomResponse(BaseResponse):
    games: Optional[List[Dict[str, int]]] = None


class FieldResponse(BaseResponse):
    cards: Optional[List[Card]] = None
    status: Optional[str] = None
    score: Optional[int] = None


class PickResponse(BaseResponse):
    isSet: Optional[bool] = None
    score: Optional[int] = None


class UserScore(BaseModel):
    name: str
    score: int


class ScoresResponse(BaseResponse):
    users: Optional[List[UserScore]] = None


# ==================== Game Logic ====================

class GameRoom:
    def __init__(self, game_id: int):
        self.game_id = game_id
        self.deck: List[Card] = []
        self.field: List[Card] = []
        self.players: Dict[str, int] = {}  # token -> score
        self.status: str = "ongoing"
        self._lock = asyncio.Lock()  # Для защиты состояния игры
        self._initialize_deck()
        self._deal_initial_cards()

    def _initialize_deck(self):
        self.deck.clear()
        card_id = 0
        # Используем 1,2,3 согласно правилам Set
        properties = [1, 2, 3]
        for color in properties:
            for shape in properties:
                for fill in properties:
                    for count in properties:
                        self.deck.append(Card(
                            id=card_id, count=count, shape=shape, fill=fill, color=color
                        ))
                        card_id += 1
        random.shuffle(self.deck)

    def _deal_initial_cards(self):
        # Раздаем 12 карт
        for _ in range(min(12, len(self.deck))):
            self.field.append(self.deck.pop())

    def add_player(self, token: str):
        if token not in self.players:
            self.players[token] = 0

    def get_player_score(self, token: str) -> int:
        return self.players.get(token, 0)

    @staticmethod
    def _is_valid_set(c1: Card, c2: Card, c3: Card) -> bool:
        """Проверяет, образуют ли 3 карты сет."""

        def check(attr):
            v1, v2, v3 = getattr(c1, attr), getattr(c2, attr), getattr(c3, attr)
            # Все одинаковые ИЛИ все разные
            return (v1 == v2 == v3) or (v1 != v2 and v1 != v3 and v2 != v3)

        return (check('color') and check('shape') and
                check('fill') and check('count'))

    async def pick_set(self, token: str, card_ids: List[int]) -> Tuple[bool, int]:
        """
        Атомарная операция выбора сета.
        Возвращает (успех, текущий счет).
        """
        async with self._lock:
            if self.status != "ongoing":
                return False, self.players.get(token, 0)

            if len(card_ids) != 3:
                return False, self.players.get(token, 0)

            # Находим объекты карт
            # Используем dict для быстрого поиска
            field_map = {c.id: c for c in self.field}
            selected_cards = []

            for cid in card_ids:
                if cid not in field_map:
                    # Карта уже забрана другим игроком или ID неверен
                    return False, self.players.get(token, 0)
                selected_cards.append(field_map[cid])

            if self._is_valid_set(*selected_cards):
                # Удаляем карты с поля
                for card in selected_cards:
                    self.field.remove(card)

                self.players[token] += 1

                # Добавляем новые карты, если есть в колоде
                # Правило: на поле должно быть не менее 12 карт, если колода не пуста,
                # либо просто восстанавливаем до 12 после взятия сета.
                cards_needed = 12 - len(self.field)
                if cards_needed > 0:
                    for _ in range(min(cards_needed, len(self.deck))):
                        self.field.append(self.deck.pop())

                # Проверка конца игры
                if not self.deck and len(self.field) < 3:
                    # Упрощенная проверка конца игры.
                    # В идеале надо проверять наличие сетов на остатках поля.
                    self.status = "ended"

                return True, self.players[token]
            else:
                # Штраф за неверный сет
                self.players[token] -= 1
                return False, self.players[token]

    async def add_cards_manual(self):
        """Добавляет 3 карты (если игроки не видят сетов)."""
        async with self._lock:
            for _ in range(min(3, len(self.deck))):
                self.field.append(self.deck.pop())


# ==================== Server State Manager ====================

class ServerState:
    def __init__(self):
        self.users: Dict[str, Dict[str, Any]] = {}  # token -> user_data
        self.games: Dict[int, GameRoom] = {}
        self.next_game_id = 0
        self._global_lock = asyncio.Lock()

    def register_user(self, nickname: str, password: str) -> str:
        # Простая генерация токена и хэширование пароля
        # В реальном проекте используйте bcrypt и JWT
        token = secrets.token_hex(16)
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()

        self.users[token] = {
            "nickname": nickname,
            "password_hash": pwd_hash,
            "current_game_id": None
        }
        return token

    def verify_token(self, token: str) -> bool:
        return token in self.users

    def get_user_nickname(self, token: str) -> Optional[str]:
        return self.users.get(token, {}).get("nickname")

    async def create_game(self) -> int:
        async with self._global_lock:
            gid = self.next_game_id
            self.next_game_id += 1
            self.games[gid] = GameRoom(gid)
            return gid

    def get_game(self, game_id: int) -> Optional[GameRoom]:
        return self.games.get(game_id)

    def get_user_game(self, token: str) -> Optional[GameRoom]:
        gid = self.users.get(token, {}).get("current_game_id")
        if gid is None:
            return None
        return self.games.get(gid)

    def enter_game(self, token: str, game_id: int) -> bool:
        if game_id not in self.games:
            return False
        self.users[token]["current_game_id"] = game_id
        self.games[game_id].add_player(token)
        return True


# Инициализация глобального состояния
server_state = ServerState()

app = FastAPI(title="Set Game Server")


# ==================== Helpers ====================

def success_response(data: dict):
    """Обертка для успешного ответа."""
    return {"success": True, "exception": None, **data}


def error_response(message: str):
    """Обертка для ответа с ошибкой."""
    return {"success": False, "exception": {"message": message}}


def check_auth(token: str):
    """Проверяет токен и выбрасывает исключение, если неверен."""
    if not server_state.verify_token(token):
        raise ValueError("Invalid access token")


# ==================== API Endpoints ====================

@app.post("/user/register", response_model=RegisterResponse)
async def register(req: RegisterRequest):
    try:
        # Проверка на пустые поля
        if not req.nickname or not req.password:
            raise ValueError("Nickname and password required")

        token = server_state.register_user(req.nickname, req.password)
        return success_response({"nickname": req.nickname, "accessToken": token})
    except Exception as e:
        return error_response(str(e))


@app.post("/set/room/create", response_model=CreateRoomResponse)
async def create_room(req: BaseTokenRequest):
    try:
        check_auth(req.accessToken)
        game_id = await server_state.create_game()
        return success_response({"gameId": game_id})
    except Exception as e:
        return error_response(str(e))


@app.post("/set/room/list", response_model=ListRoomResponse)
async def list_rooms(req: BaseTokenRequest):
    try:
        check_auth(req.accessToken)
        # Получаем список ID активных игр
        games_list = [{"id": gid} for gid in server_state.games.keys()]
        return success_response({"games": games_list})
    except Exception as e:
        return error_response(str(e))


@app.post("/set/room/enter", response_model=BaseResponse)
async def enter_room(req: EnterRoomRequest):
    try:
        check_auth(req.accessToken)
        if server_state.enter_game(req.accessToken, req.gameId):
            return success_response({"gameId": req.gameId})
        else:
            return error_response("Game not found")
    except Exception as e:
        return error_response(str(e))


@app.post("/set/field", response_model=FieldResponse)
async def get_field(req: BaseTokenRequest):
    try:
        check_auth(req.accessToken)
        game = server_state.get_user_game(req.accessToken)
        if not game:
            return error_response("User is not in a game")

        score = game.get_player_score(req.accessToken)

        # Важно: при асинхронности чтение списка безопасно, но
        # для полной строгости можно использовать lock, если поле часто меняется.
        # Здесь возвращаем копию списка для безопасности
        return success_response({
            "cards": list(game.field),
            "status": game.status,
            "score": score
        })
    except Exception as e:
        return error_response(str(e))


@app.post("/set/pick", response_model=PickResponse)
async def pick_set(req: PickRequest):
    try:
        check_auth(req.accessToken)
        game = server_state.get_user_game(req.accessToken)
        if not game:
            return error_response("User is not in a game")

        is_set, new_score = await game.pick_set(req.accessToken, req.cards)
        return success_response({
            "isSet": is_set,
            "score": new_score
        })
    except Exception as e:
        return error_response(str(e))


@app.post("/set/add", response_model=BaseResponse)
async def add_cards(req: BaseTokenRequest):
    try:
        check_auth(req.accessToken)
        game = server_state.get_user_game(req.accessToken)
        if not game:
            return error_response("User is not in a game")

        await game.add_cards_manual()
        return success_response({})
    except Exception as e:
        return error_response(str(e))


@app.post("/set/scores", response_model=ScoresResponse)
async def get_scores(req: BaseTokenRequest):
    try:
        check_auth(req.accessToken)
        game = server_state.get_user_game(req.accessToken)
        if not game:
            return error_response("User is not in a game")

        users_list = []
        for token, score in game.players.items():
            nickname = server_state.get_user_nickname(token) or "Unknown"
            users_list.append(UserScore(name=nickname, score=score))

        # Сортировка по убыванию очков
        users_list.sort(key=lambda x: x.score, reverse=True)
        return success_response({"users": users_list})
    except Exception as e:
        return error_response(str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)