import logging

from flask import Flask, jsonify, render_template, request

from gym_tracker import config
from gym_tracker.services.services import (
    create_gym,
    enter_gym,
    leave_gym,
    get_capacity,
)

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)
app = Flask(__name__, template_folder="templates")


@app.post("/gyms")
def create_gym_endpoint():
    logger.info("POST /gyms called")
    data = request.get_json(silent=True) or {}

    name = data.get("name")
    max_capacity = data.get("max_capacity")
    if name is None or max_capacity is None:
        return jsonify({"error": "Missing required fields"}), 400

    capacity = create_gym(name=name, max_capacity=max_capacity)

    return jsonify(
        {
            "message": "Gym created",
            "name": name,
            "capacity": capacity,
        }
    ), 201


@app.get("/gyms/<name>")
def get_gym_endpoint(name):
    logger.info(f"GET /gyms/{name} called")
    capacity = get_capacity(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(
        {
            "name": name,
            "current": capacity["current"],
            "max": capacity["max"],
            "is_full": capacity["current"] >= capacity["max"],
        }
    ), 200


@app.post("/gyms/<name>/enter")
def enter_gym_endpoint(name):
    logger.info(f"POST /gyms/{name}/enter called")
    capacity = enter_gym(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(
        {
            "message": "Person entered gym",
            "name": name,
            "capacity": capacity,
        }
    ), 200


@app.route("/gyms/<name>/leave", methods=["POST"])
def leave_gym_endpoint(name):
    logger.info(f"POST /gyms/{name}/leave called")
    capacity = leave_gym(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(
        {
            "message": "Person left gym",
            "name": name,
            "current": capacity["current"],
            "max": capacity["max"],
            "is_full": capacity["current"] >= capacity["max"],
        }
    ), 200


@app.get("/dashboard/<name>")
def dashboard(name):
    logger.info(f"GET /dashboard/{name} called")
    capacity = get_capacity(name)

    if capacity is None:
        return "Gym not found", 404

    current = capacity["current"]
    max_capacity = capacity["max"]

    if current >= max_capacity:
        status = "FULL"
    elif current >= max_capacity * 0.7:
        status = "BUSY"
    else:
        status = "NORMAL"

    return render_template(
        "dashboard.html",
        name=name,
        current=current,
        max=max_capacity,
        status=status,
    )


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500
