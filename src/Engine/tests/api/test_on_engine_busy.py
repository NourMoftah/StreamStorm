from logging import Logger, getLogger

from pytest import MonkeyPatch, fixture

from fastapi.testclient import TestClient
from fastapi.responses import Response

logger: Logger = getLogger(f"tests.{__name__}")

@fixture(autouse=True)
def set_engine_busy(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BUSY_REASON", "Mocked busy")
    monkeypatch.setenv("BUSY", "1")    
    
    
def test_start_storm(client: TestClient):
    
    response: Response = client.post("/storm/start")
    logger.debug(response.json())

    assert response.status_code == 429
    

def test_create_profiles(client: TestClient):
    
    data: dict = {"count": 1}

    response: Response = client.post("/environment/profiles/create", json=data)
    logger.debug(response.json())

    assert response.status_code == 429
    
def test_delete_all_profiles(client: TestClient):
    
    response: Response = client.post("/environment/profiles/delete")
    logger.debug(response.json())

    assert response.status_code == 429