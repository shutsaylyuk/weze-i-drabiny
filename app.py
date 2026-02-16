import random
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from flask import Flask, render_template, redirect, request, jsonify, make_response

app = Flask(__name__)

BOARD_END = 100

SNAKE_LADDERS = {
    9: 27, 16: 7, 18: 37, 28: 51, 25: 54, 56: 64, 59: 17, 63: 19,
    67: 30, 68: 88, 76: 97, 79: 100, 93: 69, 95: 75, 99: 77
}

# --- MAGIC CARDS ---
# None -> wolne
# "p1"/0 -> zajęte przez gracza, który na nim stoi i dostał kartę (żółte dalej świeci)
# "USED" -> zużyte (żółte ma zniknąć)
MAGIC_TILES_TEMPLATE: Dict[int, Optional[str]] = {
    6: None, 14: None, 22: None, 35: None, 47: None, 58: None, 73: None, 86: None
}

CARD_POOL = ["ANTY_WAZ", "TELEPORT_PLUS3"]




class Player:
    def __init__(
        self,
        pid: Any,
        name: str,
        pos: int = 0,
        color: str = "p-red",
        card: Optional[str] = None,
        is_bot: bool = False
    ):
        self.id = pid
        self.name = name
        self.pos = int(pos)
        self.color = color
        self.card = card
        self.is_bot = bool(is_bot)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "pos": int(self.pos),
            "color": self.color,
            "card": self.card,
            "is_bot": self.is_bot
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Player":
        return Player(
            pid=d.get("id"),
            name=d.get("name", "Gracz"),
            pos=int(d.get("pos", 0)),
            color=d.get("color", "p-red"),
            card=d.get("card"),
            is_bot=bool(d.get("is_bot", False))
        )


class MagicTiles:
    """
    Trzyma magic_tiles jako dict[int, state].
    Dla multiplayera zapisujemy jako dict[str, state] (JSON-friendly).
    """
    def __init__(self, initial: Optional[Dict[int, Any]] = None):
        self.tiles: Dict[int, Any] = dict(initial) if initial else MAGIC_TILES_TEMPLATE.copy()

    @staticmethod
    def from_container(container: Dict[str, Any]) -> "MagicTiles":
        mt = container.get("magic_tiles")
        if not mt:
            return MagicTiles({})

        out: Dict[int, Any] = {}
        if isinstance(mt, dict):
            for k, v in mt.items():
                try:
                    out[int(k)] = v
                except Exception:
                    pass
        elif isinstance(mt, list):
            out = {int(x): None for x in mt}
        return MagicTiles(out)

    def write_back(self, container: Dict[str, Any], *, for_json_room: bool) -> None:
        if for_json_room:
            container["magic_tiles"] = {str(k): v for k, v in self.tiles.items()}
        else:
            container["magic_tiles"] = self.tiles

    def active_list(self) -> List[int]:
        return [k for k, v in self.tiles.items() if v != "USED"]


class GameState:
    """
    Hot-seat / AI
    """
    def __init__(self):
        self.players: List[Player] = []
        self.turn: int = 0
        self.last_roll: Optional[int] = None
        self.last_player: int = 0
        self.message: str = ""
        self.history: List[str] = []
        self.move_count: int = 0
        self.mode: str = "hotseat"
        self.card_used: Optional[str] = None
        self.pending: Optional[Dict[str, Any]] = None
        self.last_move: Optional[Dict[str, Any]] = None
        self.magic = MagicTiles(MAGIC_TILES_TEMPLATE.copy())

    def push_history(self, text: str) -> None:
        self.history.append(text)
        self.history = self.history[-8:]

    def anyone_won(self) -> bool:
        return any(int(p.pos) == BOARD_END for p in self.players)

    def to_template_payload(self) -> Dict[str, Any]:
        return {
            "players": [p.to_dict() for p in self.players],
            "turn": self.turn,
            "last_roll": self.last_roll,
            "last_player": self.last_player,
            "message": self.message,
            "history": self.history,
            "move_count": self.move_count,
            "mode": self.mode,
            "pending": self.pending,
            "magic_tiles": self.magic.active_list(),   # lista aktywnych żółtych pól
            "last_move": self.last_move
        }


# =========================
#   Multiplayer storage
# =========================

ROOMS_DIR = Path("data/rooms")
ROOMS_DIR.mkdir(parents=True, exist_ok=True)

def room_path(code: str) -> Path:
    return ROOMS_DIR / f"{code}.json"

def gen_room_code(n=4) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(n))

def load_room(code: str) -> Dict[str, Any]:
    p = room_path(code)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_room(code: str, data: Dict[str, Any]) -> None:
    p = room_path(code)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


# =========================
#   Game logic helpers
# =========================

def is_snake(pos: int) -> bool:
    return pos in SNAKE_LADDERS and SNAKE_LADDERS[pos] < pos

def is_ladder(pos: int) -> bool:
    return pos in SNAKE_LADDERS and SNAKE_LADDERS[pos] > pos


def give_card_if_magic_tile(container: Dict[str, Any], player: Dict[str, Any], *, for_json_room: bool) -> Optional[str]:
    pos = int(player["pos"])
    pid = player.get("id")

    mt = MagicTiles.from_container(container)
    if pos not in mt.tiles:
        return None

    state = mt.tiles.get(pos)
    if state == "USED":
        return None
    if state is not None and state != pid:
        return None
    if player.get("card"):
        return None

    card = random.choice(CARD_POOL)
    player["card"] = card
    mt.tiles[pos] = pid
    mt.write_back(container, for_json_room=for_json_room)
    return f" ✨ Zdobywasz kartę: {card.replace('_', ' ')}"


def mark_magic_tile_used_if_leaving(container: Dict[str, Any], player: Dict[str, Any], start_pos: int, *, for_json_room: bool) -> None:
    pid = player.get("id")
    mt = MagicTiles.from_container(container)
    if start_pos in mt.tiles and mt.tiles.get(start_pos) == pid:
        mt.tiles[start_pos] = "USED"
        mt.write_back(container, for_json_room=for_json_room)


def try_start_snake_pending(container: Dict[str, Any], idx: int, player: Dict[str, Any]) -> bool:
    pos = int(player["pos"])
    if not is_snake(pos):
        return False

    if player.get("card") == "ANTY_WAZ":
        container["pending"] = {
            "type": "snake_choice",
            "player_id": player.get("id", idx),
            "from": pos,
            "to": SNAKE_LADDERS[pos],
        }
        return True

    return False


def apply_snake_if_no_pending(player: Dict[str, Any]) -> Optional[str]:
    pos = int(player["pos"])
    if is_snake(pos):
        to = SNAKE_LADDERS[pos]
        player["pos"] = to
        return f" 🐍 Wąż! {pos} -> {to}"
    return None


def do_one_move_in_container(container: Dict[str, Any], players: List[Dict[str, Any]], player_idx: int, *, for_json_room: bool) -> Tuple[str, int, bool]:
    """
    Surowy ruch: rzut + przesunięcie + drabina automatycznie.
    Wąż tylko opisuje (decyzja/spadek rozstrzygane wyżej).
    + Obsługa: "żółte znika po zejściu" (mark USED), ale tylko gdy ruch jest wykonany.
    """
    player = players[player_idx]
    roll_value = random.randint(1, 6)

    start = int(player["pos"])
    tentative = start + roll_value

    if tentative > BOARD_END:
        msg = f"{player['name']}: wyrzucono {roll_value}. Musisz trafić dokładnie!"
        return msg, roll_value, False

    # ruch się wykona -> jeśli start był magiczny i należał do gracza => USED
    mark_magic_tile_used_if_leaving(container, player, start, for_json_room=for_json_room)

    player["pos"] = tentative
    new_pos = tentative

    if is_ladder(new_pos):
        after = SNAKE_LADDERS[new_pos]
        player["pos"] = after
        msg = f"{player['name']}: wyrzucono {roll_value}. Drabina! {new_pos} -> {after}"
    elif is_snake(new_pos):
        after = SNAKE_LADDERS[new_pos]
        msg = f"{player['name']}: wyrzucono {roll_value}. Wąż! {new_pos} -> {after}"
    else:
        msg = f"{player['name']}: wyrzucono {roll_value}. Ruch: {start} -> {new_pos}"

    if int(player["pos"]) == BOARD_END:
        msg = f"{player['name']}: wyrzucono {roll_value}. Ruch: {start} -> {new_pos}. Meta! Wygrał(a): {player['name']}"
        return msg, roll_value, True

    return msg, roll_value, False


# =========================
#   Global GAME (klasa)
# =========================

GAME = GameState()


# =========================
#   Routes: Hotseat/AI
# =========================

@app.route("/")
def index():
    if not GAME.players:
        return redirect("/new?mode=hotseat&players=2")

    won = GAME.anyone_won()
    n_players = len(GAME.players)
    mc = int(GAME.move_count)
    round_num = 1 if mc == 0 else ((mc - 1) // n_players) + 1

    payload = GAME.to_template_payload()
    payload.update({
        "won": won,
        "round": round_num,
        "snakes_ladders": SNAKE_LADDERS,
    })
    return render_template("index.html", **payload)


@app.route("/roll")
def roll():
    if GAME.anyone_won():
        return redirect("/")

    if not GAME.players:
        return redirect("/new?mode=hotseat&players=2")

    if GAME.pending:
        GAME.message = "Najpierw podejmij decyzję z kartą (wąż)."
        GAME.push_history(GAME.message)
        GAME.last_move = None
        return redirect("/")

    idx = int(GAME.turn)

    if GAME.mode == "ai" and GAME.players[idx].is_bot:
        return redirect("/")

    # pracujemy na dictach (żeby nie ruszać Twojej logiki), ale trzymamy 1 spójny container
    players_dict = [p.to_dict() for p in GAME.players]
    container = {"magic_tiles": GAME.magic.tiles, "pending": GAME.pending}

    from_pos = int(players_dict[idx]["pos"])

    msg, roll_value, won = do_one_move_in_container(container, players_dict, idx, for_json_room=False)

    # ✅ KLUCZOWE: utrwal zmiany w magic_tiles (USED po zejściu)
    GAME.magic.tiles = MagicTiles.from_container(container).tiles
    GAME.pending = container.get("pending")

    # zaktualizuj players w klasach
    GAME.players = [Player.from_dict(d) for d in players_dict]

    if GAME.card_used == "DRUGI_RZUT":
        msg = "DRUGI RZUT ->" + msg
        GAME.card_used = None

    GAME.last_roll = int(roll_value)
    GAME.last_player = idx
    GAME.move_count = int(GAME.move_count) + 1

    # land (po samym rzucie, z zasadą dokładnie 100)
    if from_pos + int(roll_value) > BOARD_END:
        land_pos = from_pos
    else:
        land_pos = from_pos + int(roll_value)

    # pending: decyzja na wężu
    players_dict = [p.to_dict() for p in GAME.players]  # odśwież
    if (not won) and try_start_snake_pending(container, idx, players_dict[idx]):
        GAME.pending = container["pending"]
        GAME.magic.tiles = MagicTiles.from_container(container).tiles  # na wszelki wypadek
        GAME.players = [Player.from_dict(d) for d in players_dict]

        msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
        GAME.message = msg
        GAME.push_history(msg)
        GAME.last_move = None
        return redirect("/")

    # jeśli nie ma pending -> wąż spada
    if not won:
        extra = apply_snake_if_no_pending(players_dict[idx])
        if extra:
            msg += extra

    # karta z magic tile (tu również container może się zmienić)
    if not won:
        extra2 = give_card_if_magic_tile(container, players_dict[idx], for_json_room=False)
        if extra2:
            msg += extra2

    # ✅ utrwal po kartach
    GAME.magic.tiles = MagicTiles.from_container(container).tiles
    GAME.pending = container.get("pending")
    GAME.players = [Player.from_dict(d) for d in players_dict]

    if (not won) and int(roll_value) == 6:
        msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

    GAME.message = msg
    GAME.push_history(msg)

    to_pos = int(GAME.players[idx].pos)

    if (to_pos != from_pos) or (land_pos != from_pos):
        GAME.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": to_pos,
            "move_count": GAME.move_count,
            "won": bool(won),
        }
    else:
        GAME.last_move = None

    if won:
        return redirect("/")

    if int(roll_value) != 6:
        GAME.turn = (idx + 1) % len(GAME.players)

    return redirect("/")


@app.route("/snake_decision", methods=["POST"])
def snake_decision():
    pend = GAME.pending
    if not pend or pend.get("type") != "snake_choice":
        return redirect("/")

    pid = pend["player_id"]
    idx = next((i for i, p in enumerate(GAME.players) if p.id == pid), None)
    if idx is None:
        GAME.pending = None
        return redirect("/")

    choice = request.form.get("choice", "stay")
    pl = GAME.players[idx]

    if choice == "back":
        pl.pos = int(pend["to"])
        msg = f"{pl.name}: wybrał(a) cofnięcie. 🐍 {pend['from']} -> {pend['to']}"
    else:
        pl.card = None
        msg = f"{pl.name}: użył(a) ANTY WĄŻ i zostaje na {pend['from']} ✅"

    GAME.pending = None
    GAME.message = msg
    GAME.push_history(msg)

    if GAME.last_roll != 6:
        GAME.turn = (idx + 1) % len(GAME.players)

    return redirect("/")


@app.route("/use_card", methods=["POST"])
def use_card():
    if not GAME.players:
        return redirect("/")

    if GAME.pending:
        GAME.message = "Najpierw rozwiąż decyzję na wężu."
        GAME.push_history(GAME.message)
        return redirect("/")

    idx = int(GAME.turn)
    card = GAME.players[idx].card
    if not card:
        return redirect("/")

    # Uwaga: działamy na dictach, potem odtwarzamy obiekty (żeby wszystko było spójne)
    players_dict = [p.to_dict() for p in GAME.players]
    container = {"magic_tiles": GAME.magic.tiles, "pending": GAME.pending}

    pl = players_dict[idx]

    if card == "DRUGI_RZUT":
        pl["card"] = None
        GAME.players = [Player.from_dict(d) for d in players_dict]
        GAME.card_used = "DRUGI_RZUT"
        return redirect("/roll")

    if card == "TELEPORT_PLUS3":
        start = int(pl["pos"])
        tentative = start + 3

        if tentative > BOARD_END:
            msg = f"{pl['name']}: TELEPORT +3, ale musisz trafić dokładnie!"
            GAME.message = msg
            GAME.push_history(msg)
            return redirect("/")

        # ✅ zejście z magic tile -> USED
        mark_magic_tile_used_if_leaving(container, pl, start, for_json_room=False)

        pl["card"] = None
        pl["pos"] = tentative
        msg = f"{pl['name']}: używa TELEPORT +3: {start} -> {tentative}"

        land_pos = tentative

        if is_ladder(tentative):
            after = SNAKE_LADDERS[tentative]
            pl["pos"] = after
            msg += f" 🪜 Drabina! {tentative} -> {after}"

        elif is_snake(tentative):
            # pending?
            if try_start_snake_pending(container, idx, pl):
                msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
                GAME.pending = container["pending"]
                GAME.magic.tiles = MagicTiles.from_container(container).tiles
                GAME.players = [Player.from_dict(d) for d in players_dict]
                GAME.message = msg
                GAME.push_history(msg)
                return redirect("/")

            extra = apply_snake_if_no_pending(pl)
            if extra:
                msg += extra

        # karta z magic tile
        extra2 = give_card_if_magic_tile(container, pl, for_json_room=False)
        if extra2:
            msg += extra2

        # ✅ utrwal stan
        GAME.magic.tiles = MagicTiles.from_container(container).tiles
        GAME.pending = container.get("pending")
        GAME.players = [Player.from_dict(d) for d in players_dict]
        GAME.message = msg
        GAME.push_history(msg)

        return redirect("/")

    return redirect("/")


@app.route("/ai_move")
def ai_move():
    if not GAME.players:
        return redirect("/new?mode=ai")

    if GAME.anyone_won():
        return redirect("/")

    idx = int(GAME.turn)
    if not (GAME.mode == "ai" and GAME.players[idx].is_bot):
        return redirect("/")

    players_dict = [p.to_dict() for p in GAME.players]
    container = {"magic_tiles": GAME.magic.tiles, "pending": GAME.pending}

    from_pos = int(players_dict[idx]["pos"])

    msg, roll_value, won = do_one_move_in_container(container, players_dict, idx, for_json_room=False)

    # ✅ KLUCZOWE: utrwal USED po zejściu
    GAME.magic.tiles = MagicTiles.from_container(container).tiles
    GAME.pending = container.get("pending")

    if from_pos + int(roll_value) > BOARD_END:
        land_pos = from_pos
    else:
        land_pos = from_pos + int(roll_value)

    # bot: nie robimy pending decision (ale jeśli jednak kiedyś ustawisz, to nie animujemy)
    if GAME.pending:
        GAME.last_move = None
        GAME.players = [Player.from_dict(d) for d in players_dict]
        return redirect("/")

    if not won:
        extra = apply_snake_if_no_pending(players_dict[idx])
        if extra:
            msg += extra

    if not won:
        extra2 = give_card_if_magic_tile(container, players_dict[idx], for_json_room=False)
        if extra2:
            msg += extra2

    # ✅ utrwal po kartach
    GAME.magic.tiles = MagicTiles.from_container(container).tiles
    GAME.pending = container.get("pending")

    if (not won) and int(roll_value) == 6:
        msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

    GAME.players = [Player.from_dict(d) for d in players_dict]

    GAME.message = msg
    GAME.push_history(msg)

    to_pos = int(GAME.players[idx].pos)

    GAME.move_count = int(GAME.move_count) + 1
    if (from_pos != to_pos) or (from_pos != land_pos):
        GAME.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": to_pos,
            "move_count": GAME.move_count,
            "won": bool(won),
        }
    else:
        GAME.last_move = None

    if won:
        return redirect("/")

    if int(roll_value) != 6:
        GAME.turn = (idx + 1) % len(GAME.players)

    return redirect("/")


@app.route("/new")
def new_game():
    mode = request.args.get("mode", "hotseat")

    if mode == "ai":
        GAME.players = [
            Player(pid=0, name="Ty", pos=0, color="p-red", is_bot=False, card=None),
            Player(pid=1, name="Komputer", pos=0, color="p-blue", is_bot=True, card=None),
        ]
    else:
        n = int(request.args.get("players", 2))
        n = max(2, min(4, n))
        colors = ["p-red", "p-blue", "p-green", "p-purple"]
        GAME.players = []
        for i in range(n):
            GAME.players.append(Player(
                pid=i,
                name=f"Gracz {i+1}",
                pos=0,
                color=colors[i],
                is_bot=False,
                card=None
            ))

    GAME.mode = mode
    GAME.turn = 0
    GAME.last_roll = None
    GAME.last_player = 0
    GAME.message = ""
    GAME.history = []
    GAME.move_count = 0
    GAME.pending = None
    GAME.last_move = None
    GAME.magic = MagicTiles(MAGIC_TILES_TEMPLATE.copy())

    return redirect("/")


@app.route("/set_colors", methods=["POST"])
def set_colors():
    palette = ["p-red", "p-blue", "p-green", "p-purple"]
    used = set()

    for i, pl in enumerate(GAME.players):
        if GAME.mode == "ai" and pl.is_bot:
            continue

        key = f"color_{i}"
        c = request.form.get(key, pl.color)
        if c not in palette:
            c = "p-red"

        if c in used:
            for alt in palette:
                if alt not in used:
                    c = alt
                    break

        pl.color = c
        used.add(c)

    return redirect("/")


@app.route("/howto")
def howto():
    return render_template("howto.html", mode=GAME.mode)


# =========================
#   Multiplayer
# =========================

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

    mt = MagicTiles(MAGIC_TILES_TEMPLATE.copy())
    room = {
        "code": code,
        "created": int(time.time()),
        "players": [
            {"id": "p1", "name": name, "pos": 0, "color": "p-red", "card": None}
        ],
        "turn": 0,
        "last_roll": None,
        "last_player": 0,
        "message": "",
        "history": [],
        "move_count": 0,
        "max_players": max_players,
        "winner": None,
        "magic_tiles": {str(k): v for k, v in mt.tiles.items()},
        "pending": None,
        "last_move": None
    }
    save_room(code, room)

    resp = make_response(redirect(f"/mp/room/{code}"))
    resp.set_cookie(f"mp_{code}_pid", "p1", max_age=60 * 60 * 24 * 7)
    return resp


@app.route("/mp/join", methods=["POST"])
def mp_join():
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "Gracz").strip()[:20]

    room = load_room(code)
    if not room:
        return render_template("mp_lobby.html", error="Nie ma takiego pokoju.")

    if room.get("winner"):
        return render_template("mp_lobby.html", error="Ten pokój jest już zakończony (ktoś wygrał).")

    if len(room.get("players", [])) >= int(room.get("max_players", 2)):
        return render_template("mp_lobby.html", error="Pokój jest pełny.")

    new_id = f"p{len(room['players']) + 1}"
    colors = ["p-red", "p-blue", "p-green", "p-purple"]
    used = {p.get("color") for p in room["players"]}
    color = next((c for c in colors if c not in used), colors[0])

    room["players"].append({"id": new_id, "name": name, "pos": 0, "color": color, "card": None})
    save_room(code, room)

    resp = make_response(redirect(f"/mp/room/{code}"))
    resp.set_cookie(f"mp_{code}_pid", new_id, max_age=60 * 60 * 24 * 7)
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
        for i, p in enumerate(room.get("players", [])):
            if p.get("id") == my_pid:
                my_idx = i
                break

    winner = room.get("winner")
    my_turn = (my_idx is not None and int(room.get("turn", 0)) == my_idx)
    can_roll = (not winner) and my_turn and (len(room.get("players", [])) >= 2)

    return render_template(
        "mp_room.html",
        room=room,
        my_pid=my_pid,
        my_idx=my_idx,
        my_turn=my_turn,
        can_roll=can_roll,
        snakes_ladders=SNAKE_LADDERS
    )


@app.route("/mp/room/<code>/state")
def mp_state(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error": "no_room"}), 404

    room["turn"] = int(room.get("turn", 0))
    room["last_player"] = int(room.get("last_player", 0))
    room["move_count"] = int(room.get("move_count", 0))
    return jsonify(room)


@app.route("/mp/room/<code>/roll", methods=["POST"])
def mp_roll(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error": "no_room"}), 404

    if room.get("winner"):
        return jsonify({"error": "game_over"}), 400

    if room.get("pending"):
        return jsonify({"error": "pending_decision"}), 400

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return jsonify({"error": "no_player_cookie"}), 403

    idx = next((i for i, p in enumerate(room["players"]) if p["id"] == my_pid), None)
    if idx is None:
        return jsonify({"error": "not_in_room"}), 403

    if idx != int(room.get("turn", 0)):
        return jsonify({"error": "not_your_turn"}), 403

    from_pos = int(room["players"][idx]["pos"])

    msg, roll_value, won = do_one_move_in_container(room, room["players"], idx, for_json_room=True)
    roll_value = int(roll_value)

    if from_pos + roll_value > BOARD_END:
        land_pos = from_pos
    else:
        land_pos = from_pos + roll_value

    room["last_roll"] = roll_value
    room["last_player"] = idx
    room["move_count"] = int(room.get("move_count", 0)) + 1

    if (not won) and try_start_snake_pending(room, idx, room["players"][idx]):
        msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
        room["message"] = msg
        room["history"].append(msg)
        room["history"] = room["history"][-8:]
        room["last_move"] = None
        save_room(code, room)
        return redirect(f"/mp/room/{code}")

    if not won:
        extra = apply_snake_if_no_pending(room["players"][idx])
        if extra:
            msg += extra

    if not won:
        extra2 = give_card_if_magic_tile(room, room["players"][idx], for_json_room=True)
        if extra2:
            msg += extra2

    if (not won) and roll_value == 6:
        msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

    room["message"] = msg
    room["history"].append(msg)
    room["history"] = room["history"][-8:]

    to_pos = int(room["players"][idx]["pos"])
    if (to_pos != from_pos) or (land_pos != from_pos):
        room["last_move"] = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": to_pos,
            "move_count": room["move_count"],
            "won": bool(won),
        }
    else:
        room["last_move"] = None

    if won:
        room["winner"] = room["players"][idx]["id"]
    else:
        if roll_value != 6:
            room["turn"] = (idx + 1) % len(room["players"])

    save_room(code, room)
    return redirect(f"/mp/room/{code}")


@app.route("/mp/room/<code>/snake_decision", methods=["POST"])
def mp_snake_decision(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error": "no_room"}), 404

    pend = room.get("pending")
    if not pend or pend.get("type") != "snake_choice":
        return jsonify({"error": "no_pending"}), 400

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return jsonify({"error": "no_player_cookie"}), 403

    if pend.get("player_id") != my_pid:
        return jsonify({"error": "not_your_pending"}), 403

    idx = next((i for i, p in enumerate(room["players"]) if p["id"] == my_pid), None)
    if idx is None:
        return jsonify({"error": "not_in_room"}), 403

    choice = request.form.get("choice", "stay")
    pl = room["players"][idx]

    from_pos = int(pl["pos"])
    land_pos = int(pend["from"])

    if choice == "back":
        pl["pos"] = pend["to"]
        msg = f"{pl['name']}: wybrał(a) cofnięcie. 🐍 {pend['from']} -> {pend['to']}"
    else:
        pl["card"] = None
        msg = f"{pl['name']}: użył(a) ANTY WĄŻ i zostaje na {pend['from']} ✅"

    room["pending"] = None
    room["message"] = msg
    room["history"].append(msg)
    room["history"] = room["history"][-8:]

    to_pos = int(pl["pos"])

    room["move_count"] = int(room.get("move_count", 0)) + 1
    room["last_move"] = {
        "player": idx,
        "from": from_pos,
        "land": land_pos,
        "to": to_pos,
        "move_count": room["move_count"],
        "won": False,
    }

    if room.get("last_roll") != 6:
        room["turn"] = (idx + 1) % len(room["players"])

    save_room(code, room)
    return redirect(f"/mp/room/{code}")


@app.route("/mp/room/<code>/use_card", methods=["POST"])
def mp_use_card(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return jsonify({"error": "no_room"}), 404

    if room.get("winner"):
        return jsonify({"error": "game_over"}), 400

    if room.get("pending"):
        return jsonify({"error": "pending_decision"}), 400

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return jsonify({"error": "no_player_cookie"}), 403

    idx = next((i for i, p in enumerate(room["players"]) if p["id"] == my_pid), None)
    if idx is None:
        return jsonify({"error": "not_in_room"}), 403

    if idx != int(room.get("turn", 0)):
        return jsonify({"error": "not_your_turn"}), 403

    pl = room["players"][idx]
    card = pl.get("card")
    if not card:
        return jsonify({"error": "no_card"}), 400

    if card == "DRUGI_RZUT":
        pl["card"] = None
        msg = f"{pl['name']}: używa DRUGI RZUT 🎲 (rzucasz jeszcze raz)"
        room["message"] = msg
        room["history"].append(msg)
        room["history"] = room["history"][-8:]
        room["last_move"] = None
        save_room(code, room)
        return redirect(f"/mp/room/{code}")

    if card == "TELEPORT_PLUS3":
        from_pos = int(pl["pos"])
        tentative = from_pos + 3
        if tentative > BOARD_END:
            return jsonify({"error": "must_hit_exact"}), 400

        mark_magic_tile_used_if_leaving(room, pl, from_pos, for_json_room=True)

        pl["card"] = None
        pl["pos"] = tentative
        msg = f"{pl['name']}: używa TELEPORT +3: {from_pos} -> {tentative}"

        land_pos = tentative

        if is_ladder(tentative):
            after = SNAKE_LADDERS[tentative]
            pl["pos"] = after
            msg += f" 🪜 Drabina! {tentative} -> {after}"
        elif is_snake(tentative):
            if try_start_snake_pending(room, idx, pl):
                msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
                room["message"] = msg
                room["history"].append(msg)
                room["history"] = room["history"][-8:]
                room["last_move"] = None
                save_room(code, room)
                return redirect(f"/mp/room/{code}")

            extra = apply_snake_if_no_pending(pl)
            if extra:
                msg += extra

        extra2 = give_card_if_magic_tile(room, pl, for_json_room=True)
        if extra2:
            msg += extra2

        room["message"] = msg
        room["history"].append(msg)
        room["history"] = room["history"][-8:]

        to_pos = int(pl["pos"])
        room["move_count"] = int(room.get("move_count", 0)) + 1
        room["last_move"] = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": to_pos,
            "move_count": room["move_count"],
            "won": False,
        }

        save_room(code, room)
        return redirect(f"/mp/room/{code}")

    return jsonify({"error": "card_not_usable_now"}), 400


if __name__ == "__main__":
    app.run(debug=True)
