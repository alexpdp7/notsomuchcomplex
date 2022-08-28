# PYSTRAY_BACKEND=dummy emailproxy  --no-gui --local-server-auth
import collections
import dataclasses
import itertools
import os
import pathlib
import socket
import subprocess
import textwrap
import time

import appdirs
import yaml

APPNAME="notsomuchcomplex"


def _u(s):
    return textwrap.dedent(s).lstrip()


def get_free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class EmailAccount:
    def __init__(self, login):
        self.login = login
        home = os.environ["HOME"]
        self.store_path = pathlib.Path(f"{home}/.mail/{login}")

    def get_proxies(self):
        return []

    def get_proxy_config(self, redirect_port):
        return ""

@dataclasses.dataclass(frozen=True)
class Proxy:
    type: str
    server_address: str
    server_port: int

    def get_proxy_config_port(self, port):
        return _u(f"""
            [{self.type}-{port}]
            server_address = {self.server_address}
            server_port = {self.server_port}
        """)


class GmailAccount(EmailAccount):
    def get_proxies(self):
        return [
            Proxy("IMAP", "imap.gmail.com", 993),
            Proxy("SMTP", "smtp.gmail.com", 465),
        ]

    def get_proxy_config(self, redirect_port):
        return _u(f"""
            [{self.login}]

            permission_url = https://accounts.google.com/o/oauth2/auth
            token_url = https://oauth2.googleapis.com/token
            oauth2_scope = https://mail.google.com/
            redirect_uri = http://localhost:{redirect_port}
            # https://gitlab.gnome.org/GNOME/gnome-online-accounts/-/blob/master/meson_options.txt#L18
            client_id = 44438659992-7kgjeitenc16ssihbtdjbgguch7ju55s.apps.googleusercontent.com
            client_secret = -gMLuQyDiI0XrQS_vx_mhuYF
        """)

    def get_mbsync_config(self, proxies_to_ports):
        imap_proxy_ports = [port for proxy, port in proxies_to_ports.items() if proxy.server_address == "imap.gmail.com"]
        assert len(imap_proxy_ports) == 1
        imap_proxy_port = imap_proxy_ports[0]

        return _u(f"""
            IMAPAccount {self.login}
            # Address to connect to
            Host localhost
            Port {imap_proxy_port}
            User {self.login}
            Pass none
            SSLType None

            IMAPStore {self.login}-remote
            Account {self.login}

            MaildirStore {self.login}-local
            SubFolders Verbatim
            # The trailing "/" is important
            Path {self.store_path}/
            Inbox {self.store_path}/Inbox

            Channel {self.login}
            Far :{self.login}-remote:
            Near :{self.login}-local:
            Patterns *
            # Automatically create missing mailboxes, both locally and on the server
            Create Both
            # Sync the movement of messages between folders and deletions, add after making sure the sync works
            # Expunge Both
            # Save the synchronization state files in the relevant directory
            SyncState *

        """)


class YahooAccount(EmailAccount):
    def __init__(self, login, options):
        self.application_password = options["application_password"]
        super().__init__(login)

    def get_mbsync_config(self, _proxies_to_ports):
        return _u(f"""
            IMAPAccount {self.login}
            # Address to connect to
            Host imap.mail.yahoo.com
            User {self.login}
            Pass {self.application_password}
            SSLType IMAPS
            PipelineDepth 5

            IMAPStore {self.login}-remote
            Account {self.login}

            MaildirStore {self.login}-local
            SubFolders Verbatim
            # The trailing "/" is important
            Path {self.store_path}/
            Inbox {self.store_path}/Inbox

            Channel {self.login}
            Far :{self.login}-remote:
            Near :{self.login}-local:
            Patterns *
            # Automatically create missing mailboxes, both locally and on the server
            Create Both
            # Sync the movement of messages between folders and deletions, add after making sure the sync works
            # Expunge Both
            # Save the synchronization state files in the relevant directory
            # SyncState *

        """)


def create_email_account(address, options):
    if address.endswith("@gmail.com"):
        return GmailAccount(address)
    if address.endswith("@yahoo.es"):
        return YahooAccount(address, options)
    assert False, f"unknown type for login {login}"


def create_emailproxy_config(proxies_to_ports, emails, redirect_port):
    config = ""
    for proxy, port in proxies_to_ports.items():
        config += proxy.get_proxy_config_port(port)
    for email in emails:
        config += email.get_proxy_config(redirect_port)
    return config


def create_mbsync_config(emails, proxies_to_ports):
    config = ""
    for email in emails:
        config += email.get_mbsync_config(proxies_to_ports)
    return config
   

def main():
    config_dir = pathlib.Path(appdirs.user_config_dir(APPNAME))
    with open(config_dir / "accounts.yaml") as config_file:
        config = yaml.safe_load(config_file)
    redirect_port = config["redirect_port"]
    emails = [create_email_account(address, options) for address, options in config["emails"].items()]
    proxies = set(itertools.chain(*[email.get_proxies() for email in emails]))
    proxies_to_ports = dict([(proxy, get_free_port()) for proxy in proxies])
    epc_path = config_dir / "emailproxy.config" 
    with open(epc_path, "w", encoding="utf8") as epc_file:
        epc_file.write(create_emailproxy_config(proxies_to_ports, emails, redirect_port))
    mb_path = config_dir / "mbsyncrc"
    with open(mb_path, "w", encoding="utf8") as mb_file:
        mb_file.write(create_mbsync_config(emails, proxies_to_ports))
    for email in emails:
        email.store_path.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(["emailproxy", "--no-gui", "--local-server-auth", f"--config-file={epc_path}"], env={"PYSTRAY_BACKEND": "dummy", "PATH": os.environ["PATH"]}) as ep_process:
        time.sleep(5)  # TODO: wait proxy
        while True:
            print("Running mbsync...")
            subprocess.run(["mbsync", "-a", "-c", mb_path])
            print("... mbsync finished, going to sleep")
            subprocess.run(["notmuch", "new"])
            time.sleep(60)


if __name__ == "__main__":
    main()
