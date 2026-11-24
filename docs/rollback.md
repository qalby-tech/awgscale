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
* Any save of «Блокируется» in the earlier version — even one about another rule or group — drops the
  two kept fields (`held_route`, `held_on`) from **every** such rule, since that version does not know
  them. Each of them then comes back here as a switched-off block rule for its groups: safe (it sends
  nobody anywhere), but where it led and whether it was on are lost. Note them before the rollback and
  set them on the canvas again after. Its saves of «Сайты», its `store-init` and reading the list keep
  them (`tests/agent_store.py`, `t_rules_older_agent`, checks both).
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
watch service on every apply. An earlier watch service and an earlier gate never make them. While the
agent runs, it stops naming them at its next restart of the resolver (it asks the watch service for
`scoped-names`, see "Deploy order" below), but the file it wrote last stays until then: after a reboot
the earlier gate does not make the sets that file names, the resolver does not start, and DNS is down
for **everybody** until a newer watch service runs again.

The boot gate is put back by itself, too: after a boot that is not confirmed, `stack/deadman.sh`
restores `gate.pyz.proven`, the copy of the gate the last confirmed boot ran. Until the first confirmed
boot after the deploy of this version, that copy is the earlier gate. So before the first reboot after
deploying this watch service, either confirm a boot or keep every rule for some groups that routes
switched off.

Coming back: deploy the watch service first (its own approval), confirm a boot, then switch the rules
back on, on the canvas.

## When the resolver's sets cannot be made

The watch service's reset of the names (`/v1/domains`, while the resolver is stopped) makes and empties
every `rs_…` set; the ones it cannot make it names in its answer (`missing`) instead of failing the step.
The agent then starts the resolver with a file that names every set but those (only `force_exit` and
`force_direct` when none could be made), forgets the names of the rules for groups, and marks
`domains-scoped.retry`. The change for everybody — a save of «Сайты», the nightly refresh, a change on the
canvas — goes through all the same. The people of those groups go by the rules for everybody meanwhile.
The journal says so once, as soon as the agent sees the mark («Сайты правил для отдельных групп не
переданы блокировщику…»); the agent tries again from its looks at the state five minutes later, then with
a pause that doubles while the tries fail (10, 20, 40 minutes), up to an hour (each try restarts the
resolver, as long as the watch service makes these sets; one that does not, or does not answer, is only
asked — see below), and the journal says when they are back. Any change of the rules or of «Сайты»
tries again at once.

## Deploy order, and what guards it in the code

The agent names an `rs_…` set in the resolver's file only when the watch service says `scoped-names` in
its health (this version's watch service; the first one with rules for groups said only `scoped`). With
an older watch service the canvas refuses to switch a rule for some groups to «За границу» or «Напрямую»
(«…заработают после обновления сторожа»), and a restart of the resolver gives it the file of the sets that
always exist, marks the retry and says so in the journal. The retry then does not restart the resolver:
each time it is due (at the current pause, which does not grow meanwhile: five minutes unless earlier
tries had failed) it only asks the watch service's health whether it says `scoped-names` yet, and gives
the names (one restart) once it does. So deploying the agent before the watch service cannot take DNS
down, nor blip it every hour; the rules for groups simply wait.

What only this runbook guards: a reboot after the watch service or the gate was put back to an earlier
version while `domains.ipset` still names `rs_…` sets (see "The watch service and the boot gate" above).
The resolver's keeper does not drop missing sets before it starts AdGuard Home: its container has no
`ipset` tool and reads the cascade directory read-only.

## When a group's own list does not fit the blocker

The people of a group some rule sends abroad or directly ask the resolver through a client of their own,
which carries their group's whole list of upstreams and their addresses. AdGuard Home takes one request
of at most 65 536 bytes. When that client would not fit (a long list, a group that grew), it is not
made: the group's people ask DNS as everybody, while the routing of their rules still acts. The journal
says so once, and once more when it fits again; nothing is refused, neither a save of «Сайты» nor the
nightly refresh of the lists.

## The groups' lists (levels, children's add-on lists)

Which of HaGeZi's lists a person has is a **tag of their AdGuard client** (`device_pc` for Pro,
`device_laptop` Pro++, `device_nas` Ultimate, `device_tv` NSFW, `device_gameconsole` gambling,
`device_securityalarm` the ways around the filter, `device_phone` social networks: AdGuard knows only its
own fixed tags, and no HaGeZi list uses these). The file
`/opt/AdGuardHome/userfilters/group-lists.txt` holds each needed list's names once, one rule per name
with the tags of every list that has it (`||name^$ctag=device_nas|device_pc`); it names no client. Beside
it, `group-lists.txt.unread` exists while AdGuard has not read the file in place yet.

Measured on the pinned AdGuard v0.107.80 with the real lists (24 Nov 2026), under the resolver's 576m without
swap, each pair in a fresh AdGuard with no other list (VmHWM / the container's peak, after the step):

| step | rules | file | after the first | after the second |
|---|---|---|---|---|
| the earlier file of names (3 levels with twins, 1 children's group) → converted, refresh | 407 338 | 37.3 → 21.7 MiB | 258 / 336 MiB | 395 / 479 MiB |
| converted → downloaded again, refresh | 407 338 | 21.7 → 20.6 MiB | 190 / 235 MiB | 316 / 380 MiB |
| the final file → a changed one, refresh (steady state) | 407 338 | 20.6 MiB | 186 / 231 MiB | 304 / 368 MiB |
| all seven lists → a changed one, refresh | 476 943 | 23.6 MiB | 195 / 246 MiB | 333 / 407 MiB |

The highest is the conversion's refresh (the earlier file loaded, the converted one read). None was killed.

The agent's own work on them (it reads the file line by line): converting the file of names 102 MiB at
most, downloading these lists again 131 MiB, all seven 161 MiB (ddd7ca8's keeper: 107 MiB for the same
lists; the agent's container has 320m).

### Coming from the earlier version (ddd7ca8)

Its file names clients (`$client='grp-pro'|…`, header `! Needs: …`), and its clients carry no tags.

1. While that file is in place the agent moves nobody: each sync only gives every client, in place, the
   tags of the lists the file names it with (and takes any other tag of a list away). Nobody's lists
   change: the file still blocks by names, and a tag no rule names blocks nothing.
2. Once a sync has seen every client so, the file is **converted in place**, without downloading: each
   name gets the tags of the lists all of whose clients it was blocked for, so exactly the same people
   are blocked the same names. One write, one refresh.
3. Right after, every list is downloaded again and the file rewritten from them (one more refresh). Then
   the clients change as wanted (a group's rule variants appear only now).

Between 1 and 2 the groups' changes wait: until a sync has tagged the clients (seconds; the conversion
needs no network) and until AdGuard has read the converted file. A refresh reads every list of AdGuard,
remote ones first: with a slow remote list it takes as long as that download (measured: a remote list
that stalls 60 s, the agent stops waiting after 2 s, AdGuard reads the local file at 62 s). The agent
does not wait for the answer: that AdGuard has read the file is also seen from the list itself (the
number of rules and the moment AdGuard shows for it, compared with those noted in `.unread` just before
the write). The wait has no bound of its own if the converted file cannot be written (the disk), if
AdGuard cannot be asked, or if AdGuard refuses a client's change of tags in place (a client within a
few dozen bytes of its 65 536); the journal says so.

### Putting ddd7ca8 back

`tests/agent_edges.py` (`t_group_tags`) runs ddd7ca8's own sync and keeper on a state this version
left, and checks every state AdGuard goes through.

* Its sync compares clients without their tags. A client whose people, switches and upstreams are as it
  wants them is left alone, **with its tags**: its people keep their lists through the tags until its
  file of names is read. A client it wants otherwise it deletes and adds again, without tags (it always
  does so: between the two calls those people are in no client); they have their lists again by name
  once its file is read.
* It does not know the rule variants of clients (`grp-…-r…`): it deletes them and puts their people
  back into their group's client, as above.
* Its keeper does not recognise the file (no `! Needs:` line), downloads its lists and writes its file of
  names over it, then refreshes. Until then the file of tags stays in force (if a download fails, it says
  so and leaves the file of tags as it is). The tags left on its clients do nothing under its file.
* It leaves `group-lists.txt.unread` (if any) where it is; this version reads that mark again when it
  comes back, and clears it once AdGuard has read the file.

Coming back: deploy this version. It finds the file of names, tags the clients in place, converts the
file, and goes on as above.

### What a person may see meanwhile

* A children's group that switches lists (NSFW → social networks) keeps the old tag until the new list is
  in the file and AdGuard has read it; the old list is taken out of the file only once no client carries
  its tag and no group wants it. A list that cannot be downloaded keeps the old one in force forever
  (the journal says so); nobody's change of clients waits for it.
* A newcomer to a client that is still switching its lists has, meanwhile, its old lists too.
* Right after the conversion a name may carry a few more tags than its lists have (only clients the
  earlier file did not name can notice: a new rule variant, a newly made group); the download that
  follows within the same minute puts it right.
