from flask import Flask, jsonify, request, render_template

from gym_tracker.services.services import (
    create_gym,
    enter_gym,
    leave_gym,
    get_capacity,
)

app = Flask(__name__, template_folder="templates")


@app.post("/gyms")
def create_gym_endpoint():
    data = request.get_json()

    create_gym(
        name=data["name"],
        max_capacity=data["max_capacity"],
    )

    return jsonify(
        {
            "message": "Gym created",
            "name": data["name"],
            "capacity": get_capacity(data["name"]),
        }
    ), 201


@app.get("/gyms/<name>")
def get_gym_endpoint(name):
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
    enter_gym(name)

    return jsonify(
        {
            "message": "Person entered gym",
            "name": name,
            "capacity": get_capacity(name),
        }
    ), 200


@app.route("/gyms/<name>/leave", methods=["POST"])
def leave_gym_endpoint(name):
    capacity = leave_gym(name)

    if capacity is None:
        return {"error": "Gym not found"}, 404

    return {
        "message": "Person left gym",
        "name": name,
        "current": capacity["current"],
        "max": capacity["max"],
        "is_full": capacity["current"] >= capacity["max"],
    }, 200
@app.get("/dashboard/<name>")
def dashboard(name):
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
