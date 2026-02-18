import random
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from flask import Flask, render_template, redirect, request, jsonify, make_response

app = Flask(__name__)

@app.after_request
def add_header(response):
    # Jeśli to plik statyczny (CSS, JS, obrazki), cache'uj go na rok
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'public, max-age=31536000'
    else:
        # Stanu gry nie cache'uj w ogóle, żeby zawsze był świeży
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

BOARD_END = 100

SNAKE_LADDERS = {
    9: 27, 16: 7, 18: 37, 28: 51, 25: 54, 56: 64, 59: 17, 63: 19,
    67: 30, 68: 88, 76: 97, 79: 100, 93: 69, 95: 75, 99: 77
}

MAGIC_TILES_TEMPLATE: Dict[int, Optional[str]] = {
    6: None, 14: None, 22: None, 35: None, 47: None, 58: None, 73: None, 86: None
}

CARD_POOL = ["ANTY_WAZ", "TELEPORT_PLUS3"]


def is_snake(pos: int) -> bool:
    return pos in SNAKE_LADDERS and SNAKE_LADDERS[pos] < pos

def is_ladder(pos: int) -> bool:
    return pos in SNAKE_LADDERS and SNAKE_LADDERS[pos] > pos

def gen_room_code(n=4) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(n))


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
    def __init__(self, initial: Optional[Dict[int, Any]] = None):
        self.tiles: Dict[int, Any] = dict(initial) if initial else MAGIC_TILES_TEMPLATE.copy()

    @staticmethod
    def from_any(mt: Any) -> "MagicTiles":
        if not mt:
            return MagicTiles(MAGIC_TILES_TEMPLATE.copy())

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

    def to_json_dict(self) -> Dict[str, Any]:
        return {str(k): v for k, v in self.tiles.items()}

    def active_list(self) -> List[int]:
        return [k for k, v in self.tiles.items() if v != "USED"]


class Game:
    def __init__(self, mode: str = "hotseat"):
        self.mode: str = mode
        self.players: List[Player] = []
        self.turn: int = 0

        self.last_roll: Optional[int] = None
        self.last_player: int = 0
        self.message: str = ""
        self.history: List[str] = []
        self.move_count: int = 0

        self.pending: Optional[Dict[str, Any]] = None
        self.last_move: Optional[Dict[str, Any]] = None

        self.magic: MagicTiles = MagicTiles(MAGIC_TILES_TEMPLATE.copy())

        # Multiplayer
        self.max_players: int = 2
        self.winner: Optional[str] = None
        self.rolls_in_turn: int = 0

        # czyszczenie pamięci RAM
        self.updated_at: float = time.time()

    def touch(self) -> None:
        self.updated_at = time.time()

    def push_history(self, text: str) -> None:
        self.history.append(text)
        self.history = self.history[-8:]

    def anyone_won(self) -> bool:
        return any(int(p.pos) == BOARD_END for p in self.players)

    def current_index(self) -> int:
        return int(self.turn) % max(1, len(self.players))

    #Główne zasady

    def _mark_magic_tile_used_if_leaving(self, pid: Any, start_pos: int) -> None:
        if start_pos in self.magic.tiles and self.magic.tiles.get(start_pos) == pid:
            self.magic.tiles[start_pos] = "USED"

    def _give_card_if_magic_tile(self, p: Player) -> Optional[str]:
        pos = int(p.pos)
        pid = p.id

        if pos not in self.magic.tiles:
            return None

        state = self.magic.tiles.get(pos)
        if state == "USED":
            return None
        if state is not None and state != pid:
            return None
        if p.card:
            return None

        card = random.choice(CARD_POOL)
        p.card = card
        self.magic.tiles[pos] = pid
        return f" ✨ Zdobywasz kartę: {card.replace('_', ' ')}"

    def _try_start_snake_pending(self, p: Player, idx: int) -> bool:
        pos = int(p.pos)
        if not is_snake(pos):
            return False
        if p.card == "ANTY_WAZ":
            self.pending = {
                "type": "snake_choice",
                "player_id": p.id if p.id is not None else idx,
                "from": pos,
                "to": SNAKE_LADDERS[pos],
            }
            return True
        return False

    def _apply_snake_if_no_pending(self, p: Player) -> Optional[str]:
        pos = int(p.pos)
        if is_snake(pos):
            to = SNAKE_LADDERS[pos]
            p.pos = to
            return f" 🐍 Wąż! {pos} -> {to}"
        return None

    def _raw_move(self, idx: int) -> Tuple[str, int, bool, int, int, int]:

        p = self.players[idx]
        roll_value = random.randint(1, 6)

        start = int(p.pos)
        tentative = start + roll_value

        if tentative > BOARD_END:
            msg = f"{p.name}: wyrzucono {roll_value}. Musisz trafić dokładnie!"
            return msg, roll_value, False, start, start, start

        # żółte znika po zejściu
        self._mark_magic_tile_used_if_leaving(p.id, start)

        p.pos = tentative
        land_pos = tentative

        msg = f"{p.name}: wyrzucono {roll_value}. Ruch: {start} -> {land_pos}"

        if is_ladder(land_pos):
            after = SNAKE_LADDERS[land_pos]
            p.pos = after
            msg = f"{p.name}: wyrzucono {roll_value}. Drabina! {land_pos} -> {after}"
        elif is_snake(land_pos):
            after = SNAKE_LADDERS[land_pos]
            msg = f"{p.name}: wyrzucono {roll_value}. Wąż! {land_pos} -> {after}"

        won = int(p.pos) == BOARD_END
        if won:
            msg = f"{p.name}: wyrzucono {roll_value}. Ruch: {start} -> {land_pos}. Meta! Wygrał(a): {p.name}"

        return msg, roll_value, won, start, land_pos, int(p.pos)


    def roll(self) -> None:

        self.touch()

        if not self.players:
            self.message = "Brak graczy."
            return

        if self.anyone_won():
            self.message = "Gra zakończona."
            return

        if self.pending:
            self.message = "Najpierw podejmij decyzję z kartą (wąż)."
            self.push_history(self.message)
            self.last_move = None
            return

        idx = self.current_index()

        if self.mode == "ai" and self.players[idx].is_bot:
            self.message = "Teraz ruch komputera…"
            self.push_history(self.message)
            return

        msg, roll_value, won, from_pos, land_pos, to_pos = self._raw_move(idx)

        self.last_roll = int(roll_value)
        self.last_player = idx
        self.move_count += 1

        #ANTY_WAZ
        if (not won) and self._try_start_snake_pending(self.players[idx], idx):
            msg += " 🃏 Masz ANTY WĄŻ - wybierz: zostać czy cofnąć się?"
            self.message = msg
            self.push_history(msg)
            self.last_move = None
            return

        #Wąż
        if not won:
            extra = self._apply_snake_if_no_pending(self.players[idx])
            if extra:
                msg += extra
                to_pos = int(self.players[idx].pos)

        # give card
        if not won:
            extra2 = self._give_card_if_magic_tile(self.players[idx])
            if extra2:
                msg += extra2

        # bonus 6
        if (not won) and int(roll_value) == 6:
            msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

        # last_move
        self.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": int(self.players[idx].pos),
            "move_count": self.move_count,
            "won": bool(won),
        } if (from_pos != int(self.players[idx].pos) or from_pos != land_pos) else None

        self.message = msg
        self.push_history(msg)

        # change turn unless 6
        if not won and int(roll_value) != 6:
            self.turn = (idx + 1) % len(self.players)

    def snake_decision(self, player_id: Any, choice: str) -> None:

        self.touch()

        pend = self.pending
        if not pend or pend.get("type") != "snake_choice":
            return

        # znajdź gracza
        idx = next((i for i, p in enumerate(self.players) if p.id == player_id), None)
        if idx is None:
            self.pending = None
            return

        pl = self.players[idx]
        from_pos = int(pl.pos)
        land_pos = int(pend["from"])

        if choice == "back":
            pl.pos = int(pend["to"])
            msg = f"{pl.name}: wybrał(a) cofnięcie. 🐍 {pend['from']} -> {pend['to']}"
        else:
            pl.card = None
            msg = f"{pl.name}: użył(a) ANTY WĄŻ i zostaje na {pend['from']} ✅"

        self.pending = None
        self.message = msg
        self.push_history(msg)

        # last_move (decyzja to też ruch)
        self.move_count += 1
        self.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": int(pl.pos),
            "move_count": self.move_count,
            "won": False,
        }

        # tura: tylko jeśli poprzedni rzut nie dawał bonusu
        if self.last_roll != 6:
            self.turn = (idx + 1) % len(self.players)

    def use_card(self) -> None:
        #Obsługa karty TELEPORT3
        self.touch()

        if not self.players or self.anyone_won():
            return

        if self.pending:
            self.message = "Najpierw rozwiąż decyzję na wężu."
            self.push_history(self.message)
            return

        idx = self.current_index()
        pl = self.players[idx]
        card = pl.card
        if not card:
            return

        if card == "TELEPORT_PLUS3":
            start = int(pl.pos)
            tentative = start + 3

            if tentative > BOARD_END:
                msg = f"{pl.name}: TELEPORT +3, ale musisz trafić dokładnie!"
                self.message = msg
                self.push_history(msg)
                return

            self._mark_magic_tile_used_if_leaving(pl.id, start)

            pl.card = None
            pl.pos = tentative
            msg = f"{pl.name}: używa TELEPORT +3: {start} -> {tentative}"
            land_pos = tentative

            if is_ladder(tentative):
                after = SNAKE_LADDERS[tentative]
                pl.pos = after
                msg += f" 🪜 Drabina! {tentative} -> {after}"

            elif is_snake(tentative):
                if self._try_start_snake_pending(pl, idx):
                    msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
                    self.message = msg
                    self.push_history(msg)
                    self.last_move = None
                    return

                extra = self._apply_snake_if_no_pending(pl)
                if extra:
                    msg += extra

            extra2 = self._give_card_if_magic_tile(pl)
            if extra2:
                msg += extra2

            self.move_count += 1
            self.last_move = {
                "player": idx,
                "from": start,
                "land": land_pos,
                "to": int(pl.pos),
                "move_count": self.move_count,
                "won": False,
            }

            self.message = msg
            self.push_history(msg)
            return

    def ai_move(self) -> None:

        self.touch()

        if not self.players or self.anyone_won():
            return

        idx = self.current_index()
        if not (self.mode == "ai" and self.players[idx].is_bot):
            return

        bot = self.players[idx]

        # bot może użyć teleportu na początku tury
        if bot.card == "TELEPORT_PLUS3" and not self.pending:
            start = int(bot.pos)
            tentative = start + 3
            if tentative <= BOARD_END:
                self._mark_magic_tile_used_if_leaving(bot.id, start)
                bot.card = None
                bot.pos = tentative

                msg = f"{bot.name}: używa TELEPORT +3: {start} -> {tentative}"
                land_pos = tentative

                if is_ladder(tentative):
                    after = SNAKE_LADDERS[tentative]
                    bot.pos = after
                    msg += f" 🪜 Drabina! {tentative} -> {after}"
                elif is_snake(tentative):
                    extra = self._apply_snake_if_no_pending(bot)
                    if extra:
                        msg += extra

                extra2 = self._give_card_if_magic_tile(bot)
                if extra2:
                    msg += extra2

                self.move_count += 1
                self.last_roll = None
                self.last_player = idx
                self.last_move = {
                    "player": idx,
                    "from": start,
                    "land": land_pos,
                    "to": int(bot.pos),
                    "move_count": self.move_count,
                    "won": bool(int(bot.pos) == BOARD_END),
                }

                self.message = msg
                self.push_history(msg)

                if int(bot.pos) != BOARD_END:
                    self.turn = (idx + 1) % len(self.players)
                return

        # normalny rzut
        msg, roll_value, won, from_pos, land_pos, to_pos = self._raw_move(idx)

        self.last_roll = int(roll_value)
        self.last_player = idx
        self.move_count += 1

        # BOT z kartą ANTY_WAZ
        pos_after = int(self.players[idx].pos)
        if (not won) and is_snake(pos_after):
            if self.players[idx].card == "ANTY_WAZ":
                self.players[idx].card = None
                msg += f" 🃏 BOT używa ANTY WĄŻ i zostaje na {pos_after} ✅"
            else:
                extra = self._apply_snake_if_no_pending(self.players[idx])
                if extra:
                    msg += extra

        if not won:
            extra2 = self._give_card_if_magic_tile(self.players[idx])
            if extra2:
                msg += extra2

        if (not won) and int(roll_value) == 6:
            msg += " 🎲 Bonus: 6 → dodatkowy rzut!"

        self.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": int(self.players[idx].pos),
            "move_count": self.move_count,
            "won": bool(won),
        } if (from_pos != int(self.players[idx].pos) or from_pos != land_pos) else None

        self.message = msg
        self.push_history(msg)

        if not won and int(roll_value) != 6:
            self.turn = (idx + 1) % len(self.players)


    #MULTIPLAYER

    def mp_roll(self, my_pid: str) -> None:

        self.touch()

        if self.winner:
            self.message = "Gra zakończona."
            return

        if self.pending:
            self.message = "Najpierw podejmij decyzję na wężu."
            return

        idx = next((i for i, p in enumerate(self.players) if p.id == my_pid), None)
        if idx is None:
            self.message = "Nie jesteś w tym pokoju."
            return

        if idx != int(self.turn):
            self.message = "Nie twoja tura."
            return

        if self.rolls_in_turn >= 3:
            self.turn = (idx + 1) % len(self.players)
            self.rolls_in_turn = 0
            self.message = "Limit 3 rzutów w turze — koniec tury."
            self.push_history(self.message)
            return

        msg, roll_value, won, from_pos, land_pos, to_pos = self._raw_move(idx)
        roll_value = int(roll_value)

        self.last_roll = roll_value
        self.last_player = idx
        self.move_count += 1

        self.rolls_in_turn += 1

        # snake pending (ANTY_WAZ)
        if (not won) and self._try_start_snake_pending(self.players[idx], idx):
            msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
            self.message = msg
            self.push_history(msg)
            self.last_move = None
            return

        # apply snake
        if not won:
            extra = self._apply_snake_if_no_pending(self.players[idx])
            if extra:
                msg += extra

        # give card
        if not won:
            extra2 = self._give_card_if_magic_tile(self.players[idx])
            if extra2:
                msg += extra2

        # bonus / limit message
        if (not won) and roll_value == 6 and self.rolls_in_turn < 3:
            msg += " 🎲 Bonus: 6 → dodatkowy rzut!"
        elif (not won) and roll_value == 6 and self.rolls_in_turn >= 3:
            msg += " 🎲 Wypadło 6, ale limit 3 rzutów — koniec tury."

        self.message = msg
        self.push_history(msg)

        self.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": int(self.players[idx].pos),
            "move_count": self.move_count,
            "won": bool(won),
        }

        if won:
            self.winner = str(self.players[idx].id)
            self.rolls_in_turn = 0
            return

        # zmiana tury: jeśli nie ma bonusu za 6 (i nie przekroczono limitu)
        if not (roll_value == 6 and self.rolls_in_turn < 3):
            self.turn = (idx + 1) % len(self.players)
            self.rolls_in_turn = 0

    # Zapisywanie stanu gry

    def to_template_payload(self) -> Dict[str, Any]:
        # dla hotseat/ai
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
            "magic_tiles": self.magic.active_list(),
            "last_move": self.last_move
        }

    @staticmethod
    def new_hotseat(n_players: int) -> "Game":
        g = Game(mode="hotseat")
        n = max(2, min(4, int(n_players)))
        colors = ["p-red", "p-blue", "p-green", "p-purple"]
        g.players = [
            Player(pid=i, name=f"Gracz {i+1}", pos=0, color=colors[i], is_bot=False, card=None)
            for i in range(n)
        ]
        return g

    @staticmethod
    def new_ai() -> "Game":
        g = Game(mode="ai")
        g.players = [
            Player(pid=0, name="Ty", pos=0, color="p-red", is_bot=False, card=None),
            Player(pid=1, name="Komputer", pos=0, color="p-blue", is_bot=True, card=None),
        ]
        return g

    @staticmethod
    def from_room_dict(room: Dict[str, Any]) -> "Game":
        g = Game(mode="mp")
        g.players = [Player.from_dict(p) for p in room.get("players", [])]
        g.turn = int(room.get("turn", 0))
        g.last_roll = room.get("last_roll")
        g.last_player = int(room.get("last_player", 0))
        g.message = room.get("message", "")
        g.history = (room.get("history", []) or [])[-8:]
        g.move_count = int(room.get("move_count", 0))
        g.pending = room.get("pending")
        g.last_move = room.get("last_move")
        g.magic = MagicTiles.from_any(room.get("magic_tiles"))
        g.max_players = int(room.get("max_players", 2))
        g.winner = room.get("winner")
        g.rolls_in_turn = int(room.get("rolls_in_turn", 0))
        return g

    def to_room_dict(self, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        room = dict(base) if base else {}
        room["players"] = [p.to_dict() for p in self.players]
        room["turn"] = int(self.turn)
        room["last_roll"] = self.last_roll
        room["last_player"] = int(self.last_player)
        room["message"] = self.message
        room["history"] = self.history[-8:]
        room["move_count"] = int(self.move_count)
        room["pending"] = self.pending
        room["last_move"] = self.last_move
        room["magic_tiles"] = self.magic.to_json_dict()
        room["max_players"] = int(self.max_players)
        room["winner"] = self.winner
        room["rolls_in_turn"] = int(self.rolls_in_turn)
        return room


#MULTIPLAYER ZAPIS
ROOMS_DIR = Path("data/rooms")
ROOMS_DIR.mkdir(parents=True, exist_ok=True)

def room_path(code: str) -> Path:
    return ROOMS_DIR / f"{code}.json"

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

def bump_version(room: Dict[str, Any]) -> None:
    room["version"] = int(room.get("version", 0)) + 1

def save_room_bumped(code: str, room: Dict[str, Any]) -> None:
    bump_version(room)
    save_room(code, room)


GAMES: Dict[str, Game] = {}

def cleanup_games(ttl_seconds: int = 60 * 60 * 6) -> None:
    now = time.time()
    dead = [sid for sid, g in GAMES.items() if getattr(g, "updated_at", now) < now - ttl_seconds]
    for sid in dead:
        del GAMES[sid]

def new_sid() -> str:
    sid = gen_room_code(8)
    while sid in GAMES:
        sid = gen_room_code(8)
    return sid

def current_game() -> Optional[Game]:
    sid = request.cookies.get("sid")
    if not sid:
        return None
    return GAMES.get(sid)

def set_sid_cookie(resp, sid: str):
    resp.set_cookie("sid", sid, max_age=60 * 60 * 24 * 7)
    return resp


@app.route("/")
def index():
    cleanup_games()
    game = current_game()
    if not game or not game.players:
        return redirect("/new?mode=hotseat&players=2")

    won = game.anyone_won()
    n_players = len(game.players)
    mc = int(game.move_count)
    round_num = 1 if mc == 0 else ((mc - 1) // n_players) + 1

    payload = game.to_template_payload()
    payload.update({
        "won": won,
        "round": round_num,
        "snakes_ladders": SNAKE_LADDERS,
    })
    return render_template("index.html", **payload)

@app.route("/new")
def new_game():
    cleanup_games()
    mode = request.args.get("mode", "hotseat")

    if mode == "ai":
        game = Game.new_ai()
    else:
        n = int(request.args.get("players", 2))
        game = Game.new_hotseat(n)

    sid = new_sid()
    GAMES[sid] = game

    resp = make_response(redirect("/"))
    return set_sid_cookie(resp, sid)

@app.route("/roll")
def roll():
    game = current_game()
    if not game:
        return redirect("/new?mode=hotseat&players=2")

    game.roll()
    return redirect("/")

@app.route("/snake_decision", methods=["POST"])
def snake_decision():
    game = current_game()
    if not game:
        return redirect("/new?mode=hotseat&players=2")

    pend = game.pending
    if not pend or pend.get("type") != "snake_choice":
        return redirect("/")

    pid = pend.get("player_id")
    choice = request.form.get("choice", "stay")
    game.snake_decision(pid, choice)
    return redirect("/")

@app.route("/use_card", methods=["POST"])
def use_card():
    game = current_game()
    if not game:
        return redirect("/new?mode=hotseat&players=2")

    game.use_card()
    return redirect("/")

@app.route("/ai_move")
def ai_move():
    game = current_game()
    if not game:
        return redirect("/new?mode=ai")

    game.ai_move()
    return redirect("/")

@app.route("/set_colors", methods=["POST"])
def set_colors():
    game = current_game()
    if not game:
        return redirect("/new?mode=hotseat&players=2")

    palette = ["p-red", "p-blue", "p-green", "p-purple"]
    used = set()

    for i, pl in enumerate(game.players):
        if game.mode == "ai" and pl.is_bot:
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

    game.touch()
    return redirect("/")

@app.route("/howto")
def howto():
    game = current_game()
    mode = game.mode if game else "hotseat"
    return render_template("howto.html", mode=mode)

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

    game = Game(mode="mp")
    game.max_players = max_players
    game.players = [Player(pid="p1", name=name, pos=0, color="p-red", card=None, is_bot=False)]
    game.magic = MagicTiles(MAGIC_TILES_TEMPLATE.copy())

    room = {
        "code": code,
        "created": int(time.time()),
        "version": 0,
        "turn": 0,
        "last_roll": None,
        "last_player": 0,
        "message": "",
        "history": [],
        "move_count": 0,
        "max_players": max_players,
        "winner": None,
        "pending": None,
        "last_move": None,
        "rolls_in_turn": 0,
    }
    room = game.to_room_dict(room)
    save_room_bumped(code, room)

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

    room["message"] = f"✅ Dołączył(a): {name}"
    room.setdefault("history", []).append(room["message"])
    room["history"] = room["history"][-8:]

    save_room_bumped(code, room)

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
    can_roll = (not winner) and my_turn and (len(room.get("players", [])) >= 2) and (not room.get("pending"))

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
        resp = make_response(jsonify({"ok": False, "error": "no_room"}), 200)
    else:
        resp = make_response(jsonify(room), 200)

    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route("/mp/room/<code>/roll", methods=["POST"])
def mp_roll(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return redirect("/mp")

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return redirect(f"/mp/room/{code}")

    game = Game.from_room_dict(room)
    game.mp_roll(my_pid)

    # ważne: zapisujemy room tylko po akcji (tu jest akcja)
    room = game.to_room_dict(room)
    save_room_bumped(code, room)
    return redirect(f"/mp/room/{code}")

@app.route("/mp/room/<code>/snake_decision", methods=["POST"])
def mp_snake_decision(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return redirect("/mp")

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return redirect(f"/mp/room/{code}")

    game = Game.from_room_dict(room)

    pend = game.pending
    if not pend or pend.get("type") != "snake_choice":
        return redirect(f"/mp/room/{code}")
    if pend.get("player_id") != my_pid:
        return redirect(f"/mp/room/{code}")

    choice = request.form.get("choice", "stay")
    game.snake_decision(my_pid, choice)

    room = game.to_room_dict(room)
    save_room_bumped(code, room)
    return redirect(f"/mp/room/{code}")

@app.route("/mp/room/<code>/use_card", methods=["POST"])
def mp_use_card(code):
    code = code.upper()
    room = load_room(code)
    if not room:
        return redirect("/mp")

    if room.get("winner") or room.get("pending"):
        return redirect(f"/mp/room/{code}")

    my_pid = request.cookies.get(f"mp_{code}_pid")
    if not my_pid:
        return redirect(f"/mp/room/{code}")

    game = Game.from_room_dict(room)

    # tylko gracz na turze
    idx = next((i for i, p in enumerate(game.players) if p.id == my_pid), None)
    if idx is None or idx != int(game.turn):
        return redirect(f"/mp/room/{code}")

    # użyj karty (w tej wersji obsługujemy TELEPORT_PLUS3)
    game.use_card()

    room = game.to_room_dict(room)
    save_room_bumped(code, room)
    return redirect(f"/mp/room/{code}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=11901)

