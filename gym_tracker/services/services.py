from ..domain.model import GymCapacity
from ..adapters.storage import load_gyms, save_gyms


gyms = load_gyms()


def create_gym(name: str, max_capacity: int):
    gym = GymCapacity(name, max_capacity)
    gyms[name] = gym
    save_gyms(gyms)


def enter_gym(name: str):
    gym = gyms.get(name)

    if gym:
        gym.enter()
        save_gyms(gyms)


def leave_gym(name: str):
    gym = gyms.get(name)

    if gym is None:
        return None

    gym.leave()
    save_gyms(gyms)

    return {
        "current": gym.current_count,
        "max": gym.max_capacity,
    }


def get_capacity(name: str):
    gym = gyms.get(name)

    if gym:
        return {
            "current": gym.current_count,
            "max": gym.max_capacity,
        }

    return None
    
