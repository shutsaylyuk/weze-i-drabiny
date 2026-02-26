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
        card: Optional[str] = None,  # UWAGA: w tej wersji to tylko "display"
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
    def __init__(self, mode: str = "hotseat", variant: str = "classic"):
        self.mode: str = mode
        self.variant: str = variant

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

        # KARTY: jedna karta na "gracza/drużynę"
        # - hotseat: key = str(player.id)
        # - ai classic: key = "h" / "a"
        # - ai double: key = "h" / "a"
        self.team_cards: Dict[str, Optional[str]] = {}

        # Multiplayer
        self.max_players: int = 2
        self.winner: Optional[str] = None
        self.rolls_in_turn: int = 0

        self.updated_at: float = time.time()

    def touch(self) -> None:
        self.updated_at = time.time()

    def push_history(self, text: str) -> None:
        self.history.append(text)
        self.history = self.history[-8:]

    def current_index(self) -> int:
        return int(self.turn) % max(1, len(self.players))

    # ===== Helpers: team key / card =====
    def _team_key_for_player(self, p: Player) -> str:
        # AI modes: human vs ai
        if self.mode == "ai":
            pid = str(p.id)
            if pid.startswith("h") or pid in ("0",):  # "Ty" classic ma pid=0
                return "h"
            return "a"
        # hotseat / mp: każdy gracz osobno
        return str(p.id)

    def _get_team_card(self, p: Player) -> Optional[str]:
        key = self._team_key_for_player(p)
        return self.team_cards.get(key)

    def _set_team_card(self, p: Player, value: Optional[str]) -> None:
        key = self._team_key_for_player(p)
        self.team_cards[key] = value

    def _sync_cards_for_display(self) -> None:
        # żeby UI dalej działał na players[i].card
        for pl in self.players:
            pl.card = self._get_team_card(pl)

    def _team_card_exists(self, team_key: str) -> bool:
        return bool(self.team_cards.get(team_key))

    # ===== AI DOUBLE WIN =====
    def _team_positions(self, prefix: str) -> List[int]:
        return [int(p.pos) for p in self.players if str(p.id).startswith(prefix)]

    def team_won(self, prefix: str) -> bool:
        pos = self._team_positions(prefix)
        return len(pos) >= 2 and all(x == BOARD_END for x in pos[:2])

    def winner_text(self) -> Optional[str]:
        if self.mode == "ai" and self.variant == "double":
            if self.team_won("h"):
                return "Ty"
            if self.team_won("a"):
                return "Komputer"
        return None

    def anyone_won(self) -> bool:
        if self.mode == "ai" and self.variant == "double":
            return self.team_won("h") or self.team_won("a")
        return any(int(p.pos) == BOARD_END for p in self.players)

    # ===== Rules helpers =====
    def _mark_magic_tile_used_if_leaving(self, marker: Any, start_pos: int) -> None:
        # marker = identyfikator "zajęcia" pola (teraz: team_key)
        if start_pos in self.magic.tiles and self.magic.tiles.get(start_pos) == marker:
            self.magic.tiles[start_pos] = "USED"

    def _can_team_take_card_on_tile(self, team_key: str, pos: int) -> bool:
        if pos not in self.magic.tiles:
            return False
        state = self.magic.tiles.get(pos)
        if state == "USED":
            return False
        # jeśli pole jest "zarezerwowane" dla innej drużyny, nie da
        if state is not None and state != team_key:
            return False
        # jeśli drużyna już ma kartę, nie może dostać nowej
        if self.team_cards.get(team_key) is not None:
            return False
        return True

    def _give_card_if_magic_tile(self, p: Player) -> Optional[str]:
        pos = int(p.pos)
        team_key = self._team_key_for_player(p)

        if not self._can_team_take_card_on_tile(team_key, pos):
            return None

        card = random.choice(CARD_POOL)
        self.team_cards[team_key] = card
        self.magic.tiles[pos] = team_key  # "rezerwacja" żółtego pola dla tej drużyny
        return f" ✨ Zdobywasz kartę: {card.replace('_', ' ')}"

    def _try_start_snake_pending(self, p: Player, idx: int, resume: Optional[Dict[str, Any]] = None) -> bool:
        pos = int(p.pos)
        if not is_snake(pos):
            return False

        team_key = self._team_key_for_player(p)
        team_card = self.team_cards.get(team_key)

        # tylko człowiek dostaje okienko decyzji (w AI double też)
        if team_card == "ANTY_WAZ" and (not p.is_bot):
            pend = {
                "type": "snake_choice",
                "player_id": p.id if p.id is not None else idx,
                "pawn_idx": idx,
                "team_key": team_key,
                "from": pos,
                "to": SNAKE_LADDERS[pos],
            }
            if resume:
                pend["resume"] = resume
            self.pending = pend
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
        roll_value = random.randint(1, 6)
        return self._move_with_roll(idx, roll_value)

    def _move_with_roll(self, idx: int, roll_value: int) -> Tuple[str, int, bool, int, int, int]:
        p = self.players[idx]
        roll_value = int(roll_value)

        start = int(p.pos)
        tentative = start + roll_value

        if tentative > BOARD_END:
            msg = f"{p.name}: wyrzucono {roll_value}. Musisz trafić dokładnie!"
            return msg, roll_value, False, start, start, start

        # schodząc z żółtego pola — oznaczamy USED (teraz marker=team_key)
        team_key = self._team_key_for_player(p)
        self._mark_magic_tile_used_if_leaving(team_key, start)

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

        won = (int(p.pos) == BOARD_END) if not (self.mode == "ai" and self.variant == "double") else False
        return msg, roll_value, bool(won), start, land_pos, int(p.pos)

    # ===== AI double evaluation helpers =====
    def _apply_ladder_virtual(self, n: int) -> int:
        if is_ladder(n):
            return int(SNAKE_LADDERS[n])
        return int(n)

    def _is_active_magic_for(self, p: Player, n: int) -> bool:
        if n > BOARD_END:
            return False
        team_key = self._team_key_for_player(p)
        if n not in self.magic.tiles:
            return False
        if self.magic.tiles.get(n) == "USED":
            return False
        # jeśli drużyna ma już kartę -> nie opłaca się "polować"
        if self.team_cards.get(team_key) is not None:
            return False
        state = self.magic.tiles.get(n)
        return (state is None) or (state == team_key)

    def _score_runner_ladder(self, p: Player, die: int) -> int:
        start = int(p.pos)
        land = start + int(die)
        if land > BOARD_END:
            return -10_000
        after = self._apply_ladder_virtual(land)

        score = after * 10
        if is_ladder(land):
            score += 5000
        if is_snake(land):
            score -= 3000
        score += max(0, after - 90) * 30
        return score

    def _score_card_collector(self, p: Player, die: int) -> int:
        start = int(p.pos)
        land = start + int(die)
        if land > BOARD_END:
            return -10_000

        after = self._apply_ladder_virtual(land)
        score = after * 5

        if self._is_active_magic_for(p, land):
            score += 6000
        if is_snake(land):
            score -= 1500
        if is_ladder(land):
            score += 700
        return score

    # ===== Normal roll (hotseat / ai classic) + AI double dice pending/auto =====
    def roll(self) -> None:
        self.touch()

        if not self.players:
            self.message = "Brak graczy."
            return

        if self.anyone_won():
            self.message = "Gra zakończona."
            return

        if self.pending:
            self.message = "Najpierw podejmij decyzję (pending)."
            self.push_history(self.message)
            self.last_move = None
            return

        idx = self.current_index()

        # ===== AI DOUBLE: człowiek rzuca 2 kośćmi =====
        if self.mode == "ai" and self.variant == "double":
            if idx != 0:
                self.message = "Teraz ruch komputera…"
                self.push_history(self.message)
                return

            d1 = random.randint(1, 6)
            d2 = random.randint(1, 6)

            h1_done = int(self.players[0].pos) == BOARD_END
            h2_done = int(self.players[1].pos) == BOARD_END

            # jeśli dokładnie jeden pionek jest na mecie -> rzut tylko 1 kością (bez wyboru)
            if h1_done ^ h2_done:
                idx_move = 1 if h1_done else 0
                d = random.randint(1, 6)

                parts = [f"🎲 Wyrzucono: {d}. Drugi pionek jest na mecie — wykonujesz tylko 1 ruch."]

                msg, rv, _, _, _, _ = self._move_with_roll(idx_move, d)
                self.last_roll = int(rv)
                self.last_player = idx_move
                self.move_count += 1
                parts.append(msg)

                # snake pending (tylko człowiek)
                if self._try_start_snake_pending(self.players[idx_move], idx_move,
                                                 resume={"type": "after_auto_one", "next": None}):
                    parts[-1] += " 🃏 Masz ANTY WĄŻ — wybierz decyzję."
                    self.message = " | ".join(parts)
                    self.push_history(self.message)
                    self.last_move = None
                    return

                extra = self._apply_snake_if_no_pending(self.players[idx_move])
                if extra:
                    parts[-1] += extra
                extra2 = self._give_card_if_magic_tile(self.players[idx_move])
                if extra2:
                    parts[-1] += extra2

                if self.team_won("h"):
                    self.pending = None
                    self.message = " | ".join(parts) + " 🏁 Wygrana! Oba Twoje pionki są na mecie."
                    self.push_history(self.message)
                    return

                self.pending = None
                self.turn = 2
                self.message = " | ".join(parts)
                self.push_history(self.message)
                return

                extra = self._apply_snake_if_no_pending(self.players[idx_move])
                if extra:
                    parts[-1] += extra
                extra2 = self._give_card_if_magic_tile(self.players[idx_move])
                if extra2:
                    parts[-1] += extra2

                if self.team_won("h"):
                    self.pending = None
                    self.message = " | ".join(parts) + " 🏁 Wygrana! Oba Twoje pionki są na mecie."
                    self.push_history(self.message)
                    return

                self.pending = None
                self.turn = 2
                self.message = " | ".join(parts)
                self.push_history(self.message)
                return

            self.pending = {"type": "dice_choice", "dice": [d1, d2]}
            self.message = f"🎲 Wyrzucono: {d1} i {d2}. Wybierz przypisanie kości do pionków."
            self.push_history(self.message)
            self.last_move = None
            return

        # ===== AI classic: blokuj gdy bot =====
        if self.mode == "ai" and self.players[idx].is_bot:
            self.message = "Teraz ruch komputera…"
            self.push_history(self.message)
            return

        msg, roll_value, won, from_pos, land_pos, to_pos = self._raw_move(idx)

        self.last_roll = int(roll_value)
        self.last_player = idx
        self.move_count += 1

        if (not won) and self._try_start_snake_pending(self.players[idx], idx):
            msg += " 🃏 Masz ANTY WĄŻ - wybierz: zostać czy cofnąć się?"
            self.message = msg
            self.push_history(msg)
            self.last_move = None
            return

        if not won:
            extra = self._apply_snake_if_no_pending(self.players[idx])
            if extra:
                msg += extra
                to_pos = int(self.players[idx].pos)

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

    # ===== AI DOUBLE: apply dice choice for human (2 moves) =====
    def apply_dice_choice_human(self, swap: bool) -> None:
        self.touch()

        if not (self.mode == "ai" and self.variant == "double"):
            return

        pend = self.pending
        if not pend or pend.get("type") != "dice_choice":
            return

        d1, d2 = pend.get("dice", [None, None])
        if d1 is None or d2 is None:
            self.pending = None
            return

        i1, i2 = 0, 1
        r1, r2 = (d2, d1) if swap else (d1, d2)

        parts: List[str] = []

        msg1, rv1, _, _, _, _ = self._move_with_roll(i1, r1)
        self.last_roll = int(rv1)
        self.last_player = i1
        self.move_count += 1
        parts.append(msg1)

        if self._try_start_snake_pending(self.players[i1], i1, resume={"type": "after_dice_choice", "next": [i2, r2]}):
            parts[-1] += " 🃏 Masz ANTY WĄŻ — wybierz decyzję."
            self.message = " | ".join(parts)
            self.push_history(self.message)
            self.last_move = None
            return

        extra = self._apply_snake_if_no_pending(self.players[i1])
        if extra:
            parts[-1] += extra
        extra2 = self._give_card_if_magic_tile(self.players[i1])
        if extra2:
            parts[-1] += extra2

        if self.team_won("h"):
            self.pending = None
            self.message = " | ".join(parts) + " 🏁 Wygrana! Oba Twoje pionki są na mecie."
            self.push_history(self.message)
            return

        msg2, rv2, _, _, _, _ = self._move_with_roll(i2, r2)
        self.last_roll = int(rv2)
        self.last_player = i2
        self.move_count += 1
        parts.append(msg2)

        if self._try_start_snake_pending(self.players[i2], i2, resume={"type": "after_dice_choice", "next": None}):
            parts[-1] += " 🃏 Masz ANTY WĄŻ — wybierz decyzję."
            self.message = " | ".join(parts)
            self.push_history(self.message)
            self.last_move = None
            return

        extra = self._apply_snake_if_no_pending(self.players[i2])
        if extra:
            parts[-1] += extra
        extra2 = self._give_card_if_magic_tile(self.players[i2])
        if extra2:
            parts[-1] += extra2

        if self.team_won("h"):
            self.pending = None
            self.message = " | ".join(parts) + " 🏁 Wygrana! Oba Twoje pionki są na mecie."
            self.push_history(self.message)
            return

        self.pending = None
        self.turn = 2
        self.message = " | ".join(parts)
        self.push_history(self.message)

    # ===== snake decision (classic + mp + double resume) =====
    def snake_decision(self, player_id: Any, choice: str) -> None:
        self.touch()

        pend = self.pending
        if not pend or pend.get("type") != "snake_choice":
            return

        idx = next((i for i, p in enumerate(self.players) if p.id == player_id), None)
        if idx is None:
            self.pending = None
            return

        pl = self.players[idx]
        from_pos = int(pl.pos)
        land_pos = int(pend["from"])

        team_key = pend.get("team_key") or self._team_key_for_player(pl)
        resume = pend.get("resume")

        if choice == "back":
            # NIE zużywamy karty
            pl.pos = int(pend["to"])
            msg = f"{pl.name}: wybrał(a) cofnięcie. 🐍 {pend['from']} -> {pend['to']}"
        else:
            # Zużywamy ANTY_WAZ drużyny
            if self.team_cards.get(team_key) == "ANTY_WAZ":
                self.team_cards[team_key] = None
            msg = f"{pl.name}: użył(a) ANTY WĄŻ i zostaje na {pend['from']} ✅"

        self.pending = None

        self.message = msg
        self.push_history(msg)

        self.move_count += 1
        self.last_move = {
            "player": idx,
            "from": from_pos,
            "land": land_pos,
            "to": int(pl.pos),
            "move_count": self.move_count,
            "won": False,
        }

        # ===== AI DOUBLE resume: execute second pawn move after snake decision =====
        if self.mode == "ai" and self.variant == "double" and isinstance(resume, dict) and resume.get("type") == "after_dice_choice":
            nxt = resume.get("next")
            if nxt:
                i2, r2 = int(nxt[0]), int(nxt[1])
                parts = [self.message]

                msg2, rv2, _, _, _, _ = self._move_with_roll(i2, r2)
                self.last_roll = int(rv2)
                self.last_player = i2
                self.move_count += 1
                parts.append(msg2)

                if self._try_start_snake_pending(self.players[i2], i2, resume={"type": "after_dice_choice", "next": None}):
                    parts[-1] += " 🃏 Masz ANTY WĄŻ — wybierz decyzję."
                    self.message = " | ".join(parts)
                    self.push_history(self.message)
                    self.last_move = None
                    return

                extra = self._apply_snake_if_no_pending(self.players[i2])
                if extra:
                    parts[-1] += extra
                extra2 = self._give_card_if_magic_tile(self.players[i2])
                if extra2:
                    parts[-1] += extra2

                if self.team_won("h"):
                    self.message = " | ".join(parts) + " 🏁 Wygrana! Oba Twoje pionki są na mecie."
                    self.push_history(self.message)
                    return

                self.turn = 2
                self.message = " | ".join(parts)
                self.push_history(self.message)
                return

            self.turn = 2
            return

        if self.last_roll != 6:
            self.turn = (idx + 1) % len(self.players)

    # ===== use_card (TELEPORT only) =====
    def use_card(self, pawn_idx: Optional[int] = None) -> None:
        self.touch()

        if not self.players or self.anyone_won():
            return
        if self.pending:
            self.message = "Najpierw rozwiąż decyzję (pending)."
            self.push_history(self.message)
            return

        idx_turn = self.current_index()
        turn_player = self.players[idx_turn]
        team_key = self._team_key_for_player(turn_player)
        card = self.team_cards.get(team_key)
        if not card:
            return

        # W AI double: człowiek może użyć na wybranym swoim pionku (0 lub 1)
        target_idx = idx_turn
        if self.mode == "ai" and self.variant == "double" and team_key == "h":
            if pawn_idx is not None:
                try:
                    pi = int(pawn_idx)
                    if pi in (0, 1):
                        target_idx = pi
                except Exception:
                    pass

        pl = self.players[target_idx]

        # nie pozwól użyć na pionku już na mecie
        if int(pl.pos) == BOARD_END:
            self.message = "Ten pionek jest już na mecie."
            self.push_history(self.message)
            return

        if card == "TELEPORT_PLUS3":
            start = int(pl.pos)
            tentative = start + 3

            if tentative > BOARD_END:
                msg = f"{pl.name}: TELEPORT +3, ale musisz trafić dokładnie!"
                self.message = msg
                self.push_history(msg)
                return

            # schodzimy z żółtego pola (marker=team_key)
            self._mark_magic_tile_used_if_leaving(team_key, start)

            # zużyj kartę drużyny
            self.team_cards[team_key] = None

            pl.pos = tentative
            msg = f"{pl.name}: używa TELEPORT +3: {start} -> {tentative}"
            land_pos = tentative

            if is_ladder(tentative):
                after = SNAKE_LADDERS[tentative]
                pl.pos = after
                msg += f" 🪜 Drabina! {tentative} -> {after}"
            elif is_snake(tentative):
                # człowiek: może mieć ANTY_WAZ tylko jeśli drużyna ma ANTY_WAZ (ale teraz zużyliśmy teleport)
                extra = self._apply_snake_if_no_pending(pl)
                if extra:
                    msg += extra

            extra2 = self._give_card_if_magic_tile(pl)
            if extra2:
                msg += extra2

            self.move_count += 1
            self.last_move = {
                "player": target_idx,
                "from": start,
                "land": land_pos,
                "to": int(pl.pos),
                "move_count": self.move_count,
                "won": False,
            }

            self.message = msg
            self.push_history(msg)

    # ===== AI classic =====
    def ai_move(self) -> None:
        self.touch()

        if not self.players or self.anyone_won():
            return

        idx = self.current_index()
        if not (self.mode == "ai" and self.players[idx].is_bot):
            return

        bot = self.players[idx]
        team_key = self._team_key_for_player(bot)

        # BOT: TELEPORT asap (karta drużyny)
        if self.team_cards.get(team_key) == "TELEPORT_PLUS3" and not self.pending:
            start = int(bot.pos)
            tentative = start + 3
            if tentative <= BOARD_END:
                self._mark_magic_tile_used_if_leaving(team_key, start)
                self.team_cards[team_key] = None
                bot.pos = tentative

                msg = f"{bot.name}: używa TELEPORT +3: {start} -> {tentative}"

                if is_ladder(tentative):
                    after = SNAKE_LADDERS[tentative]
                    bot.pos = after
                    msg += f" 🪜 Drabina! {tentative} -> {after}"
                elif is_snake(tentative):
                    # BOT: jeśli ma ANTY_WAZ jako karta drużyny, to zostaje
                    if self.team_cards.get(team_key) == "ANTY_WAZ":
                        self.team_cards[team_key] = None
                        msg += f" 🃏 BOT używa ANTY WĄŻ i zostaje na {tentative} ✅"
                    else:
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
                    "land": tentative,
                    "to": int(bot.pos),
                    "move_count": self.move_count,
                    "won": bool(int(bot.pos) == BOARD_END),
                }

                self.message = msg
                self.push_history(msg)

                if int(bot.pos) == BOARD_END:
                    return

        msg, roll_value, won, from_pos, land_pos, to_pos = self._raw_move(idx)

        self.last_roll = int(roll_value)
        self.last_player = idx
        self.move_count += 1

        pos_after = int(self.players[idx].pos)
        if (not won) and is_snake(pos_after):
            if self.team_cards.get(team_key) == "ANTY_WAZ":
                self.team_cards[team_key] = None
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

    # ===== AI DOUBLE: one click AI turn (2 dice + strategies) =====
    def ai_pair_move(self) -> None:
        self.touch()

        if not (self.mode == "ai" and self.variant == "double"):
            return
        if self.pending or self.anyone_won():
            return
        if self.current_index() != 2:
            return

        ladder_idx = 2
        card_idx = 3

        ladder_p = self.players[ladder_idx]
        card_p = self.players[card_idx]
        team_key = "a"

        parts: List[str] = []

        # Jeśli AI ma TELEPORT jako karta drużyny -> spróbuj użyć sensownie
        if self.team_cards.get(team_key) == "TELEPORT_PLUS3":
            used = False

            # card-pionek preferuje magic / drabiny
            if self._is_active_magic_for(card_p, int(card_p.pos) + 3) or is_ladder(int(card_p.pos) + 3) or (int(card_p.pos) + 3 >= BOARD_END - 3):
                parts.extend(self._ai_use_teleport_double(card_idx, prefer_magic=True))
                used = True

            # jeśli nie zużył, ladder-pionek zużyje gdy pomaga
            if (not used):
                t = int(ladder_p.pos) + 3
                if t <= BOARD_END and (is_ladder(t) or t >= BOARD_END - 3):
                    parts.extend(self._ai_use_teleport_double(ladder_idx, prefer_magic=False))
                    used = True

        if self.anyone_won():
            self.message = " | ".join(parts)
            self.push_history(self.message)
            return

        d1 = random.randint(1, 6)
        d2 = random.randint(1, 6)
        parts.append(f"🤖 AI rzuca: {d1} i {d2}")

        scoreA = self._score_runner_ladder(ladder_p, d1) + self._score_card_collector(card_p, d2)
        scoreB = self._score_runner_ladder(ladder_p, d2) + self._score_card_collector(card_p, d1)

        if scoreB > scoreA:
            ladder_roll, card_roll = d2, d1
            parts.append("🤖 AI wybiera przypisanie: ladder←druga kość, cards←pierwsza kość")
        else:
            ladder_roll, card_roll = d1, d2
            parts.append("🤖 AI wybiera przypisanie: ladder←pierwsza kość, cards←druga kość")

        parts.extend(self._ai_move_one_double(ladder_idx, ladder_roll))
        if self.team_won("a"):
            self.message = " | ".join(parts) + " 🏁 Wygrana AI! Oba pionki na mecie."
            self.push_history(self.message)
            return

        parts.extend(self._ai_move_one_double(card_idx, card_roll))
        if self.team_won("a"):
            self.message = " | ".join(parts) + " 🏁 Wygrana AI! Oba pionki na mecie."
            self.push_history(self.message)
            return

        self.turn = 0
        self.message = " | ".join(parts)
        self.push_history(self.message)

    def _ai_use_teleport_double(self, idx: int, prefer_magic: bool) -> List[str]:
        parts: List[str] = []
        bot = self.players[idx]
        team_key = self._team_key_for_player(bot)

        if self.team_cards.get(team_key) != "TELEPORT_PLUS3":
            return parts

        start = int(bot.pos)
        t = start + 3
        if t > BOARD_END:
            return parts

        if prefer_magic:
            good = self._is_active_magic_for(bot, t) or is_ladder(t) or (t >= BOARD_END - 3)
            if not good:
                return parts

        self._mark_magic_tile_used_if_leaving(team_key, start)

        # zużyj karta drużyny
        self.team_cards[team_key] = None

        bot.pos = t
        msg = f"{bot.name}: używa TELEPORT +3: {start} -> {t}"

        if is_ladder(t):
            after = SNAKE_LADDERS[t]
            bot.pos = after
            msg += f" 🪜 Drabina! {t} -> {after}"
        elif is_snake(t):
            # BOT: jeśli ma ANTY_WAZ jako karta drużyny -> zostań (ale tu teleport już zużył, więc raczej nie)
            extra = self._apply_snake_if_no_pending(bot)
            if extra:
                msg += extra

        extra2 = self._give_card_if_magic_tile(bot)
        if extra2:
            msg += extra2

        parts.append(msg)
        return parts

    def _ai_move_one_double(self, idx: int, roll: int) -> List[str]:
        parts: List[str] = []
        msg, rv, _, _, _, _ = self._move_with_roll(idx, roll)
        self.last_roll = int(rv)
        self.last_player = idx
        self.move_count += 1
        parts.append(msg)

        bot = self.players[idx]
        team_key = self._team_key_for_player(bot)
        pos_after = int(bot.pos)

        if is_snake(pos_after) and self.team_cards.get(team_key) == "ANTY_WAZ":
            self.team_cards[team_key] = None
            parts[-1] += f" 🃏 BOT używa ANTY WĄŻ i zostaje na {pos_after} ✅"
        else:
            extra = self._apply_snake_if_no_pending(bot)
            if extra:
                parts[-1] += extra

        extra2 = self._give_card_if_magic_tile(bot)
        if extra2:
            parts[-1] += extra2

        return parts

    # ===== Multiplayer (bez zmian logiki kart tutaj) =====
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

        if (not won) and self._try_start_snake_pending(self.players[idx], idx):
            msg += " 🃏 Masz ANTY WĄŻ — wybierz: zostać czy cofnąć się?"
            self.message = msg
            self.push_history(msg)
            self.last_move = None
            return

        if not won:
            extra = self._apply_snake_if_no_pending(self.players[idx])
            if extra:
                msg += extra

        if not won:
            extra2 = self._give_card_if_magic_tile(self.players[idx])
            if extra2:
                msg += extra2

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

        if not (roll_value == 6 and self.rolls_in_turn < 3):
            self.turn = (idx + 1) % len(self.players)
            self.rolls_in_turn = 0

    # ===== Payload =====
    def to_template_payload(self) -> Dict[str, Any]:
        self._sync_cards_for_display()
        return {
            "players": [p.to_dict() for p in self.players],
            "turn": self.turn,
            "last_roll": self.last_roll,
            "last_player": self.last_player,
            "message": self.message,
            "history": self.history,
            "move_count": self.move_count,
            "mode": self.mode,
            "variant": self.variant,
            "pending": self.pending,
            "magic_tiles": self.magic.active_list(),
            "last_move": self.last_move,
            "winner_text": self.winner_text(),
        }

    @staticmethod
    def new_hotseat(n_players: int) -> "Game":
        g = Game(mode="hotseat", variant="classic")
        n = max(2, min(4, int(n_players)))
        colors = ["p-red", "p-blue", "p-green", "p-purple"]
        g.players = [
            Player(pid=i, name=f"Gracz {i+1}", pos=0, color=colors[i], is_bot=False, card=None)
            for i in range(n)
        ]
        # karta per gracz
        for pl in g.players:
            g.team_cards[str(pl.id)] = None
        return g

    @staticmethod
    def new_ai() -> "Game":
        g = Game(mode="ai", variant="classic")
        g.players = [
            Player(pid=0, name="Ty", pos=0, color="p-red", is_bot=False, card=None),
            Player(pid=1, name="Komputer", pos=0, color="p-blue", is_bot=True, card=None),
        ]
        g.team_cards["h"] = None
        g.team_cards["a"] = None
        return g

    @staticmethod
    def new_ai_double() -> "Game":
        g = Game(mode="ai", variant="double")
        g.players = [
            Player(pid="h1", name="Ty (1)", pos=0, color="p-red", is_bot=False, card=None),
            Player(pid="h2", name="Ty (2)", pos=0, color="p-red", is_bot=False, card=None),
            Player(pid="a_lad", name="AI (ladder)", pos=0, color="p-blue", is_bot=True, card=None),
            Player(pid="a_crd", name="AI (cards)", pos=0, color="p-blue", is_bot=True, card=None),
        ]
        g.turn = 0
        g.team_cards["h"] = None
        g.team_cards["a"] = None
        return g

    @staticmethod
    def from_room_dict(room: Dict[str, Any]) -> "Game":
        g = Game(mode="mp", variant="classic")
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

        # mp: jeśli chcesz też 1 karta na gracza w mp, trzeba trzymać to w pliku
        # (na razie trzymamy "jak było": per pionek display)
        for pl in g.players:
            g.team_cards[str(pl.id)] = pl.card

        return g

    def to_room_dict(self, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        room = dict(base) if base else {}
        self._sync_cards_for_display()
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


# ===== MULTIPLAYER SAVE =====
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
    winner_text = game.winner_text()

    n_players = len(game.players)
    mc = int(game.move_count)
    round_num = 1 if mc == 0 else ((mc - 1) // max(1, n_players)) + 1

    payload = game.to_template_payload()
    payload.update({
        "won": won,
        "winner_text": winner_text,
        "round": round_num,
        "snakes_ladders": SNAKE_LADDERS,
    })
    return render_template("index.html", **payload)


@app.route("/new")
def new_game():
    cleanup_games()
    mode = request.args.get("mode", "hotseat")

    if mode == "ai":
        variant = request.args.get("variant", "classic")
        if variant == "double":
            game = Game.new_ai_double()
        else:
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


@app.route("/dice_choice", methods=["POST"])
def dice_choice():
    game = current_game()
    if not game:
        return redirect("/new?mode=ai&variant=double")

    swap = (request.form.get("swap") == "1")
    game.apply_dice_choice_human(swap)
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

    pawn_idx = request.form.get("pawn_idx")
    game.use_card(pawn_idx=pawn_idx)
    return redirect("/")


@app.route("/ai_move")
def ai_move():
    game = current_game()
    if not game:
        return redirect("/new?mode=ai")

    if game.mode == "ai" and getattr(game, "variant", "classic") == "double":
        game.ai_pair_move()
    else:
        game.ai_move()

    return redirect("/")


@app.route("/howto")
def howto():
    game = current_game()
    mode = game.mode if game else "hotseat"
    return render_template("howto.html", mode=mode)


@app.get("/ai")
def ai_menu():
    return render_template("ai_menu.html", mode="ai")


# ===== multiplayer endpoints (bez zmian) =====
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

    game = Game(mode="mp", variant="classic")
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

    idx = next((i for i, p in enumerate(game.players) if p.id == my_pid), None)
    if idx is None or idx != int(game.turn):
        return redirect(f"/mp/room/{code}")

    # mp: zostawiamy "jak było" (per pionek display)
    game.use_card()

    room = game.to_room_dict(room)
    save_room_bumped(code, room)
    return redirect(f"/mp/room/{code}")


@app.route("/set_colors", methods=["POST"])
def set_colors():
    game = current_game()
    if not game:
        return redirect("/new?mode=hotseat&players=2")

    palette = ["p-red", "p-blue", "p-green", "p-purple"]

    if game.mode == "ai" and getattr(game, "variant", "classic") == "double":
        chosen = request.form.get("color_0", "p-red")
        if chosen not in palette:
            chosen = "p-red"

        game.players[0].color = chosen
        game.players[1].color = chosen

        ai_color = next((c for c in palette if c != chosen), "p-blue")
        game.players[2].color = ai_color
        game.players[3].color = ai_color

        game.touch()
        return redirect("/")

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=12363)