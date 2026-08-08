# Goal-run regression ledger

## 2026-08-08 trainer battle switch

- Pre-state: Triathlete Mikey vs Chimchar Lv12 (32/32) and Krabby Lv9 (27/27); RAM command prompt; tactical report selected Pidgey because modeled Aqua Jet was a guaranteed Chimchar KO.
- First transaction attempt: `switch_pokemon` rejected `battle_move_menu_not_ready` / command cursor remained 0 while stale battle-text mode was active.
- Recovery: reconnect boundary; the same switch to Pidgey completed.
- Observed result: Pidgey became active, then the queued turn resolved with Pidgey Gust and foe Krabby Stomp; Pidgey ended at 12/33 and Krabby at 18/27. This was not the predicted switch-only line.
- Reusable cause: voluntary switching must cross the focus-free menu boundary before moving the battle action cursor.
- Tooling change: `games/run_and_bun/state.py::RunBun.switch_pokemon` now invokes `_menu_pause_boundary()` before `set_action_cursor(2)`; the full 56-test unittest suite passed.

## 2026-08-08 trainer battle branch evidence

- Same command-menu checkpoint, Mach Punch: observed Krabby net -5 HP after Oran Berry and Aqua Jet -24 HP to Chimchar (32 -> 8); unsafe.
- Same checkpoint, switch to Pidgey: Aqua Jet dealt 10 (33 -> 23) with no damage to Krabby. Gust then dealt 10 (27 -> 17), Stomp dealt 12 (23 -> 11); a second ordinary attack is not survivable. Sand Attack lowered accuracy but Stomp still KO'd Pidgey from 12 HP.
- Same checkpoint, switch to Seedot: Aqua Jet dealt 4 (34 -> 30). Nature Power deterministically produced Stomp + flinch (Seedot 30 -> 20, PP unchanged); unsafe as a repeated line. Bide absorbed the next two Stomps and released for a deterministic Krabby KO; Seedot settled at 10 HP and the trainer sent out species 193 Lv9.
- Verification lesson: a command-menu return can precede the final queued foe action by hundreds of frames. A post-action result is not stable until battle text has drained and HP/PP/species remain unchanged across an additional wait; early observations were recorded as provisional only.
- Yanma contingency: switching Chimchar (12 HP) into Yanma's Sonic Boom left it at 12 because the fixed 20 damage cannot exceed its 32 max HP; switching to Pidgey then left Pidgey at 13. Battle Bag/Potion was explicitly rejected with `Items can't be used now`; Sand Attack still allowed Sonic Boom to hit and KO Pidgey. The current party has no reliable second-turn answer to this foe without added HP/levels or a better counter.
# Capture preparation

- Granite Cave Cufant, Grotle Absorb: the first selected action failed to execute because live paralysis triggered. Target HP and PP were unchanged; Grotle took 4 damage. Capture certificates must surface the 25% full-paralysis action-failure risk whenever the active battler has status bit `0x40`.
- Granite Cave Cufant, Grotle Absorb: the first successful hit dealt 9 versus the static 11.18–13.15 range. Persist `(388, 71, 878) = 9` as exact matchup experience and prefer that observed bound on subsequent capture turns.
- Granite Cave encounter filtering: `escape_battle` reached `move_menu` from a stale Fight confirmation and aborted. The helper must press `B`, return to the command grid, and continue fleeing; it must never convert this state into an attack.
- Granite Cave Phanpy capture: `switch_pokemon(slot=3)` correctly changed Grotle to Onix, then treated the persistent Pokemon command cursor as an open Shift submenu and sent a redundant A. That consumed an unintended Tackle plus a second enemy action (Phanpy 32 -> 27, Tackle PP 35 -> 34). The battler-identity change is sufficient acknowledgement; never send a post-ACK confirmation based only on the stale command cursor. Replay `runtime/session/phanpy-pre-weaken.ss` before promotion and require Onix active with Phanpy still 32/32 and Tackle PP still 35 immediately after the switch helper returns.
- Granite Cave Phanpy capture: Onix Tackle at Attack stage -1 dealt integer 5 while the static interval was 5.69-6.69. Static bounds must respect cartridge integer damage: floor the conservative low roll and ceil the conservative high roll. The corrected interval is 5-7; observed state (Phanpy 27 -> 22, Tackle PP 34 -> 33) is inside it.
