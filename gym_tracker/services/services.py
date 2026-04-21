from ..domain.model import GymCapacity


gyms = {}

def create_gym(name : str , max_capacity:int):
    gym = GymCapacity(name,max_capacity)
    gyms[name] = gym
    
def enter_gym(name:str):
    gym = gyms.get(name)
    if gym:
        gym.enter()

def leave_gym(name):
    gym = gyms.get(name)

    if gym is None:
        return None

    gym.leave()
    return {"current": gym.current_count, "max": gym.max_capacity}

def get_capacity(name: str):
    gym = gyms.get(name)
    if gym:
        return {"current": gym.current_count, "max": gym.max_capacity}
    return None
    
