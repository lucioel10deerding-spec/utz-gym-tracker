import os
from gym_tracker.entrypoints.api import app
from gym_tracker.domain.model import GymCapacity
from gym_tracker.services.services import create_gym, enter_gym, get_capacity
from gym_tracker.adapters.storage import load_gyms

def test_saved_gyms_are_loaded():
    create_gym("Reload Gym", 25)

    gyms = load_gyms()

    assert "Reload Gym" in gyms
    assert gyms["Reload Gym"].max_capacity == 25

def test_enter_gym_is_saved_to_file():
    create_gym("Saved Enter Gym", 10)

    enter_gym("Saved Enter Gym")

    gyms = load_gyms()

    assert gyms["Saved Enter Gym"].current_count == 1


def test_new_gym_starts_with_zero_people():
    gym = GymCapacity("McFit Erding", 80)

    assert gym.current_count == 0


def test_enter_increases_current_count_by_one():
    gym = GymCapacity("McFit Erding", 80)

    gym.enter()

    assert gym.current_count == 1


def test_leave_decreases_current_count_by_one():
    gym = GymCapacity("McFit Erding", 80)

    gym.enter()
    gym.leave()

    assert gym.current_count == 0

def test_enter_does_not_go_above_max_capacity():
    gym = GymCapacity("McFit Erding",2 )

    gym.enter()
    gym.enter()
    gym.enter()

    assert gym.current_count == 2

def test_leave_does_not_go_below_zero():
    gym = GymCapacity("McFit Erding",80)

    gym.leave()

    assert gym.current_count == 0

def test_enter_gym_through_service():
    create_gym("McFit Erding",2)

    enter_gym("McFit Erding")
    enter_gym("McFit Erding")
    enter_gym("McFit Erding")

    assert get_capacity("McFit Erding") == {"current": 2, "max": 2}


def test_get_gym_returns_correct_data():
    client = app.test_client()
    name = "Northside Fitness"

    create = client.post("/gyms", json={"name": name, "max_capacity": 50})
    assert create.status_code == 201

    enter = client.post(f"/gyms/{name}/enter")
    assert enter.status_code == 200

    response = client.get(f"/gyms/{name}")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body.keys()) >= {"name", "current", "max", "is_full"}
    assert body["name"] == name
    assert body["current"] == 1
    assert body["max"] == 50
    assert body["is_full"] is False

def test_get_unknown_gym_returns_404():
    client = app.test_client()

    response = client.get("/gyms/Unknown")

    assert response.status_code == 404
def test_leave_gym_through_api():
    client = app.test_client()
    name = "Northside Fitness Leave"

    create = client.post("/gyms", json={"name": name, "max_capacity": 50})
    assert create.status_code == 201

    enter = client.post(f"/gyms/{name}/enter")
    assert enter.status_code == 200

    leave = client.post(f"/gyms/{name}/leave")
    assert leave.status_code == 200

    body = leave.get_json()
    assert body["name"] == name
    assert body["current"] == 0
    assert body["max"] == 50
    assert body["is_full"] is False


def test_leave_unknown_gym_returns_404():
    client = app.test_client()

    response = client.post("/gyms/UnknownGym/leave")

    assert response.status_code == 404

def test_gyms_json_file_exists_after_create():
    create_gym("File Gym", 40)

    assert os.path.exists("gyms.json")