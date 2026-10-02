# Libranet Operator Guide

Version 0.1 • October 2026

---

## 1. Purpose

This guide is for the person running a Libranet node, and covers the tasks
that need one. So far there is one: resetting the `/config` password. It
describes the Python node in this repository. Where the node keeps its files
is in [File Layout](../implementation/File%20Layout.md), and what `/config`
must do is in [HTTP API](../specs/HttpApi.md) §2.3.

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
     curl -u NEW_USER http://127.0.0.1:8180/config/api
   ```

   `curl` asks for the new password, which keeps it out of the shell's
   history, and sends the request that sets it. The node answers with the
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
