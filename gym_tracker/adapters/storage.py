import json
from pathlib import Path

FILE_PATH = Path("gyms.json")


def save_gyms(gyms):
    data = {
        name: {
            "current": gym.current_count,
            "max": gym.max_capacity,
        }
        for name, gym in gyms.items()
    }

    with open(FILE_PATH, "w") as f:
        json.dump(data, f)


def load_gyms():
    if not FILE_PATH.exists():
        return {}

    with open(FILE_PATH, "r") as f:
        data = json.load(f)

    from gym_tracker.domain.model import GymCapacity

    gyms = {}
    for name, values in data.items():
        gym = GymCapacity(name, values["max"])
        gym.current_count = values["current"]
        gyms[name] = gym

    return gyms
