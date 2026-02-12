import random
from flask import Flask, render_template, redirect
app = Flask(__name__)

GAME = {
    "players":[
        {"id":0, "name" : "Gracz 1", "pos" : 1, "color" : "p-red"},
        {"id":1, "name" : "Gracz 2", "pos" : 2, "color" : "p-blue"},
    ],
    "turn" : 0,
    "last_roll" : None,
    "message" : "",
    "history" : []
}

BOARD_END = 100

SNAKE_LADDERS = {
    9 : 27, 16 : 7, 18 : 37, 28 : 51, 25 : 54, 56 : 64, 59 : 17, 63 : 19,
    67 : 30, 68 : 88, 76 : 97, 79 : 100, 93 : 69, 95 : 75, 99 : 77
}

def apply_snake_or_ladder(pos):
    return SNAKE_LADDERS.get(pos, pos)
@app.route('/')
def index():
    won = any(p["pos"] == BOARD_END for p in GAME["players"])
    return render_template(
        "index.html",
        players=GAME["players"],
        turn=GAME["turn"],
        last_roll=GAME["last_roll"],
        message=GAME["message"],
        won = won,
        snakes_ladders=SNAKE_LADDERS
    )
@app.route("/roll")
def roll():
    current = GAME["players"][GAME["turn"]]

    roll_value = random.randint(1,6)
    GAME["last_roll"] = roll_value
    GAME["message"] = ""

    start = current["pos"]
    tentative = start + roll_value

    if tentative > BOARD_END:
        GAME["message"] = f"{p['name']}: wyrzucono {roll_value}. Musisz trafić dokładnie!"
        GAME["turn"] = (GAME["turn"] + 1) % len(GAME["players"])
        return redirect("/")

    new_pos = tentative
    after = apply_snake_or_ladder(new_pos)

    if after != new_pos:
        if after > new_pos:
            GAME["message"] = f"Wyrzucono {roll_value}. Drabina! {new_pos} -> {after}"
        else:
            GAME["message"] = f"Wyrzucono {roll_value}. Wąż! {new_pos} -> {after}"
        new_pos = after
    else:
        GAME["message"] = f"Wyrzucono {roll_value}. Ruch: {start} -> {new_pos}"

    current["pos"] = new_pos

    if current["pos"] == BOARD_END:
        GAME["message"] = f"Meta! Wygrał(a): {current['name']}"

    if current["pos"] != BOARD_END:
        GAME["turn"] = (GAME["turn"] + 1) % len(GAME["players"])

    return redirect("/")
@app.route("/new")
def new_game():
    for p in GAME["players"]:
        p["pos"] = 1
    GAME["turn"] = 0
    GAME["last_roll"] = None
    GAME["message"] = ""
    GAME["history"] = []
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)