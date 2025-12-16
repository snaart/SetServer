import pytest
from httpx import AsyncClient, ASGITransport
from main import app

# Используем ASGITransport для тестирования FastAPI приложения напрямую без запуска сервера
transport = ASGITransport(app=app)

@pytest.mark.asyncio
async def test_full_game_flow():
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        
        # 1. Регистрация игрока 1
        resp = await ac.post("/user/register", json={"nickname": "Alice", "password": "123"})
        assert resp.status_code == 200
        data_a = resp.json()
        assert data_a["success"] is True
        token_a = data_a["accessToken"]

        # 2. Регистрация игрока 2
        resp = await ac.post("/user/register", json={"nickname": "Bob", "password": "456"})
        data_b = resp.json()
        assert data_b["success"] is True
        token_b = data_b["accessToken"]

        # 3. Создание комнаты (Alice)
        resp = await ac.post("/set/room/create", json={"accessToken": token_a})
        data_room = resp.json()
        assert data_room["success"] is True
        game_id = data_room["gameId"]

        # 4. Просмотр списка комнат (Bob)
        resp = await ac.post("/set/room/list", json={"accessToken": token_b})
        data_list = resp.json()
        assert len(data_list["games"]) > 0
        assert data_list["games"][0]["id"] == game_id

        # 5. Вход в комнату (Alice и Bob)
        await ac.post("/set/room/enter", json={"accessToken": token_a, "gameId": game_id})
        await ac.post("/set/room/enter", json={"accessToken": token_b, "gameId": game_id})

        # 6. Получение поля
        resp = await ac.post("/set/field", json={"accessToken": token_a})
        field_data = resp.json()
        assert field_data["success"] is True
        cards = field_data["cards"]
        assert len(cards) == 12  # Начальное поле

        # 7. Попытка взять сет (Эмуляция: берем любые 3 карты, скорее всего не сет)
        # В реальном тесте нужно парсить карты и искать настоящий сет, 
        # но для теста сервера достаточно проверить механику запроса.
        pick_ids = [c["id"] for c in cards[:3]]
        
        resp = await ac.post("/set/pick", json={
            "accessToken": token_a,
            "cards": pick_ids
        })
        pick_data = resp.json()
        assert pick_data["success"] is True
        # isSet может быть True или False, главное что сервер ответил
        assert "score" in pick_data

        # 8. Добавление карт
        resp = await ac.post("/set/add", json={"accessToken": token_a})
        assert resp.json()["success"] is True

        # Проверка увеличения поля (если в колоде были карты)
        resp = await ac.post("/set/field", json={"accessToken": token_a})
        new_count = len(resp.json()["cards"])
        # Либо карт стало больше, либо они заместились, 
        # либо колода кончилась (маловероятно в начале игры)
        assert new_count >= 12 

        # 9. Таблица рекордов
        resp = await ac.post("/set/scores", json={"accessToken": token_b})
        scores = resp.json()["users"]
        assert len(scores) == 2
        names = {u["name"] for u in scores}
        assert "Alice" in names
        assert "Bob" in names

@pytest.mark.asyncio
async def test_invalid_token():
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/set/room/create", json={"accessToken": "bad_token"})
        data = resp.json()
        assert data["success"] is False
        assert data["exception"]["message"] == "Invalid access token"