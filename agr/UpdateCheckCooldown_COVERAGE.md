# Update-check cooldown scope

`UpdateCheckCooldown.tla` covers a bounded UI session with a metadata worker,
current/stale queued responses and a server-directed cooldown. A delayed response
cannot disable the current request; a new metadata request cannot start during
the cooldown; a limit response never downloads or installs. Expiration permits
an explicit check without starting an automatic retry. All three mutants must
violate their corresponding invariant.

`updater.rate_limit_error` only parses bounded response content and server header
values. It holds no cache, token, worker or persistent state. Header/date bounds,
ordinary access-denial errors and response cleanup are tested separately.
The cooldown is session-local, not a persisted IP-wide request quota. It cannot
remove a GitHub limit or prevent another application from consuming that quota.

The browser fallback opens only the fixed official releases page or the release
URL already admitted by the existing metadata validator. It does not parse web
pages into trusted update metadata or silently download/install a package.
Existing channel, asset URL, size, digest, manifest, extraction and transactional
installation checks are unchanged and covered by the updater regression tests.
No new installer/recovery lifetime or storage ownership is introduced.
