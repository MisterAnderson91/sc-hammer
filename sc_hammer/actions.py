"""Turns "move/swap/erase/pair this slot" into a checked list of device writes.

A pairing is one shared 8-byte key, stored in a receiver slot (with the controller's serial as a
label) and in a controller slot (with the receiver's serial as a label). Every pairing lives in
exactly one receiver slot and one controller slot, so both labels always tell the truth:

- Move and Swap within one device just rearrange slots; the key and labels don't change.
- Anything between devices makes a fresh key and removes the old pairing from wherever else it
  was: Pair, and Move to another receiver or controller (which re-pairs with the other side).
- Erase removes the pairing from both sides.

Controller slots can only be written over USB."""
from __future__ import annotations

import errno
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set, Tuple, Union

from . import protocol as p
from .model import (EMPTY, UNKNOWN, USED, Controller, ControllerSlot, Receiver, ReceiverSlot,
                    Snapshot, slot_name)

RECEIVER, CONTROLLER = "receiver", "controller"
MOVE, SWAP, ERASE, PAIR = "move", "swap", "erase", "pair"

OP_LABELS = {
    MOVE: "Move here",
    SWAP: "Swap with this slot",
    PAIR: "Pair here (new pairing)",
}


@dataclass(frozen=True)
class SlotRef:
    kind: str
    serial: str
    index: int


@dataclass
class WriteSlot:
    ref: SlotRef
    record: bytes


@dataclass
class RebootController:
    serial: str
    to_wireless: bool


@dataclass
class TurnOffController:
    serial: str


@dataclass
class SetActiveSlot:
    serial: str
    index: int


Step = Union[WriteSlot, RebootController, TurnOffController, SetActiveSlot]


@dataclass
class Plan:
    verb: str
    title: str
    lines: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    after: str = ""
    backup: bool = True  # False for plans that don't change any slot


class NotAllowed(Exception):
    pass


class ExecError(Exception):
    pass


class Namer:
    """Display names; the GUI swaps in one that knows the user's nicknames."""

    def receiver(self, serial: str) -> str:
        return "Receiver %s" % serial

    def controller(self, serial: str) -> str:
        return "Controller %s" % serial


def _find(snap: Snapshot, ref: SlotRef):
    if ref.kind == RECEIVER:
        dev = snap.receiver(ref.serial)
    else:
        dev = snap.controller(ref.serial)
    if dev is None or ref.index >= len(dev.slots):
        raise NotAllowed("That slot isn't there any more. Click Refresh and try again.")
    slot = dev.slots[ref.index]
    if slot.state == UNKNOWN:
        raise NotAllowed("The %s couldn't be read. Click Refresh and try again." %
                         slot_name(ref.kind == CONTROLLER, ref.index))
    return dev, slot


def _writable(dev) -> None:
    if isinstance(dev, Controller) and not dev.usb:
        raise NotAllowed("This controller is connected wirelessly. Plug it in with a USB cable "
                         "to change its slots.")


class _Ctx:
    def __init__(self, snap: Snapshot, namer: Namer):
        self.snap = snap
        self.n = namer

    def where(self, dev, slot) -> str:
        if isinstance(dev, Receiver):
            return "%s, slot %d" % (self.n.receiver(dev.serial), slot.index + 1)
        return "%s, %s" % (self.n.controller(dev.serial), sn(dev, slot))

    def holder(self, dev, slot) -> str:
        """What a slot holds, in words."""
        if isinstance(dev, Receiver):
            return self.n.controller(slot.name)
        hits = self.snap.receivers_with_key(slot.key)
        if hits:
            return self.n.receiver(hits[0][0].serial)
        return self.n.receiver(slot.name)

    def connected_warning(self, dev, slot) -> Optional[str]:
        if isinstance(dev, Receiver) and slot.connected:
            who = slot.connected_serial or slot.name
            return "%s is connected through %s right now and will disconnect." % (
                self.n.controller(who), self.where(dev, slot))
        return None

    def active_note(self, c: Controller) -> str:
        return "Changes to a controller take effect after it restarts. Use \"Restart\" on the " \
               "controller, or unplug it and turn it off and on again."


def sn(dev, slot) -> str:
    """The slot's name on its device: "slot 3", or "right slot" / "left slot" on a controller."""
    return slot_name(isinstance(dev, Controller), slot if isinstance(slot, int) else slot.index)


def _ref(dev, slot) -> SlotRef:
    return SlotRef(RECEIVER if isinstance(dev, Receiver) else CONTROLLER, dev.serial, slot.index)


def options(snap: Snapshot, src: SlotRef, dst: SlotRef) -> List[str]:
    """Operations that make sense when src is dropped on dst, in menu order."""
    ops = []
    for op in (MOVE, SWAP, PAIR):
        try:
            plan(snap, op, src, dst)
            ops.append(op)
        except NotAllowed:
            pass
    return ops


def plan(snap: Snapshot, op: str, src: SlotRef, dst: Optional[SlotRef] = None,
         namer: Optional[Namer] = None) -> Plan:
    ctx = _Ctx(snap, namer or Namer())
    sdev, sslot = _find(snap, src)
    if op == ERASE:
        return _erase(ctx, sdev, sslot)
    if dst is None:
        raise NotAllowed("Pick a destination slot.")
    if dst == src:
        raise NotAllowed("That's the same slot.")
    ddev, dslot = _find(snap, dst)
    if op == PAIR:
        return _pair(ctx, sdev, sslot, ddev, dslot)
    if op == MOVE:
        return _move(ctx, sdev, sslot, ddev, dslot)
    if op == SWAP:
        return _swap(ctx, sdev, sslot, ddev, dslot)
    raise NotAllowed("Unknown action.")


def _clear_key(ctx: _Ctx, pl: Plan, key: bytes, skip: Set[SlotRef],
               receivers: bool = True) -> None:
    """Erase every other copy of a pairing key that can be reached, so it's gone from both sides."""
    if not key:
        return
    for r, rs in ctx.snap.receivers_with_key(key) if receivers else []:
        ref = _ref(r, rs)
        if ref in skip:
            continue
        skip.add(ref)
        pl.steps.append(WriteSlot(ref, p.EMPTY_RECORD))
        pl.lines.append("Removes the old pairing from %s." % ctx.where(r, rs))
        w = ctx.connected_warning(r, rs)
        if w:
            pl.warnings.append(w)
    for c, cs in ctx.snap.controllers_with_key(key):
        ref = _ref(c, cs)
        if ref in skip:
            continue
        skip.add(ref)
        if c.usb:
            pl.steps.append(WriteSlot(ref, p.EMPTY_RECORD))
            pl.lines.append("Removes the old pairing from %s." % ctx.where(c, cs))
            pl.after = pl.after or ctx.active_note(c)
        else:
            pl.warnings.append("%s is connected wirelessly, so its %s can't be cleared right "
                               "now. That slot won't connect any more; plug the controller in "
                               "with USB and erase it to tidy up." %
                               (ctx.n.controller(c.serial), sn(c, cs)))


def _other_receivers(ctx: _Ctx, dev, slot) -> list:
    """Other receiver slots holding the same key (duplicates made by copying)."""
    return [(r, rs) for r, rs in ctx.snap.receivers_with_key(slot.key) if rs is not slot]


def _release_receiver_slot(ctx: _Ctx, pl: Plan, dev, slot, skip: Set[SlotRef]) -> None:
    """A receiver slot's pairing is going away. Clear the controller side too, unless another
    receiver still holds the same pairing (then the controller still uses it there)."""
    if slot.state != USED:
        return
    others = _other_receivers(ctx, dev, slot)
    if others:
        pl.lines.append("%s still works with %s." % (ctx.holder(dev, slot),
                                                     ctx.where(*others[0])))
        return
    _clear_key(ctx, pl, slot.key, skip, receivers=False)
    _left_behind(ctx, pl, dev, slot)


def _left_behind(ctx: _Ctx, pl: Plan, dev, slot) -> None:
    """Note a pairing whose other side isn't plugged in, so it can't be cleared now."""
    if slot.state != USED:
        return
    if isinstance(dev, Receiver):
        if not ctx.snap.controllers_with_key(slot.key):
            pl.lines.append("%s isn't connected, so it still lists this receiver. That entry won't "
                            "work any more; erase it next time it's plugged in." %
                            ctx.n.controller(slot.name))
    elif not ctx.snap.receivers_with_key(slot.key):
        pl.lines.append("%s isn't plugged in, so it still lists this controller. That entry won't "
                        "work any more; erase it next time it's plugged in." %
                        ctx.n.receiver(slot.name))


def _erase(ctx: _Ctx, dev, slot) -> Plan:
    _writable(dev)
    if slot.state != USED:
        raise NotAllowed("This slot is already empty.")
    pl = Plan("Erase", "Erase %s?" % ctx.where(dev, slot))
    ref = _ref(dev, slot)
    pl.steps.append(WriteSlot(ref, p.EMPTY_RECORD))
    if isinstance(dev, Receiver):
        pl.lines.append("%s won't be able to connect through this slot any more." %
                        ctx.holder(dev, slot))
        w = ctx.connected_warning(dev, slot)
        if w:
            pl.warnings.append(w)
        _release_receiver_slot(ctx, pl, dev, slot, {ref})
    else:
        pl.lines.append("The controller forgets %s." % ctx.holder(dev, slot))
        if slot.active:
            pl.warnings.append("This is the slot the controller is using right now.")
        pl.after = ctx.active_note(dev)
        _clear_key(ctx, pl, slot.key, {ref})
        _left_behind(ctx, pl, dev, slot)
    pl.lines.append("To use them together again, pair them again.")
    return pl


def _same_device(sdev, ddev) -> None:
    if isinstance(sdev, Receiver) != isinstance(ddev, Receiver):
        raise NotAllowed("A controller slot and a receiver slot are connected with Pair, which "
                         "makes a fresh pairing.")
    if sdev.serial != ddev.serial:
        raise NotAllowed("Swapping only works between slots of the same receiver or controller. "
                         "Use Move to put a pairing on another device.")
    _writable(sdev)


def _move(ctx: _Ctx, sdev, sslot, ddev, dslot) -> Plan:
    if type(sdev) is type(ddev) and sdev.serial != ddev.serial:
        return _move_across(ctx, sdev, sslot, ddev, dslot)
    _same_device(sdev, ddev)
    if sslot.state != USED:
        raise NotAllowed("There's nothing in this slot to move.")
    if dslot.state != EMPTY:
        raise NotAllowed("The destination slot isn't empty. Use Swap instead.")
    pl = Plan("Move", "Move %s to the %s?" % (ctx.where(sdev, sslot), sn(ddev, dslot))
              if isinstance(sdev, Controller) else
              "Move %s to slot %d?" % (ctx.where(sdev, sslot), dslot.index + 1))
    pl.steps.append(WriteSlot(_ref(ddev, dslot), sslot.record))
    pl.steps.append(WriteSlot(_ref(sdev, sslot), p.EMPTY_RECORD))
    if isinstance(sdev, Receiver):
        pl.lines.append("%s moves to slot %d. It doesn't need to be paired again." %
                        (ctx.holder(sdev, sslot), dslot.index + 1))
        w = ctx.connected_warning(sdev, sslot)
        if w:
            pl.warnings.append(w + " It reconnects on its own within a few seconds.")
    else:
        pl.lines.append("The pairing with %s moves to the %s." %
                        (ctx.holder(sdev, sslot), sn(ddev, dslot)))
        if sslot.active:
            pl.warnings.append("The controller is using its %s right now. After the move that "
                               "slot is empty, so the controller won't connect until you switch "
                               "it to the %s (Go wireless, or turn it on with %s)." %
                               (sn(sdev, sslot), sn(ddev, dslot), combo(dslot)))
        pl.after = ctx.active_note(sdev)
    return pl


def _swap(ctx: _Ctx, sdev, sslot, ddev, dslot) -> Plan:
    _same_device(sdev, ddev)
    if sslot.state != USED or dslot.state != USED:
        raise NotAllowed("Swap needs two slots that both hold a pairing. Use Move for an empty slot.")
    if isinstance(sdev, Receiver):
        title = "Swap slots %d and %d of %s?" % (sslot.index + 1, dslot.index + 1,
                                                  ctx.n.receiver(sdev.serial))
    else:
        title = "Swap the right and left slots of %s?" % ctx.n.controller(sdev.serial)
    pl = Plan("Swap", title)
    pl.steps.append(WriteSlot(_ref(ddev, dslot), sslot.record))
    pl.steps.append(WriteSlot(_ref(sdev, sslot), dslot.record))
    pl.lines.append("%s goes to the %s." % (ctx.holder(sdev, sslot), sn(ddev, dslot)))
    pl.lines.append("%s goes to the %s." % (ctx.holder(ddev, dslot), sn(sdev, sslot)))
    if isinstance(sdev, Receiver):
        pl.lines.append("Nothing needs to be paired again.")
        for dev, slot in ((sdev, sslot), (ddev, dslot)):
            w = ctx.connected_warning(dev, slot)
            if w:
                pl.warnings.append(w + " It reconnects on its own within a few seconds.")
    else:
        if sslot.active or dslot.active:
            active = sslot if sslot.active else dslot
            other = dslot if active is sslot else sslot
            pl.warnings.append("The controller keeps using its %s, which will then hold %s." %
                               (sn(sdev, active), ctx.holder(sdev, other)))
        pl.after = ctx.active_note(sdev)
    return pl


def _move_across(ctx: _Ctx, sdev, sslot, ddev, dslot) -> Plan:
    """Move a pairing to another receiver or controller. A key can't be shared, so this pairs the
    other side of the pairing afresh with the destination; the slot moved away from is cleared."""
    if sslot.state != USED:
        raise NotAllowed("There's nothing in this slot to move.")
    if dslot.state != EMPTY:
        raise NotAllowed("The destination slot isn't empty. Erase it first, or pick an empty slot.")
    if isinstance(sdev, Receiver):
        hits = ctx.snap.controllers_with_key(sslot.key)
        usb = [(c, cs) for c, cs in hits if c.usb]
        if not usb:
            raise NotAllowed("Moving %s to another receiver pairs it again, so plug it in with a "
                             "USB cable first." % ctx.holder(sdev, sslot))
        cdev, cslot = usb[0]
        pl = _pair(ctx, ddev, dslot, cdev, cslot)
        pl.title = "Move %s from %s to %s?" % (ctx.holder(sdev, sslot), ctx.where(sdev, sslot),
                                              ctx.where(ddev, dslot))
        pl.lines[0] = ("%s gets a fresh pairing with %s, and %s is cleared." %
                       (ctx.n.controller(cdev.serial), ctx.where(ddev, dslot),
                        ctx.where(sdev, sslot)))
    else:
        hits = ctx.snap.receivers_with_key(sslot.key)
        if not hits:
            raise NotAllowed("Moving this pairing to another controller pairs it again with %s, so "
                             "plug that receiver in first." % ctx.holder(sdev, sslot))
        if not ddev.usb:
            raise NotAllowed("Plug %s in with a USB cable to move a pairing onto it." %
                             ctx.n.controller(ddev.serial))
        rdev, rslot = hits[0]
        pl = _pair(ctx, rdev, rslot, ddev, dslot)
        pl.title = "Move the pairing with %s from %s to %s?" % (
            ctx.n.receiver(rdev.serial), ctx.where(sdev, sslot), ctx.where(ddev, dslot))
        pl.lines[0] = ("%s gets a fresh pairing with %s, and %s is cleared." %
                       (ctx.where(rdev, rslot), ctx.n.controller(ddev.serial),
                        ctx.where(sdev, sslot)))
    # The summary line already says the old slot is cleared and its pairing replaced.
    moved_from = "Removes the old pairing from %s." % ctx.where(sdev, sslot)
    pl.lines = [pl.lines[0]] + [line for line in pl.lines[1:]
                                if line != moved_from and not line.endswith("That pairing is replaced.")]
    pl.verb = "Move"
    return pl


def _pair(ctx: _Ctx, sdev, sslot, ddev, dslot) -> Plan:
    if isinstance(sdev, Receiver) == isinstance(ddev, Receiver):
        raise NotAllowed("Pair connects a receiver slot with a controller slot. To move a pairing "
                         "to another device, use Move.")
    rdev, rslot, cdev, cslot = ((sdev, sslot, ddev, dslot) if isinstance(sdev, Receiver)
                                else (ddev, dslot, sdev, sslot))
    if not cdev.usb:
        raise NotAllowed("Plug the controller in with a USB cable to pair it.")
    for other in cdev.slots:
        if other is not cslot and other.state == USED and \
                any(s.key == other.key for s in rdev.slots if s is not rslot):
            raise NotAllowed("%s is already paired with %s through its %s." %
                             (ctx.n.controller(cdev.serial), ctx.n.receiver(rdev.serial),
                              sn(cdev, other)))
    key = p.new_key()
    pl = Plan("Pair", "Pair %s with %s?" % (ctx.where(cdev, cslot), ctx.where(rdev, rslot)))
    rref, cref = _ref(rdev, rslot), _ref(cdev, cslot)
    pl.steps.append(WriteSlot(rref, p.make_record(key, cdev.serial)))
    pl.steps.append(WriteSlot(cref, p.make_record(key, rdev.serial)))
    pl.lines.append("This makes a brand new pairing between them.")
    for dev, slot in ((rdev, rslot), (cdev, cslot)):
        if slot.state == USED:
            pl.lines.append("%s held %s. That pairing is replaced." %
                            (ctx.where(dev, slot), ctx.holder(dev, slot)))
    w = ctx.connected_warning(rdev, rslot)
    if w:
        pl.warnings.append(w)
    skip = {rref, cref}
    # The controller slot's old pairing is gone from the controller, so every receiver copy goes.
    _clear_key(ctx, pl, cslot.key, skip)
    _left_behind(ctx, pl, cdev, cslot)
    if rslot.key != cslot.key:
        _release_receiver_slot(ctx, pl, rdev, rslot, skip)
    if cdev.active is not None and cdev.active != cslot.index:
        pl.warnings.append("The controller is using its %s right now, so it won't use this "
                           "pairing until you switch it to the %s (Go wireless, or turn it on "
                           "with %s)." % (sn(cdev, cdev.active), sn(cdev, cslot), combo(cslot)))
    pl.steps.append(RebootController(cdev.serial, True))
    pl.after = "The controller restarts and connects wirelessly. You can unplug its USB cable."
    return pl


def plan_restart(snap: Snapshot, serial: str, namer: Optional[Namer] = None) -> Plan:
    n = namer or Namer()
    c = snap.controller(serial)
    if c is None or not c.usb:
        raise NotAllowed("Only a controller plugged in with USB can be restarted from here.")
    return Plan("Restart", "Restart %s?" % n.controller(serial),
                ["The controller restarts so it picks up changes to its slots."],
                steps=[RebootController(serial, False)], backup=False)


def plan_go_wireless(snap: Snapshot, serial: str, slot: int,
                     namer: Optional[Namer] = None) -> Plan:
    n = namer or Namer()
    c = snap.controller(serial)
    if c is None or not c.usb:
        raise NotAllowed("Only a controller plugged in with USB can be switched to wireless here.")
    if slot >= len(c.slots) or c.slots[slot].state != USED:
        raise NotAllowed("The %s is empty, so the controller would have nothing to connect to."
                         % sn(c, slot))
    ctx = _Ctx(snap, n)
    pl = Plan("Restart wireless", "Restart %s in wireless mode?" % n.controller(serial),
              backup=False)
    if c.active != slot:
        pl.steps.append(SetActiveSlot(serial, slot))
        pl.lines.append("The controller switches to its %s (%s) first." %
                        (sn(c, slot), ctx.holder(c, c.slots[slot])))
    pl.steps.append(RebootController(serial, True))
    pl.lines.append("It then restarts and connects wirelessly using its %s. You can unplug its "
                    "USB cable." % sn(c, slot))
    if not snap.receivers_with_key(c.slots[slot].key):
        pl.warnings.append("%s isn't plugged in here, so the controller will only connect once "
                           "that receiver is on." % ctx.holder(c, c.slots[slot]))
    return pl


def plan_turn_off(snap: Snapshot, serial: str, namer: Optional[Namer] = None) -> Plan:
    n = namer or Namer()
    c = snap.controller(serial)
    if c is None:
        raise NotAllowed("That controller isn't connected any more. Click Refresh.")
    if c.usb:
        raise NotAllowed("This controller is plugged in with USB. Unplug it to turn it off.")
    pl = Plan("Disconnect", "Disconnect %s from wireless?" % n.controller(serial),
              ["The controller drops its wireless connection. Unless it's plugged in with a "
               "USB-C cable, that turns it off; press the Steam button to turn it back on.",
               "Its pairings aren't changed."],
              steps=[TurnOffController(serial)], backup=False)
    return pl


def plan_restore(snap: Snapshot, backup: dict, namer: Optional[Namer] = None) -> Plan:
    """Write back every slot that differs from the backup, on the devices that are present."""
    n = namer or Namer()
    pl = Plan("Restore", "Restore the backup from %s?" % backup.get("created_display", "?"))
    missing = []
    for rb in backup.get("receivers", []):
        r = snap.receiver(rb["serial"])
        if r is None:
            missing.append(n.receiver(rb["serial"]))
            continue
        for i, hexrec in enumerate(rb["slots"]):
            if hexrec is None or i >= len(r.slots) or r.slots[i].state == UNKNOWN:
                continue
            rec = bytes.fromhex(hexrec)
            if rec != r.slots[i].record:
                pl.steps.append(WriteSlot(SlotRef(RECEIVER, r.serial, i), rec))
                pl.lines.append("%s, slot %d" % (n.receiver(r.serial), i + 1))
    for cb in backup.get("controllers", []):
        c = snap.controller(cb["serial"])
        if c is None or not c.usb:
            if any(h and p.record_used(bytes.fromhex(h)) for h in cb["slots"]):
                missing.append(n.controller(cb["serial"]) + (" (needs USB)" if c else ""))
            continue
        for i, hexrec in enumerate(cb["slots"]):
            if hexrec is None or i >= len(c.slots) or c.slots[i].state == UNKNOWN:
                continue
            rec = bytes.fromhex(hexrec)
            if rec != c.slots[i].record:
                pl.steps.append(WriteSlot(SlotRef(CONTROLLER, c.serial, i), rec))
                pl.lines.append("%s, %s" % (n.controller(c.serial), slot_name(True, i)))
                pl.after = "Restart the controller for its changes to take effect."
    if pl.lines:
        pl.lines.insert(0, "These slots will be put back the way they were:")
    if missing:
        pl.warnings.append("Not plugged in, so left as they are: " + ", ".join(missing))
    if not pl.steps:
        raise NotAllowed("Everything that's plugged in already matches this backup.")
    return pl


def _iface_for(snap: Snapshot, ref: SlotRef):
    if ref.kind == RECEIVER:
        r = snap.receiver(ref.serial)
        if r is None or ref.index >= len(r.ifaces):
            raise ExecError("%s isn't plugged in any more." % ref.serial)
        return r.ifaces[ref.index]
    c = snap.controller(ref.serial)
    if c is None or c.iface is None:
        raise ExecError("Controller %s isn't plugged in any more." % ref.serial)
    if not c.usb:
        raise ExecError("Controller %s isn't connected with USB." % ref.serial)
    return c.iface


def _read_back(ref: SlotRef, f) -> Optional[bytes]:
    if ref.kind == RECEIVER:
        return p.read_receiver_slot(f)
    return p.read_controller_bond(f, ref.index)


def execute(snap: Snapshot, pl: Plan, progress: Callable[[str], None] = lambda s: None,
            verify: bool = True) -> None:
    for n, step in enumerate(pl.steps, 1):
        if isinstance(step, TurnOffController):
            progress("Disconnecting the controller…")
            c = snap.controller(step.serial)
            if c is None or c.iface is None:
                raise ExecError("Controller %s isn't connected any more." % step.serial)
            try:
                p.turn_off_controller(c.iface)
            except OSError as e:
                raise ExecError("Disconnecting the controller failed: %s" % e) from e
            continue
        if isinstance(step, SetActiveSlot):
            progress("Switching the controller to its %s…" % slot_name(True, step.index))
            c = snap.controller(step.serial)
            if c is None or c.iface is None or not c.usb:
                raise ExecError("Controller %s isn't plugged in any more." % step.serial)
            try:
                p.write_active_slot(c.iface, step.index)
            except OSError as e:
                raise ExecError("The controller refused to switch to its %s, so it wasn't "
                                "restarted." % slot_name(True, step.index)) from e
            for _ in range(10):
                if p.read_active_slot(c.iface) == step.index:
                    break
                time.sleep(0.1)
            else:
                raise ExecError("The controller didn't keep the switch to its %s, so it wasn't "
                                "restarted." % slot_name(True, step.index))
            continue
        if isinstance(step, RebootController):
            progress("Restarting the controller…")
            c = snap.controller(step.serial)
            if c is None or c.iface is None:
                raise ExecError("Controller %s isn't plugged in any more." % step.serial)
            p.reboot_controller(c.iface, step.to_wireless)
            continue
        progress("Writing step %d of %d…" % (n, len(pl.steps)))
        f = _iface_for(snap, step.ref)
        try:
            if step.ref.kind == RECEIVER:
                p.write_receiver_slot(f, step.record)
            else:
                p.write_controller_bond(f, step.ref.index, step.record)
        except OSError as e:
            if e.errno == errno.EPIPE:
                raise ExecError("The %s refused the change to its %s, so nothing was changed "
                                "there." % (step.ref.kind, _slot_of_ref(step.ref))) from e
            raise ExecError("Writing the %s's %s failed: %s" %
                            (step.ref.kind, _slot_of_ref(step.ref), e)) from e
        if not verify:
            continue
        for _ in range(10):
            got = _read_back(step.ref, f)
            if got is not None and _same(got, step.record):
                break
            time.sleep(0.1)
        else:
            raise ExecError("The %s didn't keep the change to its %s (read back didn't match)." %
                            (step.ref.kind, _slot_of_ref(step.ref)))


def _slot_of_ref(ref: SlotRef) -> str:
    return slot_name(ref.kind == CONTROLLER, ref.index)


def combo(slot) -> str:
    """Button combo that turns a controller on using this slot."""
    from .model import CONTROLLER_SLOT_COMBOS
    i = slot if isinstance(slot, int) else slot.index
    return CONTROLLER_SLOT_COMBOS[i] if i < len(CONTROLLER_SLOT_COMBOS) else ""


def _same(got: bytes, want: bytes) -> bool:
    if not p.record_used(want):
        return not p.record_used(got)
    return got[:p.RECORD_LEN] == want[:p.RECORD_LEN]


def slot_of(snap: Snapshot, ref: SlotRef) -> Tuple[object, object]:
    """(device, slot) or raises NotAllowed; for the GUI."""
    dev = snap.receiver(ref.serial) if ref.kind == RECEIVER else snap.controller(ref.serial)
    if dev is None or ref.index >= len(dev.slots):
        raise NotAllowed("gone")
    return dev, dev.slots[ref.index]
