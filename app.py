import random
from flask import Flask, render_template, redirect, request

app = Flask(__name__)

GAME = {
    "players": [],
    "turn": 0,
    "last_roll": None,
    "last_player": 0,
    "message": "",
    "history": [],
    "move_count": 0,
    "mode": "hotseat",
}

BOARD_END = 100

SNAKE_LADDERS = {
    9: 27, 16: 7, 18: 37, 28: 51, 25: 54, 56: 64, 59: 17, 63: 19,
    67: 30, 68: 88, 76: 97, 79: 100, 93: 69, 95: 75, 99: 77
}


def apply_snake_or_ladder(pos: int) -> int:
    return SNAKE_LADDERS.get(pos, pos)


def push_history(text: str) -> None:
    GAME["history"].append(text)
    GAME["history"] = GAME["history"][-8:]


def do_one_move(player_idx: int) -> tuple[str, int, bool]:
    """Zwraca: (msg, roll_value, won)"""
    player = GAME["players"][player_idx]
    roll_value = random.randint(1, 6)


    start = int(player["pos"])
    tentative = start + roll_value

    # nietrafienie dokładnie
    if tentative > BOARD_END:
        msg = f"{player['name']}: wyrzucono {roll_value}. Musisz trafić dokładnie!"
        return msg, roll_value, False

    new_pos = tentative
    after = apply_snake_or_ladder(new_pos)

    if after != new_pos:
        if after > new_pos:
            msg = f"{player['name']}: wyrzucono {roll_value}. Drabina! {new_pos} -> {after}"
        else:
            msg = f"{player['name']}: wyrzucono {roll_value}. Wąż! {new_pos} -> {after}"
        new_pos = after
    else:
        msg = f"{player['name']}: wyrzucono {roll_value}. Ruch: {start} -> {new_pos}"

    player["pos"] = new_pos

    if new_pos == BOARD_END:
        msg = f"Meta! Wygrał(a): {player['name']}"
        return msg, roll_value, True

    if player.get("is_bot"):
        msg = "🤖 " + msg

    return msg, roll_value, False


@app.route("/")
def index():
    if not GAME["players"]:
        return redirect("/new?mode=hotseat&players=2")

    won = any(p["pos"] == BOARD_END for p in GAME["players"])
    n_players = len(GAME["players"])
    mc = GAME.get("move_count", 0)

    # Twoja logika rund: runda zmienia się dopiero przy "kolejnym kliknięciu"
    if mc == 0:
        round_num = 1
    else:
        round_num = ((mc - 1) // n_players) + 1

    return render_template(
        "index.html",
        players=GAME["players"],
        turn=GAME["turn"],
        last_roll=GAME["last_roll"],
        last_player=GAME.get("last_player", 0),
        message=GAME["message"],
        won=won,
        round=round_num,
        history=GAME["history"],
        mode=GAME.get("mode", "hotseat"),
        snakes_ladders=SNAKE_LADDERS,
    )


@app.route("/roll")
def roll():
    # blokada po wygranej
    if any(p["pos"] == BOARD_END for p in GAME["players"]):
        return redirect("/")

    if not GAME["players"]:
        return redirect("/new?mode=hotseat&players=2")

    idx = GAME["turn"]

    # jeśli jest tryb AI i akurat tura bota -> przekieruj do ruchu bota
    if GAME.get("mode") == "ai" and GAME["players"][idx].get("is_bot", False):
        return redirect("/")

    msg, roll_value, won = do_one_move(idx)

    if (not won) and roll_value == 6:
        msg += " 🎲 Bonus: 6 → dodatkowy rzut!"
    GAME["last_roll"] = roll_value
    GAME["message"] = msg
    push_history(msg)

    GAME["last_player"] = idx
    GAME["move_count"] += 1

    if won:
        return redirect("/")

    # zmiana tury
    if roll_value != 6:
        GAME["turn"] = (idx + 1) % len(GAME["players"])

    return redirect("/")


@app.route("/ai_move")
def ai_move():
    if GAME.get("mode") != "ai":
        return redirect("/")

    if any(p["pos"] == BOARD_END for p in GAME["players"]):
        return redirect("/")

    idx = GAME["turn"]
    if not GAME["players"][idx].get("is_bot", False):
        return redirect("/")

    msg, roll_value, won = do_one_move(idx)

    GAME["last_roll"] = roll_value
    GAME["message"] = msg
    push_history(msg)

    GAME["last_player"] = idx
    GAME["move_count"] += 1

    if not won:
        if roll_value != 6:
            GAME["turn"] = (idx + 1) % len(GAME["players"])
        else:
            msg+=  " 🎲 Bonus: 6 → dodatkowy rzut!"
            push_history(msg)

    return redirect("/")


@app.route("/new")
def new_game():
    mode = request.args.get("mode", "hotseat")

    if mode == "ai":
        GAME["players"] = [
            {"id": 0, "name": "Ty", "pos": 0, "color": "p-red", "is_bot": False},
            {"id": 1, "name": "Komputer", "pos": 0, "color": "p-blue", "is_bot": True},
        ]
    else:
        n = int(request.args.get("players", 2))
        n = max(2, min(4, n))
        colors = ["p-red", "p-blue", "p-green", "p-purple"]

        GAME["players"] = []
        for i in range(n):
            GAME["players"].append({
                "id": i,
                "name": f"Gracz {i+1}",
                "pos": 0,
                "color": colors[i],
                "is_bot": False
            })

    GAME["mode"] = mode
    GAME["turn"] = 0
    GAME["last_roll"] = None
    GAME["last_player"] = 0
    GAME["message"] = ""
    GAME["history"] = []
    GAME["move_count"] = 0

    return redirect("/")


@app.route("/set_colors", methods=["POST"])
def set_colors():
    palette = ["p-red", "p-blue", "p-green", "p-purple"]

    used = set()
    for i, pl in enumerate(GAME["players"]):
        if GAME.get("mode") == "ai" and pl.get("is_bot"):
            continue
        key = f"color_{i}"
        c = request.form.get(key, pl.get("color", "p-red"))

        if c not in palette:
            c = "p-red"

        # blokada duplikatów
        if c in used:
            for alt in palette:
                if alt not in used:
                    c = alt
                    break

        pl["color"] = c
        used.add(c)

    return redirect("/")

@app.route("/howto")
def howto():
    return render_template("howto.html", mode=GAME.get("mode","hotseat"))

if __name__ == "__main__":
    app.run(debug=True)
