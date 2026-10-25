import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sc_hammer import actions as a  # noqa: E402
from sc_hammer import protocol as p  # noqa: E402
from sc_hammer import updates  # noqa: E402
from sc_hammer.describe import Names  # noqa: E402
from sc_hammer.fake import FakeController, FakeTransport, demo_world  # noqa: E402
from sc_hammer.hidraw import first_usage_page  # noqa: E402
from sc_hammer.model import EMPTY, USED, Scanner  # noqa: E402
from sc_hammer.store import Store  # noqa: E402

R, C = a.RECEIVER, a.CONTROLLER
LIVING, OFFICE = "FXA941021C8B7", "OPK-7F3A21C9"
WIRELESS, USB, USB_EMPTY = "FXB99602A1B2C", "FXB99602G7H8J", "FXB99602K2L3M"


class Base(unittest.TestCase):
    def setUp(self):
        self.world = demo_world()
        self.scanner = Scanner(FakeTransport(self.world))
        self.snap = self.scanner.scan()

    def run_op(self, op, src, dst=None):
        pl = a.plan(self.snap, op, src, dst)
        a.execute(self.snap, pl)
        self.snap = self.scanner.scan()
        return pl

    def rslot(self, serial, i):
        return self.snap.receiver(serial).slots[i]

    def cslot(self, serial, i):
        return self.snap.controller(serial).slots[i]


class ScanTest(Base):
    def test_finds_everything(self):
        self.assertEqual([r.serial for r in self.snap.receivers], [LIVING, OFFICE])
        self.assertEqual(len(self.snap.controllers), 4)
        self.assertEqual(self.snap.receiver(OFFICE).kind, "OpenPuck")
        wireless = [c for c in self.snap.controllers if not c.usb]
        self.assertEqual({c.serial for c in wireless}, {WIRELESS, "FXB99602D4E5F"})
        self.assertEqual(self.snap.controller(WIRELESS).via, (LIVING, 0))
        self.assertTrue(self.rslot(LIVING, 0).connected)
        self.assertFalse(self.rslot(LIVING, 2).connected)
        self.assertEqual(self.rslot(OFFICE, 1).state, EMPTY)
        self.assertEqual(self.snap.controller(USB).active, 0)

    def test_keys_match_across_devices(self):
        hits = self.snap.receivers_with_key(self.cslot(USB, 0).key)
        self.assertEqual([(r.serial, s.index) for r, s in hits], [(OFFICE, 0)])

    def test_permission_denied_is_reported(self):
        self.world.denied.add("fake:%s:0" % LIVING)
        snap = self.scanner.scan()
        self.assertIsNone(snap.receiver(LIVING))
        # Windows has no permissions to fix, so it's reported as "blocked" there.
        self.assertEqual(snap.problems[0].kind, "blocked" if sys.platform == "win32" else "permission")

    def test_echoed_setting_is_not_a_bond(self):
        class Echo:
            def set_feature(self, rid, cmd, payload=b""):
                self.r = bytes([rid, cmd, len(payload)]) + payload

            def get_feature(self, rid, max_len=64):
                return self.r

        self.assertIsNone(p.read_controller_bond(Echo(), 0, attempts=1))

    def test_usage_page(self):
        self.assertEqual(first_usage_page(bytes([0x06, 0x00, 0xFF, 0x09, 0x01])), 0xFF00)
        self.assertEqual(first_usage_page(bytes([0x85, 0x01, 0x05, 0x01])), 0x01)


class WindowsGroupingTest(unittest.TestCase):
    """The collection layout a real Valve puck shows on Windows 11 (from a --diagnose report)."""

    def collections(self):
        out = []
        for mi in (2, 3, 4, 5):  # the four slots: mouse, keyboard, vendor with feature reports
            for col, page, usage, feat in ((1, 0x01, 0x02, 0), (2, 0x01, 0x06, 0),
                                           (3, 0xFF00, 0x01, 64)):
                out.append({"path": "hid#vid_28de&pid_1304&mi_%02d&col%02d" % (mi, col),
                            "pid": 0x1304, "version": 2, "serial": "PUCKSERIAL1",
                            "product": "Steam Controller Puck", "mi": mi, "col": col,
                            "usage_page": page, "usage": usage, "feature_len": feat})
        out.append({"path": "hid#vid_28de&pid_1304&mi_06", "pid": 0x1304, "version": 2,
                    "serial": "PUCKSERIAL1", "product": "Steam Controller Puck", "mi": 6,
                    "col": 0, "usage_page": 0xFF00, "usage": 0x02, "feature_len": 64})
        return out

    def test_slots_are_the_vendor_collections(self):
        from sc_hammer.transport import group_collections
        devices, report_len = group_collections(self.collections())
        self.assertEqual(len(devices), 1)
        slots = devices[0].command_ifaces()
        self.assertEqual([i.number for i in slots], [2, 3, 4, 5])
        self.assertTrue(all(i.node.endswith("col03") for i in slots))
        self.assertEqual({report_len[i.node] for i in slots}, {64})


class ReceiverActionTest(Base):
    def test_move_within_receiver(self):
        rec = self.rslot(LIVING, 2).record
        self.run_op(a.MOVE, a.SlotRef(R, LIVING, 2), a.SlotRef(R, LIVING, 3))
        self.assertEqual(self.rslot(LIVING, 3).record, rec)
        self.assertEqual(self.rslot(LIVING, 2).state, EMPTY)

    def test_move_onto_used_slot_is_refused(self):
        with self.assertRaises(a.NotAllowed):
            a.plan(self.snap, a.MOVE, a.SlotRef(R, LIVING, 0), a.SlotRef(R, LIVING, 1))

    def test_swap_within_receiver(self):
        k0, k1 = self.rslot(LIVING, 0).key, self.rslot(LIVING, 1).key
        self.run_op(a.SWAP, a.SlotRef(R, LIVING, 0), a.SlotRef(R, LIVING, 1))
        self.assertEqual((self.rslot(LIVING, 0).key, self.rslot(LIVING, 1).key), (k1, k0))

    def test_no_moves_or_swaps_between_receivers(self):
        for op in (a.MOVE, a.SWAP):
            with self.assertRaises(a.NotAllowed):
                a.plan(self.snap, op, a.SlotRef(R, LIVING, 0), a.SlotRef(R, OFFICE, 0))
        with self.assertRaises(a.NotAllowed):
            a.plan(self.snap, a.MOVE, a.SlotRef(R, LIVING, 0), a.SlotRef(R, OFFICE, 3))

    def test_erase_disconnects(self):
        pl = self.run_op(a.ERASE, a.SlotRef(R, LIVING, 1))
        self.assertTrue(pl.warnings)
        self.assertEqual(self.rslot(LIVING, 1).state, EMPTY)
        self.assertIsNone(self.snap.controller("FXB99602D4E5F"))

    def test_erase_clears_usb_controller_side_too(self):
        self.run_op(a.ERASE, a.SlotRef(R, OFFICE, 0))
        self.assertEqual(self.rslot(OFFICE, 0).state, EMPTY)
        self.assertEqual(self.cslot(USB, 0).state, EMPTY)

    def test_options(self):
        src = a.SlotRef(R, LIVING, 0)
        self.assertEqual(a.options(self.snap, src, a.SlotRef(R, LIVING, 3)), [a.MOVE])
        self.assertEqual(a.options(self.snap, src, a.SlotRef(R, LIVING, 1)), [a.SWAP])
        # Its controller is wireless, so it can't be re-paired to another receiver.
        self.assertEqual(a.options(self.snap, src, a.SlotRef(R, OFFICE, 3)), [])
        self.assertEqual(a.options(self.snap, src, a.SlotRef(C, USB_EMPTY, 1)), [a.PAIR])
        self.assertEqual(a.options(self.snap, src, a.SlotRef(C, WIRELESS, 1)), [])
        office = a.SlotRef(R, OFFICE, 0)  # held by the USB controller
        self.assertEqual(a.options(self.snap, office, a.SlotRef(R, LIVING, 3)), [a.MOVE])
        self.assertEqual(a.options(self.snap, office, a.SlotRef(R, LIVING, 0)), [])


class ControllerActionTest(Base):
    def test_move_and_swap_controller_slots(self):
        rec = self.cslot(USB, 0).record
        self.run_op(a.MOVE, a.SlotRef(C, USB, 0), a.SlotRef(C, USB, 1))
        self.assertEqual(self.cslot(USB, 1).record, rec)
        self.assertEqual(self.cslot(USB, 0).state, EMPTY)
        self.run_op(a.MOVE, a.SlotRef(C, USB, 1), a.SlotRef(C, USB, 0))
        self.assertEqual(self.cslot(USB, 0).record, rec)

    def test_erase_controller_slot_deletes_the_bond_on_both_sides(self):
        self.run_op(a.ERASE, a.SlotRef(C, USB, 0))
        self.assertEqual(self.cslot(USB, 0).state, EMPTY)
        self.assertIsNone(self.world.controllers[2].bonds[0])
        self.assertEqual(self.rslot(OFFICE, 0).state, EMPTY)

    def test_turn_off_wireless_controller(self):
        a.execute(self.snap, a.plan_turn_off(self.snap, WIRELESS))
        self.snap = self.scanner.scan()
        self.assertIsNone(self.snap.controller(WIRELESS))
        self.assertFalse(self.rslot(LIVING, 0).connected)
        with self.assertRaises(a.NotAllowed):
            a.plan_turn_off(self.snap, USB)

    def test_go_wireless_on_chosen_slot(self):
        self.run_op(a.PAIR, a.SlotRef(C, USB, 1), a.SlotRef(R, LIVING, 3))  # goes wireless on slot 1
        self.world.controllers[2].usb = True  # plug it back in
        self.snap = self.scanner.scan()
        pl = a.plan_go_wireless(self.snap, USB, 1)
        self.assertIsInstance(pl.steps[0], a.SetActiveSlot)
        a.execute(self.snap, pl)
        self.snap = self.scanner.scan()
        self.assertEqual(self.snap.controller(USB).via, (LIVING, 3))
        with self.assertRaises(a.NotAllowed):
            a.plan_go_wireless(self.snap, USB_EMPTY, 0)  # nothing paired

    def test_wireless_controller_is_read_only(self):
        with self.assertRaises(a.NotAllowed):
            a.plan(self.snap, a.ERASE, a.SlotRef(C, WIRELESS, 0))

    def test_no_swaps_or_pairs_between_controllers(self):
        for op in (a.SWAP, a.PAIR):
            with self.assertRaises(a.NotAllowed):
                a.plan(self.snap, op, a.SlotRef(C, USB, 0), a.SlotRef(C, USB_EMPTY, 0))

    def test_move_pairing_to_another_controller(self):
        old = self.cslot(USB, 0).key
        pl = self.run_op(a.MOVE, a.SlotRef(C, USB, 0), a.SlotRef(C, USB_EMPTY, 1))
        self.assertEqual(pl.verb, "Move")
        new = self.rslot(OFFICE, 0).key
        self.assertNotEqual(new, old)
        self.assertEqual(self.rslot(OFFICE, 0).name, USB_EMPTY)
        self.assertEqual(self.cslot(USB, 0).state, EMPTY)
        # It restarted into wireless mode (and uses its other slot), so check the device itself.
        dest = self.world.controllers[3]
        self.assertEqual(p.record_key(dest.bonds[1]), new)
        self.assertEqual(p.record_name(dest.bonds[1]), OFFICE)

    def test_move_to_another_controller_needs_the_receiver(self):
        self.world.receivers.remove(self.world.receivers[1])  # unplug Office
        self.snap = self.scanner.scan()
        with self.assertRaises(a.NotAllowed):
            a.plan(self.snap, a.MOVE, a.SlotRef(C, USB, 0), a.SlotRef(C, USB_EMPTY, 0))

    def test_pair_new_controller(self):
        self.run_op(a.PAIR, a.SlotRef(C, USB_EMPTY, 0), a.SlotRef(R, OFFICE, 1))
        c = self.snap.controller(USB_EMPTY)
        self.assertFalse(c.usb)
        self.assertEqual(c.via, (OFFICE, 1))
        self.assertEqual(self.rslot(OFFICE, 1).name, USB_EMPTY)
        self.assertEqual(c.slots[0].name, OFFICE)

    def test_move_to_another_receiver_repairs_cleanly(self):
        old = self.cslot(USB, 0).key
        pl = self.run_op(a.MOVE, a.SlotRef(R, OFFICE, 0), a.SlotRef(R, LIVING, 3))
        self.assertEqual(pl.verb, "Move")
        new = self.rslot(LIVING, 3).key
        self.assertNotEqual(new, old)
        self.assertEqual(self.rslot(OFFICE, 0).state, EMPTY)
        c = self.snap.controller(USB)
        self.assertEqual(c.slots[0].key, new)
        self.assertEqual(c.slots[0].name, LIVING)
        self.assertEqual(self.rslot(LIVING, 3).name, USB)

    def test_pair_replacing_a_slot_removes_the_old_pairing_everywhere(self):
        # The USB controller's slot 1 pairs with Living slot 4: its old Office pairing must go.
        pl = self.run_op(a.PAIR, a.SlotRef(C, USB, 0), a.SlotRef(R, LIVING, 3))
        self.assertTrue(any("Removes the old pairing" in line for line in pl.lines))
        self.assertEqual(self.rslot(OFFICE, 0).state, EMPTY)
        keys = [s.key for r in self.snap.receivers for s in r.slots if s.key]
        self.assertEqual(len(keys), len(set(keys)))

    def test_pair_over_a_wireless_controllers_slot_warns(self):
        pl = a.plan(self.snap, a.PAIR, a.SlotRef(C, USB_EMPTY, 0), a.SlotRef(R, LIVING, 1))
        self.assertTrue(any("connected wirelessly" in w for w in pl.warnings))

    def test_one_receiver_per_controller_once(self):
        with self.assertRaises(a.NotAllowed):
            a.plan(self.snap, a.PAIR, a.SlotRef(C, USB, 1), a.SlotRef(R, OFFICE, 2))


class DuplicateTest(unittest.TestCase):
    """Pairings copied with older tools: one key on two receivers."""

    def setUp(self):
        from sc_hammer.fake import FakeController, FakeReceiver, World
        k = bytes.fromhex("8e03d5a6721bc94f")
        self.machine = FakeReceiver("MACHINE", pid=0x1305, slots=[p.make_record(k, "CTRL")] +
                                    [p.EMPTY_RECORD] * 3)
        self.puck = FakeReceiver("PUCK", slots=[p.make_record(k, "CTRL")] + [p.EMPTY_RECORD] * 3)
        self.ctrl = FakeController("CTRL", usb=True, bonds=[p.make_record(k, "PUCK"), None])
        self.scanner = Scanner(FakeTransport(World([self.machine, self.puck], [self.ctrl])))
        self.snap = self.scanner.scan()

    def test_erasing_one_copy_keeps_the_other_pairing(self):
        pl = a.plan(self.snap, a.ERASE, a.SlotRef(R, "MACHINE", 0))
        a.execute(self.snap, pl)
        snap = self.scanner.scan()
        self.assertEqual(snap.receiver("MACHINE").slots[0].state, EMPTY)
        self.assertEqual(snap.receiver("PUCK").slots[0].state, USED)
        self.assertEqual(snap.controller("CTRL").slots[0].state, USED)

    def test_erasing_the_controller_side_clears_every_copy(self):
        a.execute(self.snap, a.plan(self.snap, a.ERASE, a.SlotRef(C, "CTRL", 0)))
        snap = self.scanner.scan()
        self.assertEqual(snap.receiver("MACHINE").slots[0].state, EMPTY)
        self.assertEqual(snap.receiver("PUCK").slots[0].state, EMPTY)


class ReversePuckTest(Base):
    """A ReversePuck (Steam Deck dongle) next to the demo's real controllers."""
    RP = "FXA99602ABCDE"

    def setUp(self):
        self.world = demo_world()
        self.rp = FakeController(self.RP, usb=True, reversepuck=True, bonds=[p.EMPTY_RECORD] * 8)
        self.world.controllers.append(self.rp)
        self.scanner = Scanner(FakeTransport(self.world))
        self.snap = self.scanner.scan()

    def test_recognised_with_all_slots(self):
        rp = self.snap.controller(self.RP)
        self.assertTrue(rp.reversepuck)
        self.assertEqual(len(rp.slots), 8)
        self.assertTrue(all(s.state == EMPTY for s in rp.slots))
        self.assertIsNone(rp.active)
        self.assertEqual(Names(Store(tempfile.mkdtemp()), self.snap).controller(self.RP),
                         "ReversePuck ABCDE")
        self.assertEqual(a.sn(rp, 5), "slot 6")
        # the real controllers next to it are unchanged
        usb = self.snap.controller(USB)
        self.assertFalse(usb.reversepuck)
        self.assertEqual(len(usb.slots), 2)
        self.assertEqual(usb.active, 0)
        self.assertEqual(a.sn(usb, 1), "left slot")

    def test_pair_move_swap_erase(self):
        pl = self.run_op(a.PAIR, a.SlotRef(C, self.RP, 5), a.SlotRef(R, LIVING, 3))
        self.assertFalse(any(isinstance(s, a.RebootController) for s in pl.steps))
        self.assertTrue(self.snap.controller(self.RP).usb)
        key = self.rslot(LIVING, 3).key
        self.assertEqual(self.cslot(self.RP, 5).key, key)
        self.assertEqual(p.record_key(self.rp.bonds[5]), key)

        self.run_op(a.MOVE, a.SlotRef(C, self.RP, 5), a.SlotRef(C, self.RP, 7))
        self.assertEqual(self.cslot(self.RP, 7).key, key)
        self.assertEqual(self.cslot(self.RP, 5).state, EMPTY)

        self.run_op(a.PAIR, a.SlotRef(C, self.RP, 0), a.SlotRef(R, OFFICE, 1))
        other = self.cslot(self.RP, 0).key
        pl = self.run_op(a.SWAP, a.SlotRef(C, self.RP, 0), a.SlotRef(C, self.RP, 7))
        self.assertEqual(pl.title, "Swap slots 1 and 8 of Controller FXA99602ABCDE?")
        self.assertEqual((self.cslot(self.RP, 0).key, self.cslot(self.RP, 7).key), (key, other))

        self.run_op(a.ERASE, a.SlotRef(C, self.RP, 0))
        self.assertEqual(self.cslot(self.RP, 0).state, EMPTY)
        self.assertIsNone(self.rp.bonds[0])
        self.assertEqual(self.rslot(LIVING, 3).state, EMPTY)

    def test_no_restart_or_wireless(self):
        for fn in (lambda: a.plan_restart(self.snap, self.RP),
                   lambda: a.plan_go_wireless(self.snap, self.RP, 0)):
            with self.assertRaises(a.NotAllowed):
                fn()

    def test_old_firmware_is_a_plain_controller(self):
        # ReversePuck firmware without rp/info echoes unknown settings back: read like before
        self.rp.reversepuck = False
        self.rp.bonds = [None, None]
        snap = self.scanner.scan()
        self.assertFalse(snap.controller(self.RP).reversepuck)
        self.assertEqual(len(snap.controller(self.RP).slots), 2)


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        updates._claimed = False
        self.saved = os.environ.pop("APPIMAGE", None)

    def tearDown(self):
        updates._claimed = False
        os.environ.pop("APPIMAGE", None)
        if self.saved is not None:
            os.environ["APPIMAGE"] = self.saved

    def test_versions(self):
        self.assertEqual(updates.parse_version("v0.1.12"), (0, 1, 12))
        self.assertIsNone(updates.parse_version("0.1.0-preview.2f7e455"))

    def test_only_release_builds_check_once(self):
        self.assertFalse(updates.claim_check("0.1.1"))  # from source
        os.environ["APPIMAGE"] = "/tmp/SC-Hammer-x86_64.AppImage"
        self.assertFalse(updates.claim_check("0.1.0-preview.2f7e455"))
        self.assertTrue(updates.claim_check("0.1.1"))
        self.assertFalse(updates.claim_check("0.1.1"))  # once per run

    def test_newer_release(self):
        import io
        import json
        from unittest import mock

        def latest(tag):
            body = io.BytesIO(json.dumps({"tag_name": tag}).encode())
            return mock.patch("urllib.request.urlopen", return_value=mock.MagicMock(
                __enter__=lambda s: body, __exit__=lambda s, *e: False))
        with latest("v0.2.0"):
            self.assertEqual(updates.newer_release("0.1.1"), "0.2.0")
        for tag in ("v0.1.1", "v0.1.0"):
            with latest(tag):
                self.assertIsNone(updates.newer_release("0.1.1"))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertIsNone(updates.newer_release("0.1.1"))


class BackupTest(Base):
    def test_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "c"), os.path.join(d, "d"))
            store.save_backup(self.snap, "test")
            before = [s.record for r in self.snap.receivers for s in r.slots]
            self.run_op(a.ERASE, a.SlotRef(R, LIVING, 2))
            self.run_op(a.MOVE, a.SlotRef(C, USB, 0), a.SlotRef(C, USB, 1))
            backup = store.backups()[0]
            self.run_op_plan(a.plan_restore(self.snap, backup))
            self.assertEqual([s.record for r in self.snap.receivers for s in r.slots], before)
            self.assertEqual(self.cslot(USB, 1).state, EMPTY)
            with self.assertRaises(a.NotAllowed):
                a.plan_restore(self.snap, backup)

    def run_op_plan(self, pl):
        a.execute(self.snap, pl)
        self.snap = self.scanner.scan()

    def test_old_settings_folder_is_carried_over(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "openpuck-configurator")
            os.makedirs(old)
            with open(os.path.join(old, "names.json"), "w") as f:
                f.write('{"%s": "Living room"}' % LIVING)
            saved = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                self.assertEqual(Store(data_dir=d).name(LIVING), "Living room")
            finally:
                if saved is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = saved
            self.assertTrue(os.path.isdir(os.path.join(d, "sc-hammer")))
            self.assertFalse(os.path.exists(old))

    def test_names(self):
        with tempfile.TemporaryDirectory() as d:
            Store(d, d).set_name(LIVING, "Living room")
            self.assertEqual(Store(d, d).name(LIVING), "Living room")


if __name__ == "__main__":
    unittest.main()
