# Putting an earlier version back while rules for some groups route

A rule for some groups of people that goes abroad («За границу») or directly («Напрямую») needs three
things that earlier versions do not have: the agent that compiles it, the watch service that loads its
chain (`CASCADE-SCOPED`) and makes its sets (`rs_…`, `rg_…`), and the boot gate that makes those sets
before the resolver starts. This page says what happens when one of them is put back, and what to do
first.

Rules for some groups that only block («Блок») are older (they came with the canvas's «Для кого») and
are not what this page is about.

## The agent (and the panel)

**Nothing has to be done first.** The store keeps every such rule *parked*: on disk it is a block rule
that is switched off, for the same groups, with where it really leads and its switch kept beside it
(`held_route`, `held_on`, in the row's `extra`). This version reads it back as it was. An earlier
version reads only what it knows:

* it sends nothing of it abroad or directly, for anybody;
* its blocker blocks nothing of it (the rule is off);
* its `store-init` finds the old documents in step with the list and gathers nothing;
* its saves of «Сайты» and «Блокируется» leave the rule where it is, for the same groups.

`tests/agent_store.py` runs the agent of `ddd7ca8` (the last one before these rules) against a store
written by this version and checks each of these.

What the earlier version shows, and what not to do there:

* The canvas and «Блокируется» show the rule as a switched-off block rule for its groups.
  **Do not switch it on there.** It then becomes what that version made of it: a block rule for those
  groups. Coming back to this version does not undo it; set it to «За границу» or «Напрямую» on the
  canvas again.
* A save of «Блокируется» in the earlier version drops the two kept fields it does not know. The rule
  then comes back as a switched-off block rule for its groups. Set its way on the canvas again.
* A version older than the rules list (one that reads only the documents of the groups of sites and of
  the block groups) sees a switched-off block group. It does not know «Для кого», so switching it on
  there blocks its sites **for everybody**. Do not.

While the earlier agent runs, the people of those groups go by the rules for everybody: the earlier
agent writes neither `scoped.rules` nor `domains-scoped.txt`, and its resolver's file names none of
their sets. A newer watch service still holds the chain the last newer agent loaded; it acts only on the
addresses already learned for those rules, for those groups' people, until the earlier agent's first
restart of the resolver empties those sets.

Coming back: deploy this version as usual. `store-init` gathers what the earlier version changed, and
the first change of the rules or of «Сайты» writes the routing again.

## The watch service and the boot gate

**First switch off every rule for some groups that goes abroad or directly**: on the canvas, «Выключено»,
or «Все» if it should keep acting for everybody. Then check that the resolver's file names none of
their sets:

```
grep -c 'rs_' /root/awg/cascade/domains.ipset        # 0
cat /root/awg/cascade/scoped.rules                    # empty
```

Only then put the watch service, or the gate, back.

Why: AdGuard Home refuses to start when its file of sets (`domains.ipset`) names a set that is not
there. This version makes the `rs_…` sets in the gate, before the resolver starts at boot, and in the
watch service on every apply. An earlier watch service and an earlier gate never make them, and an
earlier watch service still answers the agent's reset of the names as if all went well, so the agent
has no reason to stop naming them. After the next reboot the resolver would not start, and DNS would be
down for **everybody** until a newer watch service runs again.

The boot gate is put back by itself, too: after a boot that is not confirmed, `stack/deadman.sh`
restores `gate.pyz.proven`, the copy of the gate the last confirmed boot ran. Until the first confirmed
boot after the deploy of this version, that copy is the earlier gate. So before the first reboot after
deploying this watch service, either confirm a boot or keep every rule for some groups that routes
switched off.

Coming back: deploy the watch service first (its own approval), confirm a boot, then switch the rules
back on, on the canvas.

## When the resolver's sets cannot be made

If the watch service's reset of the names fails while the resolver is stopped, the agent starts the
resolver with a file that names only the sets that always exist (`force_exit`, `force_direct`), forgets
the names of the rules for groups, and marks `domains-scoped.retry`. The people of those groups then go
by the rules for everybody. The journal says so once («Сайты правил для отдельных групп не переданы
блокировщику…»), the agent tries again from its looks at the state, five minutes later, then with a
pause that doubles while the tries fail, up to an hour (each try restarts the resolver), and the
journal says when they are back. Any change of the rules or of
«Сайты» tries again at once.

## When a group's own list does not fit the blocker

The people of a group some rule sends abroad or directly ask the resolver through a client of their own,
which carries their group's whole list of upstreams and their addresses. AdGuard Home takes one request
of at most 65 536 bytes. When that client would not fit (a long list, a group that grew), it is not
made: the group's people ask DNS as everybody, while the routing of their rules still acts. The journal
says so once, and once more when it fits again; nothing is refused, neither a save of «Сайты» nor the
nightly refresh of the lists.
