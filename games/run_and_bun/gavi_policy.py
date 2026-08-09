"""Executable, cartridge-tested policy for Camper Gavi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class GaviPolicy:
    """Choose only actions from a fresh Gavi battle certificate."""

    fresh_entry_species: int | None = None
    bibarel_razor_used: bool = False
    eelektrik_bushtank_used: bool = False
    eelektrik_nuzzled: bool = False

    @staticmethod
    def _find(actions: list[dict[str, Any]], kind: str, **match: int) -> dict[str, Any] | None:
        return next(
            (action for action in actions if action.get("kind") == kind and all(action.get(k) == v for k, v in match.items())),
            None,
        )

    def choose(self, certificate: dict[str, Any]) -> dict[str, Any]:
        player = certificate["state"]["player"]
        opponent = certificate["state"]["opponent"]
        actions = certificate["legal_actions"]
        ps, os = player["species"], opponent["species"]
        move = lambda move_id: self._find(actions, "move", move_id=move_id)
        switch = lambda species: self._find(actions, "switch", species=species)
        ready_switch = lambda species: next(
            (action for action in actions if action.get("kind") == "switch" and action.get("species") == species and not int(action.get("status", 0)) & 0x27),
            None,
        )

        if certificate["boundary"]["party_switch_required"]:
            action = actions[0] if len(actions) == 1 else (next((candidate for candidate in actions if candidate.get("kind") == "switch"), None) if opponent["hp"] <= 0 else None)
            action = action or ({77: switch(231) or switch(777) or switch(878) or switch(111), 603: switch(878) or switch(453), 192: switch(878) or switch(543) or switch(397) or switch(453), 269: switch(111) or switch(397)}.get(os)
                      or switch(111) or switch(878) or switch(777) or switch(453) or switch(397) or switch(388) or switch(231) or switch(543))
        elif os == 400:  # Bibarel
            action = move(209) if ps == 777 else move(75) if ps == 388 and not self.bibarel_razor_used else switch(777)
        elif os == 77:  # Ponyta
            if ps == 231:
                action = move(420) if opponent["hp"] <= 3 else (switch(777) or switch(397) or switch(453) or move(523)) if player["hp"] <= 19 else move(523)
            elif ps == 777:
                action = move(252) if self.fresh_entry_species == 777 else switch(231) or switch(111) or switch(878) or move(209)
            elif ps == 878:
                action = move(523)
            elif ps == 111:
                action = move(479)
            elif ps == 453:
                action = move(341) if opponent["hp"] <= 14 else switch(388) or switch(397)
            elif ps == 397:
                action = move(332)
            else:
                action = switch(111) or switch(878) or switch(231) or switch(777)
        elif os == 603:  # Eelektrik / Levitate
            if ps == 453:
                action = (move(252) if self.fresh_entry_species == 453 else None)
                action = action or switch(878) or (switch(388) if not self.eelektrik_bushtank_used else None) or move(124)
            elif ps == 388:
                action = move(75) if not self.eelektrik_bushtank_used else switch(878) or switch(453) or switch(777) or move(75)
            elif ps == 878:
                action = move(249)
            elif ps == 777:
                action = move(609) if not self.eelektrik_nuzzled else (switch(878) or switch(453) or move(209)) if player["hp"] <= 10 and opponent["hp"] > 10 else move(209)
            elif ps == 397:
                action = move(98) if opponent["hp"] <= 7 else switch(878) or switch(453) or switch(388) or switch(777) or move(98)
            elif ps == 231:
                action = switch(878) or switch(453) or switch(388) or switch(777) or move(420)
            else:
                action = switch(878) or switch(453) or switch(388) or switch(777)
        elif os == 192:  # Sunflora
            action = move(249) if ps == 878 else (ready_switch(397) or move(342)) if ps == 543 and player.get("status", 0) & 0x27 else move(342) if ps == 543 else ready_switch(878) or ready_switch(543) or (move(124) if ps == 453 else move(332) if ps == 397 else move(420) if ps == 231 else (move(252) if self.fresh_entry_species == 777 else move(232)) if ps == 777 else switch(453) or switch(397) or switch(111))
        elif os == 269:  # Dustox
            action = (
                move(332) if ps == 397
                else ((move(420) if opponent["hp"] <= 7 else move(523)) or switch(397) or switch(388) or switch(111) or switch(543)) if ps == 231
                else move(44) if ps == 388
                else move(124) if ps == 453
                else move(479) if ps == 111
                else move(209) if ps == 777
                else move(523) if ps == 878
                else (switch(111) or switch(397) or switch(388) or switch(231) or switch(777) or switch(453) or move(205)) if ps == 543
                else switch(111) or switch(397) or switch(388) or switch(231) or switch(777) or switch(453) or switch(878) or switch(543)
            )
        else:
            raise RuntimeError(f"unmodeled Gavi opponent species {os}")
        if action is None or action not in actions:
            raise RuntimeError(f"Gavi policy action unavailable: player={ps} opponent={os} legal={actions}")
        return action

    def record_verified(self, certificate: dict[str, Any], action: dict[str, Any]) -> None:
        ps = certificate["state"]["player"]["species"]
        os = certificate["state"]["opponent"]["species"]
        self.fresh_entry_species = action.get("species") if action["kind"] == "switch" else None
        if os == 400 and ps == 388 and action.get("move_id") == 75:
            self.bibarel_razor_used = True
        if os == 603 and ps == 388 and action.get("move_id") == 75:
            self.eelektrik_bushtank_used = True
        if os == 603 and ps == 777 and action.get("move_id") == 609:
            self.eelektrik_nuzzled = True
