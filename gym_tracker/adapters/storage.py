import json
import os

from gym_tracker import config
from gym_tracker.domain.model import GymCapacity

DATA_FILE = config.DATA_FILE


def save_gyms(gyms):
    data = {}

    for name, gym in gyms.items():
        data[name] = {
            "name": gym.gym_name,
            "max_capacity": gym.max_capacity,
            "current_count": gym.current_count,
        }

    with open(DATA_FILE, "w") as file:
        json.dump(data, file)


def load_gyms():
    if not os.path.exists(DATA_FILE):
        return {}

    with open(DATA_FILE, "r") as file:
        data = json.load(file)

    gyms = {}

    for name, gym_data in data.items():
        gym_name = gym_data.get("name", gym_data.get("gym_name", name))
        gym = GymCapacity(
            gym_name,
            gym_data["max_capacity"]
        )
        gym.current_count = gym_data.get("current_count", 0)
        gyms[name] = gym

    return gyms
