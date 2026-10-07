# Libranet Operator Guide

Version 0.1 • October 2026

---

## 1. Purpose

This guide is for the person running a Libranet node, and covers the tasks
that need one. So far there are two: resetting the `/config` password, and
running a super node. It describes the Python node in this repository.
Where the node keeps its files is in
[File Layout](../implementation/File%20Layout.md), and what `/config` must do
is in [HTTP API](../specs/HttpApi.md) §2.3.

## 2. Resetting the `/config` Password

The node's administration page and API, `/config`, ask for a username and
password. A new node has none: the first request to `/config` that carries
a username and password sets them, and every request after it is checked
against them (HTTP API §2.3.1). The node keeps only a salted hash of them,
from which the password cannot be recovered, so a forgotten password cannot
be looked up. It can be reset: delete the file that holds the hash, and the
next request sets a new username and password.

Do the same to change a password you still know, or when `/config` answers
`500` and the web server's log names a `CredentialFileError`, which means
the file can no longer be read.

### 2.1 Where the File Is

The file is `config_credential`, in the node's key directory. That directory
also holds `node_private_key.pem` and `backup_secret`. Delete neither of
those: without the first, the node starts with a new identity, and without
the second, no backup it made can be read.

Unless something moves it, the key directory is `keys` in the node's data
directory:

| Platform | File |
| --- | --- |
| macOS | `~/Library/Application Support/libranet/keys/config_credential` |
| Linux | `~/.local/share/libranet/keys/config_credential` |
| Windows | `%LOCALAPPDATA%\libranet\keys\config_credential` |

On Linux, `$XDG_DATA_HOME` takes the place of `~/.local/share` when it is
set. What moves it:

- `identity.key_dir` in the config file names the key directory itself.
- Otherwise, `storage.data_dir` in the config file, or `--data-dir` on the
  command line, moves the data directory, and `keys` with it.
- A relative path in either is relative to the directory the node was
  started from, not to the config file.

To see what a node uses, run `libranet --check-config` with the `--config`
and `--data-dir` it was started with. It prints the configuration and
exits, starting nothing. `identity.key_dir` is the key directory, or, if it
is `null`, `keys` under `storage.data_dir`. A node that has run has also
logged its data directory, as `Data directory: ...` in
`libranet-supervisor.log`.

### 2.2 Resetting It

The node can keep running. It reads the file on every `/config` request,
so deleting it takes effect at once, with no restart.

1. **Close every browser tab showing `/config`.** The page reads its lists
   again every five seconds, and the browser sends the old username and
   password with those requests without asking. Left open, the page sets
   the old credential again within seconds of the delete.
2. **Delete the file and set the new credential in one command,** so that
   nothing else gets the chance in between. On macOS, for a node on port
   8080 (on Linux, use the path in §2.1):

   ```bash
   rm ~/Library/Application\ Support/libranet/keys/config_credential &&
     curl -u NEW_USER -e http://127.0.0.1:8180/config/ \
       http://127.0.0.1:8180/config/api
   ```

   `curl` asks for the new password, which keeps it out of the shell's
   history, and sends the request that sets it. `-e` names the `/config`
   page as the request's `Referer`, which every `/config/api` request
   needs. The node answers with the
   list of `/config`'s endpoints.
3. **Check that it took.** The same `curl` again, with the new password,
   gets that list again. A `401` means another request set a credential
   first: start again from step 1, and see §2.3.
4. **Give the browser the new credential.** Open `/config`, and give the
   new username and password when asked. A browser that still holds the old
   ones sends them first, is refused, and then asks.

`/config` is at `http://127.0.0.1:PORT/config/`. `PORT` is
`network.config_port` if that is set, and otherwise 100 above the node's
port, or the next 100 up that was free as the node started: 8180 for a
node on 8080, unless something else had that port. The node logs the
address it took as it starts, in `libranet-webserver.log`:
`Web server listening on 127.0.0.1:8080, and /config at
http://127.0.0.1:8180/config/`.

Without `curl`, use the browser alone. Quit it, so that it forgets the old
credential, delete the file, start the browser again, and type `/config`'s
address. The username and password you give when it asks are the new
credential. Type the address rather than following a link, even one on the
node's own root page: until a credential is set, `/config` refuses a
request that a link from another page made.

### 2.3 The Window Between

From the delete until the next request that carries a username and
password, the node has no credential, and that request sets whatever it
carries. Whoever sends it first holds `/config`.

What keeps that safe is that `/config` answers only this machine. It
listens only at `127.0.0.1`, and refuses with `403`, setting nothing, any
request whose connection does not come from a loopback address (HTTP API
§2.3). It also refuses the requests that other sites' pages make in your
browser (§2.3.3). On a machine only you use, with nothing relaying other
machines' traffic to its loopback addresses, the only request that can set
the credential is yours.

Two things change that:

- **Other people with accounts on the machine.** Every local user, and
  every process they run, can reach `127.0.0.1`.
- **Anything relaying other machines' traffic into loopback** on the node's
  machine: a reverse proxy, an SSH tunnel or port forward, or a container's
  published port. The requests it relays come from a loopback address, and
  pass.

On such a machine, stop any relay for the reset if you can, and keep to the
one command of §2.2, so that the window lasts only as long as typing the
password. Step 3 tells you whether yours was the request that set it.

### 2.4 What a Reset Leaves Alone

Nothing else depends on the credential. Backup jobs, registered
applications, the node's identity, and its backups are as they were, and
the new username need not be the old one.

## 3. Running a Super Node

A super node is one computer running sixteen nodes, one in each identifier
bucket, so that between them they give priority to all content. Run one on
a machine that is always on, and your laptops and other computers have a
single node nearby that tries to hold everything they share, while they are
asleep or away.

### 3.1 Why Sixteen

A node gives priority to keeping content whose hash shares the most leading
bits with its own node id. When it runs short of space, it hands off what it
gives least priority to, toward the node that matches it best
(HighLevelDesign §4.5). The first hex digit of a node id puts the node in
one of sixteen buckets, so a lone node favors about a sixteenth of all
content. Sixteen nodes, one per bucket, favor all of it: whatever an object's
hash, one of them shares at least its first hex digit.

A super node holds what reaches it: the content your computers push to it as
they create it, such as backups and applications (HighLevelDesign §4.10), and
what they hand off when they run short of space (§4.5). It does not go
looking for content it has not been sent.

### 3.2 What It Needs

- **A clone of this repository**, since Libranet is not on PyPI yet. The
  super node is run by `libranet-local-network`, installed with `libranet`.
- **Memory:** about 320 MB for each idle node, so about 5 GB for sixteen.
- **Disk:** room for the content limit (§3.4), and more. Each node also keeps
  its database, the application files it has served, and up to 600 MiB of
  logs, so sixteen nodes can hold up to 9.4 GiB of logs alone.
- **An address that does not change.** Other computers reach the nodes at
  the machine's address on the local network, and each node is told the
  others are at it. Give the machine a fixed address, or have the router
  always hand it the same one.
- **Its ports open** to the local network, 18400 to 18415, if the machine
  runs a firewall. The macOS firewall asks whether Python may accept incoming
  connections; a super node started with the machine (§3.7) has no one to
  answer, so allow it ahead of time in the firewall's options.
- **No sleep.** Turn sleep off in the machine's power settings, or its nodes
  sleep with it.

### 3.3 Creating One

In the clone, run the script with the machine's address in place of
`192.168.1.10`:

```bash
uv run libranet-local-network --count 16 \
  --dir ~/libranet-super-node --host 192.168.1.10 \
  --max-storage-bytes 1073741824
```

- `--count 16` runs sixteen nodes. The script gives the first sixteen keys
  whose node ids begin with the hex digits `0` to `f`, one in each bucket.
- `--dir` keeps the nodes' keys, content, and logs in that directory, to be
  used again (§3.6). Without it, they go in a temporary directory that is
  deleted when the script stops.
- `--host` is the address the nodes listen at, and the address each is told
  the others are at, which they pass on to your computers. Without it, they
  listen only at `127.0.0.1`, where no other computer can reach them.
- `--max-storage-bytes` limits the content each node holds (§3.4).

The script starts the nodes, node *n* on port 18400 + *n*, tells each one
about the other fifteen, and shows their connections as they are made. Each
node's pages are at its address and port, such as
`http://192.168.1.10:18400/`, from the super node itself as much as from any
other computer: the nodes do not listen at `127.0.0.1`. Each node's `/config`
is 100 above its port, 18500 to 18515, and answers only the super node
itself (§2.3).

`Ctrl-C` stops every node, and the script with them. The directory stays.

### 3.4 Limiting Its Size

`--max-storage-bytes` writes the limit into every node's configuration, the
`libranet.yaml` in that node's directory under `--dir`:

```yaml
storage:
  max_storage_bytes: 1073741824
```

That is the most bytes of content the node keeps (File Layout §3.1). Within
8 MiB of it, the node hands off the content it gives least priority to, and
deletes it. A backup or build waits at the limit for that to make room,
rather than take the node past it.
Each node is limited on its own, so the super node holds up to sixteen times
as much. Content hashes fall evenly across the buckets, so the nodes fill at
about the same rate.

| Each node | `--max-storage-bytes` | Super node |
| --- | --- | --- |
| 1 GiB | `1073741824` | 16 GiB |
| 4 GiB | `4294967296` | 64 GiB |
| 16 GiB | `17179869184` | 256 GiB |

The script writes each node's `libranet.yaml` afresh every time it starts, so
a change made by hand lasts only until then. To change the limit, stop the
super node and start it again with a new `--max-storage-bytes`. Left out, the
switch leaves the nodes with no limit. Lowered, it has each node hand off
whatever no longer fits.

The limit counts only content. Logs, databases, and application files take
space beyond it (§3.2). Each node also keeps 1 GiB free on the disk it uses
(`storage.min_free_bytes`, which the script leaves at its default), and all
sixteen use the same disk: when it comes within about 1 GiB of full, every
node hands content off.

### 3.5 Pointing Your Computers at It

A node finds its first peers in its seed list, which it reads only while it
knows no peers at all. The list shipped with Libranet is empty, so give the
node on each of your computers one that names the super node. It is a JSON
file in the form of a node list:

```json
{"nodes": {"http://192.168.1.10:18400": null}}
```

`null` stands for the node id, which the node learns as it connects. One
entry is enough: from that node it learns the other fifteen, at the address
`--host` gave. More entries, on the other ports, help when node 0 is down.

Name the file in the node's config file, which is at
`~/Library/Application Support/libranet/libranet.yaml` on macOS and
`~/.config/libranet/libranet.yaml` on Linux unless something moves it. A
relative path there is relative to the directory the node was started from,
so give the whole path:

```yaml
peers:
  seed_file: /Users/alice/Library/Application Support/libranet/seeds.json
```

Then restart the node. Once it has connected, its node list names the super
node's nodes at the super node's address. For a node on port 8080:

```bash
curl http://127.0.0.1:8080/data/nodes
```

A node that already knows peers does not read its seed list, so it finds the
super node only if one of its peers knows it.

### 3.6 Starting It Again

Run the same command again, with the same switches. The script finds the
keys in `--dir` and starts the same sixteen nodes, holding the content and
knowing the peers they had. It writes their configuration afresh from the
switches it is given, which is why each must be given again: run without
`--host`, the nodes listen only at `127.0.0.1`, and without
`--max-storage-bytes`, they have no limit. Keep the ports the same too, by
giving the same `--base-port` if one was given, since your computers know
the nodes by their ports.

Run only one copy at a time for the same `--dir`. If the super node starts
with the machine (§3.7), stop that service before running the script by hand.

### 3.7 Starting It With the Machine

A service manager can run the script as the machine starts, and stop it as
the machine shuts down. The script stops every node cleanly on `SIGTERM`,
which is what launchd and systemd send, and then exits with status 0. It
exits with status 1 when a node fails to start, and the service manager
starts it again. That covers the machine's address not being ready yet: the
nodes cannot listen at it, and the script gives up after two minutes.

Both examples run the command from the clone's own environment,
`.venv/bin/libranet-local-network`, which is what `uv run` runs, so the
service needs no `uv` on its path. §3.3's `uv run` creates it; after
updating the clone, run `uv sync` before the service next starts. Run the
service as yourself, not as root. Without a terminal, the script prints its
table of connections whenever a connection opens or closes, so its output
grows slowly while it runs.

#### 3.7.1 macOS

A launch daemon starts as the machine does, before anyone logs in. macOS
keeps a background job out of `~/Documents`, `~/Desktop`, and `~/Downloads`
unless it is given Full Disk Access, so keep the clone and `--dir` elsewhere,
such as `~/libranet` and `~/libranet-super-node`. With your user name, paths,
and address in place of the ones shown, save this as
`/Library/LaunchDaemons/local.libranet.super-node.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.libranet.super-node</string>
  <key>UserName</key>
  <string>alice</string>
  <key>WorkingDirectory</key>
  <string>/Users/alice/libranet</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/alice/libranet/.venv/bin/libranet-local-network</string>
    <string>--count</string>
    <string>16</string>
    <string>--dir</string>
    <string>/Users/alice/libranet-super-node</string>
    <string>--host</string>
    <string>192.168.1.10</string>
    <string>--max-storage-bytes</string>
    <string>1073741824</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ExitTimeOut</key>
  <integer>120</integer>
  <key>StandardOutPath</key>
  <string>/Users/alice/Library/Logs/libranet-super-node.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/alice/Library/Logs/libranet-super-node.log</string>
</dict>
</plist>
```

`KeepAlive` starts the script again only when it exits with a failure, and
`ExitTimeOut` gives it two minutes to stop the nodes before launchd kills
it. launchd reads the file only if root owns it and no one else can write
it. To set that, and start the super node now and at every start of the
machine:

```bash
sudo chown root:wheel /Library/LaunchDaemons/local.libranet.super-node.plist
sudo chmod 644 /Library/LaunchDaemons/local.libranet.super-node.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/local.libranet.super-node.plist
```

To stop it, `sudo launchctl bootout system/local.libranet.super-node`. It
starts again with the machine unless the file is removed too.

#### 3.7.2 Linux

A systemd service starts as the machine does. With your user name, paths,
and address in place of the ones shown, save this as
`/etc/systemd/system/libranet-super-node.service`:

```ini
[Unit]
Description=Libranet super node
Wants=network-online.target
After=network-online.target

[Service]
User=alice
WorkingDirectory=/home/alice/libranet
ExecStart=/home/alice/libranet/.venv/bin/libranet-local-network \
  --count 16 --dir /home/alice/libranet-super-node --host 192.168.1.10 \
  --max-storage-bytes 1073741824
KillMode=mixed
TimeoutStopSec=120
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`KillMode=mixed` sends `SIGTERM` to the script alone, which stops the nodes a
few at a time; systemd's default would signal every node at once.
`TimeoutStopSec` gives it two minutes to do so before systemd kills what is
left, and `Restart=on-failure` starts the script again only when it exits
with a failure. Then start the super node now and at every start of the
machine:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now libranet-super-node
```

`sudo systemctl stop libranet-super-node` stops it until the machine next
starts, and `sudo systemctl disable libranet-super-node` keeps it from
starting then. `journalctl -u libranet-super-node` shows the script's
output.
