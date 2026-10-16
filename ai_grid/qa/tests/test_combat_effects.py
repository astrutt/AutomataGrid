# ai_grid/qa/tests/test_combat_effects.py
# Comprehensive Async Unit Tests for Timed Combat Status Effects:
# THROTTLED, CORRUPTED, ROOTED, and ENCRYPTED.
# Tests status application, stat drains, duration decay, turn restrictions,
# damage modifiers, and clean restoration upon expiration.

import asyncio
import datetime
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

from ai_grid.grid_combat import CombatEngine, Entity, StatusEffect
from ai_grid.models import Base
from ai_grid.grid_db import ArenaDB


class TestCombatStatusEffects(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Sandbox safe: use in-memory SQLite / temp db
        self.db_path = f"test_combat_effects_{int(datetime.datetime.now().timestamp() * 1000)}.db"
        self.db = ArenaDB(self.db_path)
        self.async_session = self.db.async_session

        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.send_mock = AsyncMock()
        self.engine = CombatEngine("match_test_effects", "!a", self.send_mock)
        # Disable sleep interval in test harness for fast execution (<1s)
        self.engine.sleep_interval = 0

    async def asyncTearDown(self):
        if hasattr(self, 'db') and hasattr(self.db, 'engine'):
            await self.db.engine.dispose()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    # -------------------------------------------------------------------------
    # 1. Status Effect Data Structures & Interoperability
    # -------------------------------------------------------------------------
    async def test_status_effect_data_structure_and_access(self):
        """StatusEffect supports object attributes, dict indexing, and metadata."""
        effect = StatusEffect("CORRUPTED", duration=3, source="HackerUnit", metadata={"stat": "cpu", "drained_amount": 4})
        self.assertEqual(effect.name, "CORRUPTED")
        self.assertEqual(effect.duration, 3)
        self.assertEqual(effect.source, "HackerUnit")
        self.assertEqual(effect["duration"], 3)
        self.assertEqual(effect["stat"], "cpu")
        self.assertEqual(effect.get("drained_amount"), 4)
        self.assertIn("stat", effect)
        self.assertIn("duration", effect)
        self.assertIsInstance(effect.to_dict(), dict)

    # -------------------------------------------------------------------------
    # 2. Kinetic Critical Hits & ROOTED Behavior
    # -------------------------------------------------------------------------
    async def test_kinetic_crit_applies_rooted(self):
        """Kinetic critical hit applies ROOTED (1 turn)."""
        attacker = Entity("KineticAttacker", cpu=10, ram=5, bnd=5, sec=5, alg=100) # ALG=100 guarantees crit
        defender = Entity("TargetDrone", cpu=5, ram=5, bnd=5, sec=2, alg=0, hp=500, max_hp=500)
        self.engine.add_entity(attacker)
        self.engine.add_entity(defender)

        res = self.engine._execute_attack(attacker, "TargetDrone", mode="kinetic")
        self.assertIn("ROOTED APPLIED", res)
        self.assertTrue(defender.has_status("ROOTED"))
        rooted_status = defender.get_status("ROOTED")
        self.assertEqual(rooted_status.duration, 1)
        self.assertEqual(rooted_status.source, "KineticAttacker")

        # Event emitted in turn_events
        proc_events = [e for e in self.engine.turn_events if e.get("type") == "status_proc" and e.get("status") == "ROOTED"]
        self.assertEqual(len(proc_events), 1)
        self.assertEqual(proc_events[0]["target"], "TargetDrone")

    async def test_rooted_prevents_flee_and_move(self):
        """ROOTED target attempting flee/move is prevented from escaping."""
        target = Entity("RootedUnit", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=50, max_hp=50, up=50)
        enemy = Entity("GuardBot", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=50, max_hp=50, up=50)
        self.engine.add_entity(target)
        self.engine.add_entity(enemy)

        # Apply ROOTED
        target.apply_status("ROOTED", duration=1)
        self.assertTrue(target.has_status("ROOTED"))

        # Queue flee
        self.engine.queue_command("RootedUnit", "flee")
        self.engine.queue_command("GuardBot", "defend")

        await self.engine.resolve_turn()

        # Flee should be blocked, target still alive in match
        self.assertTrue(target.is_alive)
        self.assertEqual(target.hp, 50)

        # Flee failure event with reason='rooted'
        flee_events = [e for e in self.engine.last_turn_events if e.get("type") == "flee"]
        self.assertEqual(len(flee_events), 1)
        self.assertFalse(flee_events[0]["success"])
        self.assertEqual(flee_events[0].get("reason"), "rooted")

    async def test_rooted_blocks_move_command_in_combat(self):
        """'move' combat alias is also blocked by ROOTED."""
        target = Entity("RootedRunner", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=50, max_hp=50, up=50)
        enemy = Entity("Chaser", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=50, max_hp=50, up=50)
        self.engine.add_entity(target)
        self.engine.add_entity(enemy)

        target.apply_status("ROOTED", duration=1)
        self.engine.queue_command("RootedRunner", "move north")
        self.engine.queue_command("Chaser", "defend")

        await self.engine.resolve_turn()
        self.assertTrue(target.is_alive)
        flee_events = [e for e in self.engine.last_turn_events if e.get("type") == "flee"]
        self.assertEqual(len(flee_events), 1)
        self.assertFalse(flee_events[0]["success"])
        self.assertEqual(flee_events[0].get("reason"), "rooted")

    # -------------------------------------------------------------------------
    # 3. Cyber Critical Hits & THROTTLED Behavior
    # -------------------------------------------------------------------------
    async def test_cyber_crit_applies_throttled_and_reduces_bnd(self):
        """Cyber critical hit applies THROTTLED (1 turn) and reduces effective BND by 50%."""
        attacker = Entity("CyberAttacker", cpu=5, ram=5, bnd=10, sec=5, alg=100) # ALG=100 guarantees crit
        defender = Entity("TargetServer", cpu=5, ram=5, bnd=10, sec=2, alg=0, hp=100, max_hp=100)
        self.engine.add_entity(attacker)
        self.engine.add_entity(defender)

        self.assertEqual(defender.bnd, 10)
        res = self.engine._execute_attack(attacker, "TargetServer", mode="cyber")
        self.assertIn("THROTTLED APPLIED", res)
        self.assertTrue(defender.has_status("THROTTLED"))

        # BND reduced by 50% (from 10 to 5)
        self.assertEqual(defender.bnd, 5)
        self.assertEqual(defender.effective_bnd, 5)

        status = defender.get_status("THROTTLED")
        self.assertEqual(status.duration, 1)
        self.assertEqual(status.metadata.get("drained_bnd"), 5)

    async def test_throttled_skips_queued_offensive_action(self):
        """THROTTLED target is delayed and skips their queued offensive action."""
        victim = Entity("VictimNode", cpu=10, ram=5, bnd=10, sec=5, alg=0, hp=100, max_hp=100, up=100)
        opponent = Entity("OpponentNode", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=100, max_hp=100, up=100)
        self.engine.add_entity(victim)
        self.engine.add_entity(opponent)

        # Apply THROTTLED to victim
        victim.apply_status("THROTTLED", duration=1)
        self.assertTrue(victim.has_status("THROTTLED"))

        # Victim queues an attack; opponent defends
        self.engine.queue_command("VictimNode", "attack OpponentNode")
        self.engine.queue_command("OpponentNode", "defend")

        await self.engine.resolve_turn()

        # Victim's attack was skipped due to throttled delay
        delay_events = [e for e in self.engine.last_turn_events if e.get("type") == "throttled_delay"]
        self.assertEqual(len(delay_events), 1)
        self.assertEqual(delay_events[0]["actor"], "VictimNode")

        # Opponent took no damage because attack was skipped
        self.assertEqual(opponent.hp, 100)

        # THROTTLED expired at end of turn and restored BND
        self.assertFalse(victim.has_status("THROTTLED"))
        self.assertEqual(victim.bnd, 10)

    # -------------------------------------------------------------------------
    # 4. Defensive Stance: ENCRYPTED Behavior
    # -------------------------------------------------------------------------
    async def test_defend_encrypt_activates_encrypted_stance_consuming_10_up(self):
        """'defend encrypt' activates ENCRYPTED stance (2 turns) consuming 10 uP."""
        tank = Entity("CryptoTank", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=100, max_hp=100, up=50)
        enemy = Entity("AttackerAI", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=100, max_hp=100, up=50)
        self.engine.add_entity(tank)
        self.engine.add_entity(enemy)

        self.engine.queue_command("CryptoTank", "defend encrypt")
        self.engine.queue_command("AttackerAI", "defend")

        await self.engine.resolve_turn()

        # Consumed exactly 10 uP (from 50 to 40)
        self.assertEqual(tank.up, 40)

        # Gained ENCRYPTED status (duration decrements to 1 after turn 1)
        self.assertTrue(tank.has_status("ENCRYPTED"))
        self.assertEqual(tank.get_status("ENCRYPTED").duration, 1)

        # Turn event recorded defend_stance encrypt
        stance_events = [e for e in self.engine.last_turn_events if e.get("type") == "defend_stance" and e.get("mode") == "encrypt"]
        self.assertEqual(len(stance_events), 1)

    async def test_defend_encrypt_fails_on_insufficient_power(self):
        """'defend encrypt' fails if actor has less than 10 uP."""
        tank = Entity("PowerlessTank", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=100, max_hp=100, up=8) # 8 uP < 10 uP
        enemy = Entity("AttackerAI", cpu=5, ram=5, bnd=5, sec=5, alg=5, hp=100, max_hp=100, up=50)
        self.engine.add_entity(tank)
        self.engine.add_entity(enemy)

        self.engine.queue_command("PowerlessTank", "defend encrypt")
        self.engine.queue_command("AttackerAI", "defend")

        await self.engine.resolve_turn()

        self.assertFalse(tank.has_status("ENCRYPTED"))
        self.assertEqual(tank.up, 8) # uP not consumed on failure
        fail_events = [e for e in self.engine.last_turn_events if e.get("type") == "insufficient_power"]
        self.assertEqual(len(fail_events), 1)

    async def test_encrypted_grants_cyber_immunity_and_kinetic_vulnerability(self):
        """ENCRYPTED targets take 0 cyber damage and +15% kinetic damage."""
        target = Entity("ShieldedTarget", cpu=10, ram=10, bnd=10, sec=5, alg=0, hp=100, max_hp=100)
        cyber_attacker = Entity("CyberAttacker", cpu=5, ram=5, bnd=10, sec=5, alg=0)
        kinetic_attacker = Entity("KineticAttacker", cpu=10, ram=5, bnd=5, sec=5, alg=0)

        self.engine.add_entity(target)
        self.engine.add_entity(cyber_attacker)
        self.engine.add_entity(kinetic_attacker)

        # Baseline kinetic damage without ENCRYPTED:
        # raw = (10 * 5) + 5 = 55. Protection = target.sec = 5. Final = 50 DMG.
        dmg_normal = 50

        # Apply ENCRYPTED
        target.apply_status("ENCRYPTED", duration=2)
        self.assertTrue(target.has_status("ENCRYPTED"))

        # 1. Cyber attack against ENCRYPTED: deals 0 DMG
        res_cyber = self.engine._execute_attack(cyber_attacker, "ShieldedTarget", mode="cyber")
        self.assertIn("0 DMG", res_cyber)
        self.assertEqual(target.hp, 100) # No damage taken

        # 2. Kinetic attack against ENCRYPTED: +15% damage -> int(50 * 1.15) = 57 DMG
        res_kinetic = self.engine._execute_attack(kinetic_attacker, "ShieldedTarget", mode="kinetic")
        self.assertIn("57 DMG", res_kinetic)
        self.assertEqual(target.hp, 100 - 57)

    # -------------------------------------------------------------------------
    # 5. Cyber Advantage & CORRUPTED Behavior
    # -------------------------------------------------------------------------
    async def test_corrupted_stat_drain_tick_damage_and_clean_restoration(self):
        """CORRUPTED drains a primary stat, ticks damage across multiple turns, and cleanly restores upon expiration."""
        victim = Entity("VictimProc", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=50, max_hp=50)
        enemy = Entity("PassiveEnemy", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=50, max_hp=50)
        self.engine.add_entity(victim)
        self.engine.add_entity(enemy)

        # Apply CORRUPTED with known parameters: 2 turns, drains 4 CPU, 3 tick damage
        victim.apply_status("CORRUPTED", duration=2, metadata={"stat": "cpu", "drained_amount": 4, "tick_damage": 3})

        # CPU was drained from 10 to 6
        self.assertEqual(victim.cpu, 6)
        self.assertTrue(victim.has_status("CORRUPTED"))

        # --- Turn 1 ---
        self.engine.queue_command("VictimProc", "defend")
        self.engine.queue_command("PassiveEnemy", "defend")
        await self.engine.resolve_turn()

        # Took 3 damage tick from corruption pulse
        self.assertEqual(victim.hp, 50 - 3)
        # Duration decremented from 2 to 1
        self.assertTrue(victim.has_status("CORRUPTED"))
        self.assertEqual(victim.get_status("CORRUPTED").duration, 1)
        self.assertEqual(victim.cpu, 6) # Stat remains drained

        # --- Turn 2 ---
        self.engine.queue_command("VictimProc", "defend")
        self.engine.queue_command("PassiveEnemy", "defend")
        await self.engine.resolve_turn()

        # Took second 3 damage tick (total 6 damage)
        self.assertEqual(victim.hp, 50 - 6)

        # Duration expired, CORRUPTED removed, and CPU CLEANLY RESTORED!
        self.assertFalse(victim.has_status("CORRUPTED"))
        self.assertEqual(victim.cpu, 10)

        # Status expire event recorded
        expire_events = [e for e in self.engine.last_turn_events if e.get("type") == "status_expire" and e.get("status") == "CORRUPTED"]
        self.assertEqual(len(expire_events), 1)
        self.assertEqual(expire_events[0]["restored_stat"], "cpu")
        self.assertEqual(expire_events[0]["restored_amount"], 4)

    async def test_cyber_attack_corrupt_verb_procs_corrupted(self):
        """Attacking with 'corrupt' cyber verb applies CORRUPTED."""
        attacker = Entity("CorruptHacker", cpu=5, ram=5, bnd=15, sec=5, alg=100)
        defender = Entity("TargetServer", cpu=10, ram=10, bnd=5, sec=5, alg=0, hp=500, max_hp=500)
        self.engine.add_entity(attacker)
        self.engine.add_entity(defender)

        self.engine.queue_command("CorruptHacker", "corrupt TargetServer")
        res = self.engine._execute_attack(attacker, "TargetServer", mode="cyber")
        self.assertIn("CORRUPTED APPLIED", res)
        self.assertTrue(defender.has_status("CORRUPTED"))
        corr = defender.get_status("CORRUPTED")
        self.assertIn(corr.duration, [2, 3])
        self.assertIn(corr.metadata.get("stat"), ["cpu", "ram", "bnd", "sec"])

    # -------------------------------------------------------------------------
    # 6. Turn Lifecycle & Duration Decay Invariants
    # -------------------------------------------------------------------------
    async def test_status_durations_decrement_turn_by_turn(self):
        """Status effect durations decrement accurately by 1 each combat turn."""
        entity = Entity("TimedEntity", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100)
        opponent = Entity("Opponent", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100)
        self.engine.add_entity(entity)
        self.engine.add_entity(opponent)

        entity.apply_status("CORRUPTED", duration=3, metadata={"stat": "sec", "drained_amount": 2, "tick_damage": 1})
        self.assertEqual(entity.get_status("CORRUPTED").duration, 3)

        # Turn 1
        self.engine.queue_command("TimedEntity", "defend")
        self.engine.queue_command("Opponent", "defend")
        await self.engine.resolve_turn()
        self.assertEqual(entity.get_status("CORRUPTED").duration, 2)

        # Turn 2
        self.engine.queue_command("TimedEntity", "defend")
        self.engine.queue_command("Opponent", "defend")
        await self.engine.resolve_turn()
        self.assertEqual(entity.get_status("CORRUPTED").duration, 1)

        # Turn 3
        self.engine.queue_command("TimedEntity", "defend")
        self.engine.queue_command("Opponent", "defend")
        await self.engine.resolve_turn()
        self.assertFalse(entity.has_status("CORRUPTED"))

    async def test_reapplying_status_restores_prior_stat_before_new_drain(self):
        """Re-applying status effect restores existing stat reduction first, preventing compounding drain corruption."""
        unit = Entity("TestUnit", cpu=10, ram=10, bnd=10, sec=10, alg=0)
        self.assertEqual(unit.bnd, 10)

        # First THROTTLED: 10 -> 5
        unit.apply_status("THROTTLED", duration=1, metadata={"drained_bnd": 5})
        self.assertEqual(unit.bnd, 5)

        # Re-apply THROTTLED with new duration: should restore 5 back to 10, then drain 5 -> remains 5
        unit.apply_status("THROTTLED", duration=1, metadata={"drained_bnd": 5})
        self.assertEqual(unit.bnd, 5)

        # Expiration restores clean 10, not -10 negative stat
        unit.remove_status("THROTTLED")
        self.assertEqual(unit.bnd, 10)

    async def test_fatal_corruption_tick_disconnects_target(self):
        """Target whose HP reaches 0 from corruption tick is properly disconnected."""
        doomed = Entity("DoomedNode", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=2, max_hp=50) # HP=2
        enemy = Entity("Survivor", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=50, max_hp=50)
        self.engine.add_entity(doomed)
        self.engine.add_entity(enemy)

        doomed.apply_status("CORRUPTED", duration=2, metadata={"stat": "ram", "drained_amount": 1, "tick_damage": 5})

        self.engine.queue_command("DoomedNode", "attack Survivor")
        self.engine.queue_command("Survivor", "defend")

        await self.engine.resolve_turn()

        # Doomed unit disconnected from corruption
        self.assertFalse(doomed.is_alive)
        self.assertLessEqual(doomed.hp, 0)
        fatal_events = [e for e in self.engine.last_turn_events if e.get("type") == "fatal" and e.get("reason") == "corruption"]
        self.assertEqual(len(fatal_events), 1)

    async def test_broadcast_state_renders_active_status_tags(self):
        """Engine broadcast_state formats active status tags and durations."""
        fighter = Entity("StatusWarrior", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=50, max_hp=50)
        self.engine.add_entity(fighter)
        fighter.apply_status("ROOTED", duration=1)
        fighter.apply_status("ENCRYPTED", duration=2)

        state = await self.engine.broadcast_state()
        self.assertIn("StatusWarrior", state)
        self.assertIn("ROOTED:1", state)
        self.assertIn("ENCRYPTED:2", state)

    # -------------------------------------------------------------------------
    # 7. Robustness & Adversarial Edge Cases
    # -------------------------------------------------------------------------
    async def test_stat_drain_bounding_prevents_stat_inflation_exploit(self):
        """Applying drain larger than current stat is clamped and restores cleanly without stat inflation."""
        # Test CORRUPTED stat bounding
        unit = Entity("LowStatDrone", cpu=2, ram=2, bnd=2, sec=2, alg=0)
        self.assertEqual(unit.sec, 2)
        # Drain 5 from stat of 2 (should drain only 1 point down to minimum of 1)
        eff = unit.apply_status("CORRUPTED", duration=2, metadata={"stat": "sec", "drained_amount": 5})
        self.assertEqual(unit.sec, 1)
        self.assertEqual(eff.metadata["drained_amount"], 1)

        # Remove status: restores cleanly back to original 2, NOT 1 + 5 = 6!
        unit.remove_status("CORRUPTED")
        self.assertEqual(unit.sec, 2)

        # Test THROTTLED BND bounding
        self.assertEqual(unit.bnd, 2)
        eff_throttled = unit.apply_status("THROTTLED", duration=1, metadata={"drained_bnd": 5})
        self.assertEqual(unit.bnd, 0)
        self.assertEqual(eff_throttled.metadata["drained_bnd"], 2)

        unit.remove_status("THROTTLED")
        self.assertEqual(unit.bnd, 2)

    async def test_double_restore_guard_prevents_stat_multiplication(self):
        """Calling _restore_status_stats multiple times on the same effect object does not duplicate restored stats."""
        unit = Entity("GuardDrone", cpu=10, ram=10, bnd=10, sec=10, alg=0)
        eff = unit.apply_status("CORRUPTED", duration=1, metadata={"stat": "cpu", "drained_amount": 4})
        self.assertEqual(unit.cpu, 6)

        unit._restore_status_stats(eff)
        self.assertEqual(unit.cpu, 10)

        # Second restoration attempt must be a no-op
        unit._restore_status_stats(eff)
        self.assertEqual(unit.cpu, 10)

    async def test_throttled_skips_exploit_and_preserves_zeroday_item_and_up(self):
        """THROTTLED actor attempting exploit is delayed, Zero-Day item is NOT consumed, and uP is NOT deducted."""
        hacker = Entity("ThrottledHacker", cpu=10, ram=10, bnd=10, sec=10, alg=10, hp=100, max_hp=100, up=100, inventory=["Zero-Day Exploit Chain #1"])
        target = Entity("TargetServer", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=100, max_hp=100, up=50)
        self.engine.add_entity(hacker)
        self.engine.add_entity(target)

        hacker.apply_status("THROTTLED", duration=1)
        self.engine.queue_command("ThrottledHacker", "exploit TargetServer")
        self.engine.queue_command("TargetServer", "defend")

        await self.engine.resolve_turn()

        # Exploit delayed
        delay_events = [e for e in self.engine.last_turn_events if e.get("type") == "throttled_delay"]
        self.assertEqual(len(delay_events), 1)
        self.assertEqual(delay_events[0]["actor"], "ThrottledHacker")

        # Inventory item preserved
        self.assertIn("Zero-Day Exploit Chain #1", hacker.inventory)
        # Power not deducted for skipped exploit (remains 100 uP)
        self.assertEqual(hacker.up, 100)
        # Target took no damage
        self.assertEqual(target.hp, 100)

    async def test_cyber_advantage_on_standard_hack_procs_corrupted(self):
        """Attacker with cyber advantage (bnd > target.bnd) issuing standard 'hack' command procs CORRUPTED."""
        attacker = Entity("AdvantageHacker", cpu=5, ram=5, bnd=20, sec=5, alg=0) # bnd=20 > bnd=5, alg=0 (no crit)
        defender = Entity("TargetNode", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=500, max_hp=500)
        self.engine.add_entity(attacker)
        self.engine.add_entity(defender)

        self.engine.queue_command("AdvantageHacker", "hack TargetNode")
        res = self.engine._execute_attack(attacker, "TargetNode", mode="cyber")

        self.assertIn("CORRUPTED APPLIED", res)
        self.assertTrue(defender.has_status("CORRUPTED"))
        self.assertIn(defender.get_status("CORRUPTED").duration, [2, 3])

    async def test_cyber_advantage_on_cyber_crit_procs_corrupted(self):
        """Attacker with cyber advantage issuing cyber crit procs CORRUPTED."""
        attacker = Entity("CritAdvantageHacker", cpu=5, ram=5, bnd=20, sec=5, alg=100) # alg=100 ensures crit
        defender = Entity("TargetNode2", cpu=5, ram=5, bnd=5, sec=5, alg=0, hp=500, max_hp=500)
        self.engine.add_entity(attacker)
        self.engine.add_entity(defender)

        self.engine.queue_command("CritAdvantageHacker", "hack TargetNode2")
        res = self.engine._execute_attack(attacker, "TargetNode2", mode="cyber")

        self.assertIn("CORRUPTED APPLIED", res)
        self.assertTrue(defender.has_status("CORRUPTED"))

    async def test_match_completion_clears_all_statuses_and_restores_stats(self):
        """When match concludes, _check_match_status cleans all active statuses on entities."""
        fighter1 = Entity("Fighter1", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100)
        fighter2 = Entity("Fighter2", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=0, max_hp=100) # Already dead
        self.engine.add_entity(fighter1)
        self.engine.add_entity(fighter2)

        fighter1.apply_status("CORRUPTED", duration=2, metadata={"stat": "cpu", "drained_amount": 4})
        fighter1.apply_status("ENCRYPTED", duration=2)
        self.assertEqual(fighter1.cpu, 6)

        is_active = self.engine._check_match_status()
        self.assertFalse(is_active)
        self.assertEqual(len(fighter1.status_effects), 0)
        self.assertEqual(fighter1.cpu, 10)


    async def test_throttled_sets_original_value_metadata(self):
        """THROTTLED sets both original_bnd and original_value in metadata."""
        unit = Entity("ThrottleMetaUnit", cpu=10, ram=10, bnd=12, sec=10, alg=0)
        eff = unit.apply_status("THROTTLED", duration=1)
        self.assertEqual(eff.metadata.get("original_value"), 12)
        self.assertEqual(eff.metadata.get("original_bnd"), 12)
        self.assertEqual(eff["original_value"], 12)

    async def test_surrender_clears_statuses_and_restores_stats_immediately(self):
        """Surrendering in combat immediately cleans statuses and restores drained stats."""
        surrenderer = Entity("Surrenderer", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)
        enemy = Entity("OpponentBot", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)
        self.engine.add_entity(surrenderer)
        self.engine.add_entity(enemy)

        surrenderer.apply_status("CORRUPTED", duration=3, metadata={"stat": "cpu", "drained_amount": 5})
        surrenderer.apply_status("ENCRYPTED", duration=2)
        self.assertEqual(surrenderer.cpu, 5)

        self.engine.queue_command("Surrenderer", "surrender")
        self.engine.queue_command("OpponentBot", "defend")

        await self.engine.resolve_turn()

        self.assertFalse(surrenderer.is_alive)
        self.assertEqual(len(surrenderer.status_effects), 0)
        self.assertEqual(surrenderer.cpu, 10)

    async def test_flee_clears_statuses_and_restores_stats_immediately(self):
        """Successful flee immediately cleans statuses and restores drained stats."""
        fleer = Entity("Fleer", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)
        enemy = Entity("ChaserBot", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)
        self.engine.add_entity(fleer)
        self.engine.add_entity(enemy)

        fleer.apply_status("CORRUPTED", duration=3, metadata={"stat": "sec", "drained_amount": 4})
        self.assertEqual(fleer.sec, 6)

        self.engine.queue_command("Fleer", "flee")
        self.engine.queue_command("ChaserBot", "defend")

        from unittest.mock import patch
        with patch("random.random", return_value=0.9):
            await self.engine.resolve_turn()

        self.assertFalse(fleer.is_alive)
        self.assertEqual(len(fleer.status_effects), 0)
        self.assertEqual(fleer.sec, 10)

    async def test_fatal_attack_clears_victim_statuses_and_prevents_postmortem_decay(self):
        """Fatal attack immediately clears victim statuses and dead entities do not emit decay/expire events in multi-combatant matches."""
        victim = Entity("VictimUnit", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=10, max_hp=100)
        killer = Entity("KillerUnit", cpu=15, ram=10, bnd=10, sec=10, alg=0, up=50)
        survivor = Entity("SurvivorUnit", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)

        self.engine.add_entity(victim)
        self.engine.add_entity(killer)
        self.engine.add_entity(survivor)

        victim.apply_status("CORRUPTED", duration=3, metadata={"stat": "cpu", "drained_amount": 5})
        victim.apply_status("ENCRYPTED", duration=2)
        self.assertEqual(victim.cpu, 5)

        # Killer executes fatal attack on Victim (raw dmg = 75 + 10 - 10 = 75 > 10 HP)
        res = self.engine._execute_attack(killer, "VictimUnit", mode="kinetic")
        self.assertIn("DISCONNECTED", res)
        self.assertFalse(victim.is_alive)
        self.assertEqual(len(victim.status_effects), 0)
        self.assertEqual(victim.cpu, 10)

        # Now resolve turn with remaining 2 combatants alive
        self.engine.queue_command("KillerUnit", "defend")
        self.engine.queue_command("SurvivorUnit", "defend")
        await self.engine.resolve_turn()

        # Dead victim emitted no post-mortem status expiration events
        expire_events = [e for e in self.engine.last_turn_events if e.get("type") == "status_expire" and e.get("target") == "VictimUnit"]
        self.assertEqual(len(expire_events), 0)

    async def test_spectator_drop_interaction_with_corrupted_unit(self):
        """Spectator drop during active combat heals a CORRUPTED entity, preventing fatal corruption tick."""
        fighter = Entity("FragileGladiator", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=2, max_hp=100, up=50)
        enemy = Entity("RivalAI", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100, up=50)
        self.engine.add_entity(fighter)
        self.engine.add_entity(enemy)
        self.engine.active = True

        fighter.apply_status("CORRUPTED", duration=2, metadata={"stat": "ram", "drained_amount": 2, "tick_damage": 5})
        self.assertEqual(fighter.hp, 2)

        from ai_grid.core.handlers.spectator import handle_spectator_drop
        mock_node = MagicMock()
        mock_node.active_engine = self.engine
        mock_node.net_name = "testnet"
        mock_node.config = {"channel": "#arena"}
        mock_node.send = AsyncMock()
        mock_node.db = MagicMock()
        mock_node.db.spectator_drop = AsyncMock(return_value=(True, "Drop deployed"))

        await handle_spectator_drop(mock_node, "GenerousWatcher", ["FragileGladiator", "Nano_Patch"], "#arena")

        # HP raised to 52
        self.assertEqual(fighter.hp, 52)
        drop_events = [e for e in self.engine.turn_events if e.get("type") == "spectator_drop"]
        self.assertEqual(len(drop_events), 1)

        # Now resolve turn: corruption pulse deals 5 DMG -> HP drops from 52 to 47, fighter survives!
        self.engine.queue_command("FragileGladiator", "defend")
        self.engine.queue_command("RivalAI", "defend")
        await self.engine.resolve_turn()

        self.assertTrue(fighter.is_alive)
        self.assertEqual(fighter.hp, 47)

    async def test_status_effect_mapping_protocol_and_dict_conversion(self):
        """StatusEffect supports keys(), items(), values(), iteration, and dict(effect) conversion."""
        eff = StatusEffect("THROTTLED", duration=1, source="HackerUnit", metadata={"drained_bnd": 5, "stat": "bnd"})
        self.assertIn("name", eff.keys())
        self.assertIn("duration", eff.keys())
        self.assertIn("source", eff.keys())
        self.assertIn("drained_bnd", eff.keys())
        self.assertIn("stat", eff.keys())

        # Test dictionary conversion
        d = dict(eff)
        self.assertEqual(d["name"], "THROTTLED")
        self.assertEqual(d["duration"], 1)
        self.assertEqual(d["source"], "HackerUnit")
        self.assertEqual(d["drained_bnd"], 5)
        self.assertEqual(d["stat"], "bnd")

        # Test iteration
        keys_iter = list(eff)
        self.assertIn("name", keys_iter)
        self.assertIn("drained_bnd", keys_iter)

        # Test items and values
        items_dict = dict(eff.items())
        self.assertEqual(items_dict["drained_bnd"], 5)
        self.assertIn("THROTTLED", list(eff.values()))

    async def test_corrupted_uppercase_stat_name_and_invalid_fallback(self):
        """CORRUPTED normalizes uppercase stat names (e.g. 'CPU') to target entity.cpu and safely falls back on invalid stats."""
        unit = Entity("UppercaseTarget", cpu=10, ram=10, bnd=10, sec=10, alg=0)
        # Apply with uppercase 'CPU'
        eff = unit.apply_status("CORRUPTED", duration=2, metadata={"stat": "CPU", "drained_amount": 3})
        self.assertEqual(eff["stat"], "cpu")
        self.assertEqual(unit.cpu, 7) # Correctly drained cpu

        unit.remove_status("CORRUPTED")
        self.assertEqual(unit.cpu, 10) # Correctly restored cpu

        # Apply with invalid stat name
        eff_invalid = unit.apply_status("CORRUPTED", duration=2, metadata={"stat": "nonexistent_stat"})
        self.assertIn(eff_invalid["stat"], ["cpu", "ram", "bnd", "sec"])
        unit.clear_statuses()

    async def test_throttled_sets_stat_bnd_and_expiration_reports_restored_stat(self):
        """THROTTLED metadata contains stat='bnd' and drained_amount, and expiration event reports restored_stat='bnd'."""
        victim = Entity("ThrottleTarget", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100)
        enemy = Entity("PassiveEnemy", cpu=10, ram=10, bnd=10, sec=10, alg=0, hp=100, max_hp=100)
        self.engine.add_entity(victim)
        self.engine.add_entity(enemy)

        eff = victim.apply_status("THROTTLED", duration=1)
        self.assertEqual(eff.metadata.get("stat"), "bnd")
        self.assertEqual(eff.metadata.get("drained_amount"), 5)

        self.engine.queue_command("ThrottleTarget", "defend")
        self.engine.queue_command("PassiveEnemy", "defend")
        await self.engine.resolve_turn()

        self.assertFalse(victim.has_status("THROTTLED"))
        self.assertEqual(victim.bnd, 10)

        expire_events = [e for e in self.engine.last_turn_events if e.get("type") == "status_expire" and e.get("status") == "THROTTLED"]
        self.assertEqual(len(expire_events), 1)
        self.assertEqual(expire_events[0]["restored_stat"], "bnd")
        self.assertEqual(expire_events[0]["restored_amount"], 5)


if __name__ == '__main__':
    unittest.main()
