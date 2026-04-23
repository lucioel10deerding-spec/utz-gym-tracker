import json
from pathlib import Path

from gym_tracker.domain.model import GymCapacity

DATA_FILE = Path("gyms.json")


def save_gyms(gyms):
    data = {}

    for name, gym in gyms.items():
        data[name] = {
            "gym_name": gym.gym_name,
            "max_capacity": gym.max_capacity,
            "current_count": gym.current_count,
        }

    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


def load_gyms():
    if not DATA_FILE.exists():
        return {}

    with open(DATA_FILE, "r") as f:
        data = json.load(f)

    gyms = {}
    for name, gym_data in data.items():
        gym = GymCapacity(
            gym_data["gym_name"],
            gym_data["max_capacity"],
        )
        gym.current_count = gym_data["current_count"]
        gyms[name] = gym

    return gyms
