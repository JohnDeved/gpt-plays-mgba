# Goal-run regression ledger

## 2026-08-09 Route 109 Tuber Ricky execution

- Target `map:0:24/local:7` was defeated and verified from RAM: live battle text reported `Player defeated Tuber Ricky!`, then the post-battle dialogue closed to Route 109 overworld with `field_message_mode=0` and `battle_active=false`. Forward checkpoint: `runtime/session/route109-ricky-defeated-forward-20260809.ss` (SHA-256 `f2561f86fc5a70e58d59075a9092fd2e44754b5f20c88be410964feec5bd6922`).
- Historical trainer data said double, but the live run exposed a sequential single command owner (`field_mode` 34/46/50); stale `gBattleMons` slots 2/3 must not determine format. Decoder now trusts double-specific field modes and has a regression test.
- Prepared opening uncertainty resolved adversarially: Aipom Fake Out targeted Grotle, not the planned Staravia partner. The controller re-read RAM, kept Grotle, and defeated Aipom, Nidorino, and Luxio with live re-planning.
- Damage mismatches: Grotle Razor Leaf dealt 21 to Aipom, Nidorino Poison Tail dealt 20 to Grotle, and Phanpy Bulldoze dealt 32 to Luxio. Static bounds are estimates only; retain exact live samples and re-evaluate custom damage after each action.
- First live switch to Phanpy changed move-slot-0 PP 30→29 without a matching move-text line; the second switch to Staravia produced no PP delta. Treat the first result as an unresolved switch-interface regression and verify active identity plus PP before accepting future switch actions.

## 2026-08-09 Route 109 Tuber Hailey execution

- Target `map:0:24/local:21` was identified from the live event template and ROM script as Tuber Hailey, trainer ID 697. The live battle was a required sequential single with Mienfoo, Nidorina, and Flaaffy; all three were defeated.
- Victory was verified by battle text `Player defeated Tuber Hailey!`, then Route 109 overworld RAM with `field_message_mode=0` and `battle_active=false`. Forward checkpoint: `runtime/session/route109-tuber-hailey-defeated-forward-20260809.ss` (SHA-256 `4691a9c341663414382753b247886147eb547eaeffde64c9e6c6567625b553fd`).
- The initial A timing incident reproduced as a short press no-op; a bounded longer press reached the battle intro. The later delayed-turn issue was different: Bulldoze was queued, but an early command-menu read occurred before the final queued action resolved. Do not accept a post-action result until HP/PP/species and battle text are stable after an additional wait.
- Phanpy fainted after Mienfoo was defeated: Nidorina's Water Pulse PP decreased while Phanpy went 20→0 before the queued Bulldoze result appeared. The forced switch selected Cufant; it defeated Nidorina with two Bulldozes, then switched to Onix after Flaaffy paralyzed Cufant. Onix defeated Flaaffy with two Rock Tombs.
- Damage mismatches retained: Mienfoo Rock Tomb→Venipede 18; Cufant Bulldoze→Nidorina 30; Cufant Bulldoze→Flaaffy 34; Onix Rock Tomb→Flaaffy 9. These are live samples, not universal bounds; future certificates must prefer exact matchup experience only when stage/status context matches.

## 2026-08-09 map transition direction regression

- The first map-transition helper attempt was rejected before input because ROM edge labels (`south`) were passed to the controller, which accepts pad directions (`DOWN`). The fix is an explicit `north/UP`, `south/DOWN`, `west/LEFT`, `east/RIGHT` mapping.
- The first center-nurse approach was rejected because the player stood on a valid elevation-0 warp/transition tile and the pathfinder disallowed a 0 -> 3 step to the normal floor. Transition elevation 0 is allowed to connect to a neighboring floor layer; ordinary 3 -> 1/4 bridge changes remain blocked until specifically decoded.
- Endless Candy at Phanpy Lv10 paused on move learning. ROM/RAM decision: replace Odor Sleuth (slot0) with Ice Shard (move420), then close the target screen with B. At Lv15, replace Growl (slot1) with Bulldoze (move523). The original helper intentionally stopped before menu cleanup; added `resolve_field_move_learning` with explicit slot and post-RAM move verification.

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
# 2026-08-09 Route 110 Camper Gavi second retry

- The revised retry defeated Bibarel, Ponyta, Eelektrik, and Sunflora from live battle RAM, but did not complete the trainer. Staravia entered Sunflora at 9 HP and was KO'd by Sludge Bomb after Aerial Ace dealt 30; Onix then dealt 9 with Rock Tomb before Sunflora's Energy Ball KO'd it; Cufant dealt 10 with Rock Smash from 3 HP and was KO'd; Phanpy finished Sunflora with Rock Throw and entered Dustox as the only live member.
- Dustox was live-verified as species 269 Lv17 with Venoshock, Infestation, Roost, and Toxic. Phanpy's priority Ice Shard dealt 5 (52 -> 47), then Infestation and its residual damage reduced Phanpy 12 -> 0. The battle text `GPT whited out!` and post-loss auto-heal were verified from RAM.
- Reusable cause: the current six-member team has no robust endgame reserve after the Eelektrik phase. Aerial Ace is not enough when Staravia reaches Sunflora damaged, Onix is a hard exclusion into Energy Ball, and Cufant cannot be preserved at 3 HP. A new full-health Sunflora/Dustox counter must be obtained and explicitly preserved before re-entry.
- Tooling note: after KO/send-out transitions the ROM uses unclassified field message modes 45 and 17. A single diagnostic A press advanced the Sunflora faint message to the forced party-switch prompt; screenshots were used only to identify that boundary. Add these modes to the RAM decoder only after regression tests confirm they are battle transition states.

# Capture preparation

# 2026-08-09 Granite Cave Bite double-submit

- Wild Aron Lv8 capture attempt exposed a tooling, not strategy, failure. The canonical certificate modeled Grotle Bite as 17-22 damage against Aron 27/27 and selected it as an explicit heuristic nonlethal weakening attempt because no move had a guaranteed nonlethal critical bound.
- `game_battle_commit` returned after Aron was 10/27 and Grotle Bite PP was 24. `game_battle_verify` then reported another `Grotle used Bite!`, Aron fainted, and final Bite PP was 23. One requested action therefore crossed an unstable queued-action boundary and was submitted twice.
- Recovery policy: do not immediately chain commit/verify at a command-menu return. Restore the newest verified forward Granite hunt checkpoint for this interface error, then use one direct action with stable HP/PP/species polling. Incident: `runtime/session/incident-granite-bite-double-submit.json`.

# 2026-08-09 Endless Candy move-learning acknowledgement

- Sandile's explicit Torment-to-Rock-Tomb replacement exposed a field-interface defect: selecting the forget slot correctly changed the UI to `Poof!`, `forgot`, `And...`, and `learned`, but the resolver waited for the party move tuple without acknowledging those pages and timed out. The live party tuple and text later verified the intended replacement `[43, 644, 317, 44]`.
- Reusable cause/fix: move-learning acknowledgement is a sequence boundary, not a single party-RAM write. Advance only RAM-verified move-learning pages; stop at `Use on which Pokémon?` and press B. Never let generic cleanup press A through a reusable Endless Candy target layer. Incident: `runtime/session/incident-move-learning-ack.json`.
- Follow-up regression: the first repair still crossed an unknown field mode 21 after the Snarl replacement and consumed extra Endless Candy uses, reaching an unintended Bulldoze prompt. Cleanup now permits A only for explicit `Poof!`/`forgot`/`And...`/`learned` pages, uses B-only layer closure, and aborts on nested move-learning text. Incident: `runtime/session/incident-move-learning-target-double-candy.json`.

# 2026-08-09 Move-learning active-printer boundary

- The live Snarl and Bulldoze prompts exposed a second decoder issue: `text.current` could retain a partial/older page while an active field printer contained `Which move should be forgotten?` or `Sandile learned ...`. RAM party tuples remained authoritative: Sandile changed to Snarl in slot 0 and Bulldoze in slot 1 only after the learned page.
- Reusable cause: the text decoder retains multiple printers for forensics; current text is not always the input-owning page after a transition. Tooling fix: gate A presses on active window-5/6 printer pages, acknowledge the mode-33 question before directional selection, and close the post-learn target with B only. Incident: `runtime/session/incident-move-learning-active-printer-20260809.json`.
- Regression: `python3 -m unittest discover -s tests -q` returned 96 passing tests; a synthetic stale-current/active-page case confirmed that the active page wins.

# 2026-08-09 Route 110 sight-line seeker regression

- `follow_live_path_to_npc(local_id=23, interact=false)` entered Gavi's downward RAM/ROM sight ray at `(14,91)` and opened prebattle dialogue before reaching a side-adjacent interaction tile. The live boundary was map `(0,25)`, field mode 2, text `I found some cool Pokémon in the grass!`; no battle turn or strategic loss occurred.
- Reusable cause: the general adaptive route blocks trainer sight tiles, but the NPC-specific seeker used its own approach path without that exclusion. Recovery is permitted by the forward-only policy: load only `slateport-sandile-l17-forward-20260809.state`, then patch and regression-test the seeker before retrying.
- Incident: `runtime/session/incident-route110-sightline-seeker-20260809.json`.

# 2026-08-09 Gavi opening damage-model regression

- The prepared opening Razor Leaf was executed once and verified: Bibarel 56→24 and PP 10→9; Grotle 57→29 and Razor Leaf PP 25→24. The opponent PP/HP delta identifies Super Fang as the incoming move and fixes its damage at 28.
- Static Razor Leaf evidence predicted 37–45 but live RAM measured 32. This is a model mismatch, not an action failure. Persist `(388,75,400)=32` and `(400,162,388)=28` as exact samples; future certificates must prefer these samples only when the same stage/status context applies and must not call this line forced.
- Incident: `runtime/session/incident-gavi-opening-damage-model-20260809.json`.

# 2026-08-09 Gavi stale command-switch boundary

- After Onix's Rock Tomb, the adapter briefly classified the battle as `command_menu` while field mode 34 still owned a queued Ponyta turn. The voluntary switch cursor move was rejected; once the queue drained, RAM verified `Double Kick` hit twice, Onix fainted, and field mode 13 exposed `Choose a Pokémon.`
- Reusable cause: a command-menu signature is provisional until active identity, HP/PP, field tasks, and drained text are stable across the queued-turn boundary. Continue via the forced party selector; never press a voluntary-switch cursor into this provisional state.
- Incident: `runtime/session/incident-gavi-stale-command-switch-20260809.json`.

# 2026-08-09 Route 110 Camper Gavi accidental third engagement

- This was not a planned third retry: the live navigator ended directly below Camper Gavi while routing from the Route 110 entrance toward grass for a Shinx counter. The trainer sight line triggered the required battle before the new preparation plan was ready. No old savestate was loaded.
- Live RAM verified the sequence: Bibarel was defeated; Eelektrik was poisoned and ultimately defeated after Venipede was sacrificed, Grotle took Toxic, Onix was switched in and then KO'd by the known Mega Drain branch, Staravia was KO'd after Quick Attack, and Phanpy finished Eelektrik with priority Ice Shard. Sunflora then used Energy Ball, which reported not very effective but still KO'd 15-HP badly poisoned Grotle; Phanpy's Rock Throw dealt damage and Sunflora remained at 47/54 before Phanpy was KO'd. `GPT whited out!` and auto-heal were verified from RAM.
- Reusable strategic cause: this party has no full-health answer to Sunflora after the Eelektrik phase. The missing preparation is a dedicated, full-health Sunflora/Dustox counter that is preserved through Bibarel/Eelektrik, not another ordering of the depleted five-member line.
- Potion use was tested once against the live battle command state and rejected by the ROM with exact feedback `Items can't be used now.<CTRL_08>`; HP and inventory were unchanged. Do not retry battle medicine through that interface.
- Forced replacement observations must remain authoritative: switching to Grotle after Venipede fainted produced an unsolicited queued Bite while Eelektrik used Toxic; later forced replacements resolved cleanly. Treat active battler identity, HP, PP, and drained battle text as the acknowledgement boundary, and record any queued move rather than assuming a switch-only turn.
- New forward recovery checkpoint: `runtime/session/route110-local23-loss3-healed-forward-20260809.ss`, SHA-256 `2d454c764d605080b566475c3fd0a237a4846f104a44440976c3bc57b15afc43`, map `(0,1)` at `(19,20)`, battle inactive, party healed. This checkpoint was saved from live post-loss RAM and was not loaded.

- Granite Cave Cufant, Grotle Absorb: the first selected action failed to execute because live paralysis triggered. Target HP and PP were unchanged; Grotle took 4 damage. Capture certificates must surface the 25% full-paralysis action-failure risk whenever the active battler has status bit `0x40`.
- Granite Cave Cufant, Grotle Absorb: the first successful hit dealt 9 versus the static 11.18–13.15 range. Persist `(388, 71, 878) = 9` as exact matchup experience and prefer that observed bound on subsequent capture turns.
- Granite Cave encounter filtering: `escape_battle` reached `move_menu` from a stale Fight confirmation and aborted. The helper must press `B`, return to the command grid, and continue fleeing; it must never convert this state into an attack.
- Granite Cave Phanpy capture: `switch_pokemon(slot=3)` correctly changed Grotle to Onix, then treated the persistent Pokemon command cursor as an open Shift submenu and sent a redundant A. That consumed an unintended Tackle plus a second enemy action (Phanpy 32 -> 27, Tackle PP 35 -> 34). The battler-identity change is sufficient acknowledgement; never send a post-ACK confirmation based only on the stale command cursor. Replay `runtime/session/phanpy-pre-weaken.ss` before promotion and require Onix active with Phanpy still 32/32 and Tackle PP still 35 immediately after the switch helper returns.
- Granite Cave Phanpy capture: Onix Tackle at Attack stage -1 dealt integer 5 while the static interval was 5.69-6.69. Static bounds must respect cartridge integer damage: floor the conservative low roll and ceil the conservative high roll. The corrected interval is 5-7; observed state (Phanpy 27 -> 22, Tackle PP 34 -> 33) is inside it.
- Granite Cave Phanpy capture: with Onix at Attack stage -3 and accuracy stage -2, Tackle was selected from a static safe interval of 4-5 but failed while the battle text reported infatuation; Phanpy stayed 22/32, Tackle PP 32 -> 31, and Growl lowered Attack again. The capture certificate must expose accuracy-stage and volatile-infatuation success uncertainty; static nonlethal damage alone is insufficient for expected-value ranking.
- Granite Cave Phanpy capture: at the same accuracy/infatuation state, Tackle connected for exact damage 3 (Phanpy 22 -> 19; Tackle PP 31 -> 30). Phanpy Rock Throw dealt 1 to Onix, and Weak Armor lowered Onix Defense while raising Speed. Persist exact sample `(95, 33, 231) = 3`; the interaction remains safe but incoming damage and volatile ability effects must be included in future turn risk.
- Granite Cave Phanpy capture: the unannotated learned sample `(95, 33, 231) = 3` was incorrectly stage-scaled after Onix’s Weak Armor change, yielding a false 1-1 estimate. Known moves must use the ROM calculation whenever attacker/defender stages are non-neutral unless the sample stores verified stage context; the static live bound is 3-5.
- Granite Cave Phanpy capture: Tackle connected for exact damage 3 again (Phanpy 19 -> 16; Tackle PP 30 -> 29); Phanpy Growl lowered Onix Attack from stage -4 to -5 without damaging Onix. The exact sample is repeatable under the current live volatile state.
- Granite Cave Phanpy capture: after switching back to neutral Attack, the certificate incorrectly reused the unannotated 3-damage legacy sample; Tackle dealt exact 7 (Phanpy 16 -> 9; Tackle PP 27 -> 26). Known move IDs must never use legacy samples as standalone bounds; the ROM formula is authoritative across stat stages and random rolls. Weak Armor again dealt 1 incoming Rock Throw damage and changed Onix Defense/Speed.
- Granite Cave Phanpy capture: first L throw at 9/32 consumed one ball (23 -> 22) and failed; the battle text said “It appeared to be caught!” but party count stayed 5 and the battle remained active. Rock Throw reduced Onix 42 -> 41. The helper also reported Grotle’s party-slot HP as `active_hp_after`; fixed it to read active battle/identity-matched RAM HP.
- Granite Cave Phanpy capture: second L throw at 9/32 consumed one ball (22 -> 21) and cleanly reported “broke free”; party count stayed 5, Phanpy stayed 9/32, Growl lowered Onix Attack, and Onix stayed 41/44. The corrected helper reported active HP 41.
- Granite Cave Phanpy capture: third L throw at 9/32 consumed one ball (21 -> 20) and reported “It was so close, too!”; party count stayed 5, Phanpy stayed 9/32, Onix stayed 41/44, and Growl lowered Attack again.
- Granite Cave Phanpy capture: fourth L throw at 9/32 consumed one ball (20 -> 19) and again said “It appeared to be caught!” without party insertion; Phanpy stayed 9/32, Onix stayed 41/44, and Odor Sleuth/Identify caused no HP loss.
- Granite Cave Phanpy capture: the next Tackle failed under infatuation/accuracy stage -2; Phanpy stayed 16/32, Tackle PP 29 -> 28, and Sand Attack lowered Onix accuracy to stage -3. No HP changed; continue only under the strict nonlethal bound and resource-preservation policy.
- Granite Cave Phanpy capture: a further Tackle failed while Onix remained infatuated at accuracy stage -3; Phanpy stayed 16/32, Tackle PP 28 -> 27, and Odor Sleuth failed. Onix remained 43/44. Repeated misses make a stage/volatile reset a candidate, but its incoming-turn risk is not yet ROM-bounded for the bench line.

# 2026-08-09 Route 110 Camper Gavi revision-3 retry

- This was the fourth Gavi attempt and was a deliberate strategic retry from a healed, six-member party. It was not corrected with a savestate: the live loss was preserved for learning and the game auto-healed normally.
- Canonical battle RAM verified the sequence and the loss. Grotle defeated Bibarel with two Razor Leafs but reached Ponyta at 7/57. Onix and then Phanpy defeated Ponyta, with Phanpy reaching Eelektrik at 28/58. Cufant entered Eelektrik at full health, but Rock Smash plus Super Fang/ Shock Wave reduced it 56 -> 3; Cufant was switched out. Staravia's Aerial Ace chipped Eelektrik, then Shock Wave KO'd Staravia. Venipede's Poison Tail triggered Eelektrik's Sitrus Berry (28 -> 19, then back to 32) and Venipede was KO'd. Phanpy, Grotle, Onix, and Cufant then failed sequentially to the remaining Mega Drain/Shock Wave pressure. Eelektrik survived at 25/54 when the final Cufant fainted.
- The post-battle text `GPT whited out!` and battle-inactive RAM were verified. The live overworld returned to map `(0,1)` at `(19,20)` and the full six-member party was auto-healed: Grotle 57/57, Staravia 46/46, Venipede 41/41, Onix 44/44, Phanpy 58/58, Cufant 56/56.
- Reusable strategic cause: the current six-member party has no reliable full-health answer to Levitate Eelektrik. Cufant/Staravia chip is insufficient, Ground moves cannot be trusted against Levitate, and the remaining team cannot survive the custom Eelektrik's Shock Wave/Mega Drain cycle. Gavi strategy status is now `not_ready`; the same unchanged plan has failed three times.
- Model/tooling evidence: Rock Smash produced 8 damage and a 14-damage critical in this matchup; Shock Wave produced 22; Mega Drain could KO Onix. The generic evaluator did not provide a safe full-matchup model. During the run, `game_battle_verify` stopped at a stale move menu twice and `game_battle_advance` mislabeled a live party-switch transition as `not_in_battle`; these are action-changing interface defects. Grotle's final Razor Leaf had unchanged PP because the battler fainted before the queued move executed, so an observed move-menu return is not sufficient acknowledgement.
- Newest forward checkpoint: `runtime/session/route110-gavi-loss4-healed-forward-20260809.ss`, SHA-256 `1156accea26e3a54ab7a32de86eb2c901354435d6704ff19daf0561aef8b62d0`, canonical state hash `078e95499b32de32`. It was saved from the live post-loss state and was not loaded. Under the updated recovery policy, it may be loaded only if a later accidental input/navigation mistake or tooling failure corrupts the live run; it may not be used to retry this deliberate strategic loss.

# 2026-08-09 Gavi stale Rock Smash acknowledgement

- During the live revision-4 battle, after Cufant reached 9/56 and Eelektrik 37/54, the adapter returned a command-menu acknowledgement for a third Rock Smash even though Cufant's PP remained 13. Stable RAM then showed Cufant at 0/56, Eelektrik still 37/54, and the drained text `Cufant fainted! Choose a Pokémon.` The move did not execute; a queued Eelektrik action resolved across a stale menu boundary.
- Reusable cause: command-menu state is not an action acknowledgement. Require PP decrement, stable battler identity/HP, field-task quiescence, and drained battle text before accepting a move result. A no-PP-change result must be treated as a tooling incident and must not receive another gameplay input.
- Incident: `runtime/session/incident-gavi-stale-rock-smash-20260809.json`.
- Recovery: only the newest verified forward pre-battle checkpoint may be loaded to repair this tooling corruption; the deliberate strategic loss itself remains non-rewindable.

# 2026-08-09 Gavi revision-5 preparation and PC recovery

- Revision 4 was a deliberate strategic loss, not a tooling mistake: the live Eelektrik phase consumed the Sandile line and left no robust counter for the remaining required roster. The loss was preserved and the game auto-healed; no savestate was loaded.
- Recovery action: switch to the verified PC workflow, withdraw Rhyhorn from Box 0 slot 1, exit cleanly, and verify party RAM. The PC observer misclassified storage screens as `overworld`, and after the withdrawal the party count temporarily read 5 while slot 5 already contained a valid Rhyhorn record. Exiting the box-operation prompt reconciled the count to 6.
- Authoritative result: six party records are present with valid checksums; Rhyhorn species 111 is slot 5, Lv17, 59/59 HP, moves [30,479,184,23] (Horn Attack, Smack Down, Scary Face, Stomp). Storage digest changed from `9c52b77f...` to `432b750c...` and the forward checkpoint is `runtime/session/slateport-rhyhorn-l17-forward-20260809.state` (SHA-256 `8c29e461c306a471a5b24b2295f92f5ebbdd09853de6487e77ffa3aec498b9ed`).
- Endless Candy at Lv12 exposed a move-learning resolver boundary: the current page said `already knows four moves` while the active-printer filter was temporarily empty, causing a false timeout. The page was advanced only after RAM/text confirmation, the resolver selected Tail Whip slot 1 for Smack Down, and the resulting tuple was verified. The resolver now has a bounded move-learning current-page fallback; 96 unit tests pass.
- Revision-5 plan: preserve Grotle for the Bibarel opening, Phanpy for the verified Ponyta Bulldoze line, Staravia for Sunflora/Dustox, and Rhyhorn for expected-best Smack Down grounding against live Levitate Eelektrik. Smack Down grounding, exact damage, and the first AI action remain action-changing; stop and replan if the Rhyhorn survival gate fails.
