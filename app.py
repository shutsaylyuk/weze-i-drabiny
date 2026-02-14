import random
import os, json, time, string
from pathlib import Path
from flask import Flask, render_template, redirect, request, jsonify, make_response

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

ROOMS_DIR = Path("data/rooms")
ROOMS_DIR.mkdir(parents=True, exist_ok=True)

def room_path(code: str) -> Path:
    return ROOMS_DIR / f"{code}.json"

def gen_room_code(n=4) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(n))

def load_room(code:str) -> dict:
    p = room_path(code)
    if not p.exists():
        return {}
    with p.open("r", encoding = "utf-8") as f:
        return json.load(f)

def save_room(code: str, data: dict) -> None:
    p = room_path(code)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding = "utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent =2)
    tmp.replace(p)

def apply_snake_or_ladder_room(pos: int) -> int:
    return SNAKE_LADDERS.get(pos, pos)

def do_one_move_room(room:dict, player_idx: int) -> tuple[str, int, bool]:
    """Zwraca: (msg, roll_value, won)"""
    player = room["players"][player_idx]
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

    return msg, roll_value, False

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

@app.route("/mp")
def mp_lobby():
    return render_template("mp_lobby.html")

@app.route("/mp/create", methods=["POST"])
def mp_create():
    name = (request.form.get("name") or "Gracz").strip()[:20]
    max_players = int(request.form.get("players") or 2)
    max_players = max(2, min(4, max_players))

    code = gen_room_code()
    while room_path(code).exists():
        code = gen_room_code()

    room = {
        "code": code,
        "created":int(time.time()),
        "players": [
            {"id": "p1", "name" : name, "pos" : 0, "color" : "p-red"}
        ],
        "turn" : 0,
        "last_roll" : None,
        "last_player" : 0,
        "message" : "",
        "history" : [],
        "move_count" : 0,
        "max_players" : max_players,
        "winner" : None
    }
    save_room(code,room)

    resp = make_response(redirect(f"/mp/room/{code}"))
    resp.set_cookie(f"mp_{code}_pid", "p1", max_age=60*60*24*7)
    return resp

@app.route("/mp/join", methods=["POST"])
def mp_join():
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "Gracz").strip()[:20]

    room = load_room(code)
    if not room:
        return render_template("mp_lobby.html", error="Nie ma takiego pokoju.")

    if room.get("winner"):
        return render_template("mp_lobby.html", error= "Ten pokój jest już zakończony (ktoś wygrał).")

    if len(room["players"]) >= room.get("max_players", 2):
        return render_template("mp_lobby.html", error= "Pokój jest pełny.")

    new_id = f"p{len(room['players'])+1}"
    colors = ["p-red", "p-blue", "p-green", "p-purple"]
    used = {p.get("color") for p in room["players"]}
    color = next((c for c in colors if c not in used), colors[0])

    room["players"].append({"id" : new_id, "name": name, "pos": 0, "color" : color})
    save_room(code,room)

    resp = make_response(redirect(f"/mp/room/{code}"))
    resp.set_cookie(f"mp_{code}_pid", new_id, max_age=60*60*24*7)
    return resp

@app.route("/mp/room/<code>")
def mp_room(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return redirect("/mp")

    my_pid = request.cookies.get(f"mp_{code}_pid")

    my_idx = None

    if my_pid:
        for i,p in enumerate(room.get("players",[])):
            if p.get("id") == my_pid:
                my_idx = i
                break

    winner = room.get("winner")
    my_turn = (my_idx is not None and room.get("turn") == my_idx)
    can_roll = (not winner) and my_turn and (len(room.get("players",[])) >=2)


    return render_template(
        "mp_room.html",
        room=room,
        my_pid=my_pid,
        my_idx = my_idx,
        my_turn = my_turn,
        can_roll = can_roll,
        snakes_ladders=SNAKE_LADDERS
    )

@app.route("/mp/room/<code>/state")
def mp_state(code):
    code=code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error" : "no_room"}), 404

    room["turn"] = int(room.get("turn", 0))
    room["last_player"] = int(room.get("last_player", 0))
    room["move_count"] = int(room.get("move_count", 0))
    return jsonify(room)

@app.route("/mp/room/<code>/roll", methods=["POST"])
def mp_roll(code):
    code=code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error" : "no_room"}), 404

    if room.get("winner"):
        return jsonify({"error" : "game_over"}), 400

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return jsonify({"error" : "no_player_cookie"}), 403

    idx = next((i for i, p in enumerate(room["players"]) if p["id"] == my_pid), None)
    if idx is None:
        return jsonify({"error" : "not_in_room"}), 403

    turn_idx = int(room.get("turn", 0))
    if idx != turn_idx:
        return jsonify({"error": "not_your_turn", "idx": idx, "turn": room.get("turn"), "turn_int" : turn_idx}), 403

    msg, roll_value, won = do_one_move_room(room, idx)

    if (not won) and roll_value==6:
        msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

    room["last_roll"] = roll_value
    room["message"] = msg
    room["history"].append(msg)
    room["history"] = room["history"][-8:]
    room["last_player"] = idx
    room["move_count"] += 1

    if won:
        room["winner"] = room["players"][idx]["id"]
    else:
        if roll_value != 6:
            room["turn"] = (idx + 1) % len(room["players"])

    save_room(code, room)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True)




