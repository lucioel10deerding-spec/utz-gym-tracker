class GymCapacity:
    def __init__(self, gym_name: str, max_capacity: int):
        self.gym_name = gym_name
        self.max_capacity = max_capacity
        self.current_count = 0

    def enter(self):
        if not self.is_full():
            self.current_count +=1

    def leave(self):
        if self.current_count > 0:
            self.current_count -=1

    def is_full(self):
        return self.current_count >= self.max_capacity




    

