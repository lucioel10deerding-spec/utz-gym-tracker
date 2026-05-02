import os

ENV = os.getenv("GYM_TRACKER_ENV", "development")
DEBUG = ENV == "development"
DATA_FILE = os.getenv("GYM_TRACKER_DATA_FILE", "gyms.json")
LOG_LEVEL = os.getenv("GYM_TRACKER_LOG_LEVEL", "INFO")
