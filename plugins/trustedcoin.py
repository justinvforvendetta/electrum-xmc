#!/usr/bin/env python
#
# Electrum - Lightweight Bitcoin Client
# Copyright (C) 2015 Thomas Voegtlin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import threading
import socket
import os
import re
import requests
import json
from hashlib import sha256
from urlparse import urljoin
from urllib import quote

from PyQt4.QtGui import *
from PyQt4.QtCore import *

import electrum_xmc
from electrum_xmc import bitcoin
from electrum_xmc.bitcoin import *
from electrum_xmc.mnemonic import Mnemonic
from electrum_xmc import version
from electrum_xmc.wallet import Multisig_Wallet, BIP32_Wallet
from electrum_xmc.i18n import _
from electrum_xmc.plugins import BasePlugin, run_hook, hook

from electrum_xmc_gui.qt.util import *
from electrum_xmc_gui.qt.qrcodewidget import QRCodeWidget
from electrum_xmc_gui.qt.amountedit import AmountEdit
from electrum_xmc_gui.qt.main_window import StatusBarButton

from decimal import Decimal

# signing_xpub is hardcoded so that the wallet can be restored from seed, without TrustedCoin's server
signing_xpub = "xpub661MyMwAqRbcGnMkaTx2594P9EDuiEqMq25PM2aeG6UmwzaohgA6uDmNsvSUV8ubqwA3Wpste1hg69XHgjUuCD5HLcEp2QPzyV1HMrPppsL"
billing_xpub = "xpub6DTBdtBB8qUmH5c77v8qVGVoYk7WjJNpGvutqjLasNG1mbux6KsojaLrYf2sRhXAVU4NaFuHhbD9SvVPRt1MB1MaMooRuhHcAZH1yhQ1qDU"

SEED_PREFIX = version.SEED_PREFIX_2FA


class TrustedCoinException(Exception):
    def __init__(self, message, status_code=0):
        Exception.__init__(self, message)
        self.status_code = status_code

class TrustedCoinCosignerClient(object):
    def __init__(self, user_agent=None, base_url='https://api.trustedcoin.com/2/', debug=False):
        self.base_url = base_url
        self.debug = debug
        self.user_agent = user_agent

    def send_request(self, method, relative_url, data=None):
        kwargs = {'headers': {}}
        if self.user_agent:
            kwargs['headers']['user-agent'] = self.user_agent
        if method == 'get' and data:
            kwargs['params'] = data
        elif method == 'post' and data:
            kwargs['data'] = json.dumps(data)
            kwargs['headers']['content-type'] = 'application/json'
        url = urljoin(self.base_url, relative_url)
        if self.debug:
            print '%s %s %s' % (method, url, data)
        response = requests.request(method, url, **kwargs)
        if self.debug:
            print response.text
            print
        if response.status_code != 200:
            message = str(response.text)
            if response.headers.get('content-type') == 'application/json':
                r = response.json()
                if 'message' in r:
                    message = r['message']
            raise TrustedCoinException(message, response.status_code)
        if response.headers.get('content-type') == 'application/json':
            return response.json()
        else:
            return response.text

    def get_terms_of_service(self, billing_plan='electrum-per-tx-otp'):
        """
        Returns the TOS for the given billing plan as a plain/text unicode string.
        :param billing_plan: the plan to return the terms for
        """
        payload = {'billing_plan': billing_plan}
        return self.send_request('get', 'tos', payload)

    def create(self, xpubkey1, xpubkey2, email, billing_plan='electrum-per-tx-otp'):
        """
        Creates a new cosigner resource.
        :param xpubkey1: a bip32 extended public key (customarily the hot key)
        :param xpubkey2: a bip32 extended public key (customarily the cold key)
        :param email: a contact email
        :param billing_plan: the billing plan for the cosigner
        """
        payload = {
            'email': email,
            'xpubkey1': xpubkey1,
            'xpubkey2': xpubkey2,
            'billing_plan': billing_plan,
        }
        return self.send_request('post', 'cosigner', payload)

    def auth(self, id, otp):
        """
        Attempt to authenticate for a particular cosigner.
        :param id: the id of the cosigner
        :param otp: the one time password
        """
        payload = {'otp': otp}
        return self.send_request('post', 'cosigner/%s/auth' % quote(id), payload)

    def get(self, id):
        """
        Attempt to authenticate for a particular cosigner.
        :param id: the id of the cosigner
        :param otp: the one time password
        """
        return self.send_request('get', 'cosigner/%s' % quote(id))

    def sign(self, id, transaction, otp):
        """
        Attempt to authenticate for a particular cosigner.
        :param id: the id of the cosigner
        :param transaction: the hex encoded [partially signed] compact transaction to sign
        :param otp: the one time password
        """
        payload = {
            'otp': otp,
            'transaction': transaction
        }
        return self.send_request('post', 'cosigner/%s/sign' % quote(id), payload)

    def transfer_credit(self, id, recipient, otp, signature_callback):
        """
        Tranfer a cosigner's credits to another cosigner.
        :param id: the id of the sending cosigner
        :param recipient: the id of the recipient cosigner
        :param otp: the one time password (of the sender)
        :param signature_callback: a callback that signs a text message using xpubkey1/0/0 returning a compact sig
        """
        payload = {
            'otp': otp,
            'recipient': recipient,
            'timestamp': int(time.time()),

        }
        relative_url = 'cosigner/%s/transfer' % quote(id)
        full_url = urljoin(self.base_url, relative_url)
        headers = {
            'x-signature': signature_callback(full_url + '\n' + json.dumps(payload))
        }
        return self.send_request('post', relative_url, payload, headers)


server = TrustedCoinCosignerClient(user_agent="Electrum/" + version.ELECTRUM_VERSION)


class Wallet_2fa(Multisig_Wallet):

    def __init__(self, storage):
        BIP32_Wallet.__init__(self, storage)
        self.wallet_type = '2fa'
        self.m = 2
        self.n = 3

    def get_action(self):
        xpub1 = self.master_public_keys.get("x1/")
        xpub2 = self.master_public_keys.get("x2/")
        xpub3 = self.master_public_keys.get("x3/")
        if xpub2 is None and not self.storage.get('use_trustedcoin'):
            return 'show_disclaimer'
        if xpub2 is None:
            return 'create_extended_seed'
        if xpub3 is None:
            return 'create_remote_key'
        if not self.accounts:
            return 'create_accounts'

    def make_seed(self):
        return Mnemonic('english').make_seed(num_bits=256, prefix=SEED_PREFIX)

    def estimated_fee(self, tx):
        fee = Multisig_Wallet.estimated_fee(self, tx)
        x = run_hook('extra_fee', tx)
        if x: fee += x
        return fee

    def get_tx_fee(self, tx):
        fee = Multisig_Wallet.get_tx_fee(self, tx)
        x = run_hook('extra_fee', tx)
        if x: fee += x
        return fee



class Plugin(BasePlugin):

    wallet = None

    def __init__(self, x, y):
        BasePlugin.__init__(self, x, y)
        self.seed_func = lambda x: bitcoin.is_new_seed(x, SEED_PREFIX)
        self.billing_info = None
        self.is_billing = False

    def constructor(self, s):
        return Wallet_2fa(s)

    def is_available(self):
        if not self.wallet:
            return False
        if self.wallet.storage.get('wallet_type') == '2fa':
            return True
        return False

    def set_enabled(self, enabled):
        self.wallet.storage.put('use_' + self.name, enabled)

    def is_enabled(self):
        if not self.is_available():
            return False
        if self.wallet.master_private_keys.get('x2/'):
            return False
        return True

    def make_long_id(self, xpub_hot, xpub_cold):
        return bitcoin.sha256(''.join(sorted([xpub_hot, xpub_cold])))

    def get_user_id(self):
        xpub_hot = self.wallet.master_public_keys["x1/"]
        xpub_cold = self.wallet.master_public_keys["x2/"]
        long_id = self.make_long_id(xpub_hot, xpub_cold)
        short_id = hashlib.sha256(long_id).hexdigest()
        return long_id, short_id

    def make_xpub(self, xpub, s):
        _, _, _, c, cK = deserialize_xkey(xpub)
        cK2, c2 = bitcoin._CKD_pub(cK, c, s)
        xpub2 = ("0488B21E" + "00" + "00000000" + "00000000").decode("hex") + c2 + cK2
        return EncodeBase58Check(xpub2)

    def make_billing_address(self, num):
        long_id, short_id = self.get_user_id()
        xpub = self.make_xpub(billing_xpub, long_id)
        _, _, _, c, cK = deserialize_xkey(xpub)
        cK, c = bitcoin.CKD_pub(cK, c, num)
        address = public_key_to_bc_address( cK )
        return address

    def create_extended_seed(self, wallet, window):
        seed = wallet.make_seed()
        if not window.show_seed(seed, None):
            return

        if not window.verify_seed(seed, None, self.seed_func):
            return

        password = window.password_dialog()
        wallet.storage.put('seed_version', wallet.seed_version, True)
        wallet.storage.put('use_encryption', password is not None, True)

        words = seed.split()
        n = len(words)/2
        wallet.add_cosigner_seed(' '.join(words[0:n]), 'x1/', password)
        wallet.add_cosigner_xpub(' '.join(words[n:]), 'x2/')

        msg = [
            _('Your wallet file is:') + " %s"%os.path.abspath(wallet.storage.path),
            _('You need to be online in order to complete the creation of your wallet.'),
            _('If you generated your seed on an offline computer, click on "%s" to close this window, move your wallet file to an online computer and reopen it with Electrum.') % _('Close'),
            _('If you are online, click on "%s" to continue.') % _('Next')
        ]
        return window.question('\n\n'.join(msg), no_label=_('Close'), yes_label=_('Next'))


    def show_disclaimer(self, wallet, window):
        msg = [
            _("Two-factor authentication is a service provided by TrustedCoin.") + ' ',
            _("It uses a multi-signature wallet, where you own 2 of 3 keys.") + ' ',
            _("The third key is stored on a remote server that signs transactions on your behalf.") + ' ',
            _("To use this service, you will need a smartphone with Google Authenticator.") + '\n\n',

            _("A small fee will be charged on each transaction that uses the remote server.") + ' ',
            _("You may check and modify your billing preferences once the installation is complete.") + '\n\n',

            _("Note that your coins are not locked in this service.") + ' ',
            _("You may withdraw your funds at any time and at no cost, without the remote server, by using the 'restore wallet' option with your wallet seed.") + '\n\n',

            _('The next step will generate the seed of your wallet.') + ' ',
            _('This seed will NOT be saved in your computer, and it must be stored on paper.') + ' ',
            _('To be safe from malware, you may want to do this on an offline computer, and move your wallet later to an online computer.')
        ]
        icon = QPixmap(':icons/trustedcoin.png')
        if not window.question(''.join(msg), icon=icon):
            return False
        self.wallet = wallet
        self.set_enabled(True)
        return True


    def restore_third_key(self, wallet):
        long_user_id, short_id = self.get_user_id()
        xpub3 = self.make_xpub(signing_xpub, long_user_id)
        wallet.add_master_public_key('x3/', xpub3)

    @hook
    def do_clear(self):
        self.is_billing = False

    @hook
    def load_wallet(self, wallet, window):
        self.wallet = wallet
        self.window = window
        self.trustedcoin_button = StatusBarButton(QIcon(":icons/trustedcoin.png"), _("TrustedCoin"), self.settings_dialog)
        self.window.statusBar().addPermanentWidget(self.trustedcoin_button)
        self.xpub = self.wallet.master_public_keys.get('x1/')
        self.user_id = self.get_user_id()[1]
        t = threading.Thread(target=self.request_billing_info)
        t.setDaemon(True)
        t.start()

    @hook
    def installwizard_load_wallet(self, wallet, window):
        self.wallet = wallet
        self.window = window

    @hook
    def close_wallet(self):
        self.window.statusBar().removeWidget(self.trustedcoin_button)

    @hook
    def get_wizard_action(self, window, wallet, action):
        if hasattr(self, action):
            return getattr(self, action)

    @hook
    def installwizard_restore(self, window, storage):
        if storage.get('wallet_type') != '2fa':
            return

        seed = window.enter_seed_dialog("Enter your seed", None, func=self.seed_func)
        if not seed:
            return
        wallet = Wallet_2fa(storage)
        self.wallet = wallet
        password = window.password_dialog()

        wallet.add_seed(seed, password)
        words = seed.split()
        n = len(words)/2
        wallet.add_cosigner_seed(' '.join(words[0:n]), 'x1/', password)
        wallet.add_cosigner_seed(' '.join(words[n:]), 'x2/', password)

        self.restore_third_key(wallet)
        wallet.create_main_account(password)
        # disable plugin
        self.set_enabled(False)
        return wallet


    def create_remote_key(self, wallet, window):
        self.wallet = wallet
        self.window = window

        if wallet.storage.get('wallet_type') != '2fa':
            raise
            return

        email = self.accept_terms_of_use(window)
        if not email:
            return

        xpub_hot = wallet.master_public_keys["x1/"]
        xpub_cold = wallet.master_public_keys["x2/"]

        # Generate third key deterministically.
        long_user_id, self.user_id = self.get_user_id()
        xpub3 = self.make_xpub(signing_xpub, long_user_id)

        # secret must be sent by the server
        try:
            r = server.create(xpub_hot, xpub_cold, email)
        except socket.error:
            self.window.show_message('Server not reachable, aborting')
            return
        except TrustedCoinException as e:
            if e.status_code == 409:
                r = None
            else:
                raise e

        if r is None:
            otp_secret = None
        else:
            otp_secret = r.get('otp_secret')
            if not otp_secret:
                self.window.show_message(_('Error'))
                return
            _xpub3 = r['xpubkey_cosigner']
            _id = r['id']
            try:
                assert _id == self.user_id, ("user id error", _id, self.user_id)
                assert xpub3 == _xpub3, ("xpub3 error", xpub3, _xpub3)
            except Exception as e:
                self.window.show_message(str(e))
                return

        if not self.setup_google_auth(self.window, self.user_id, otp_secret):
            return

        self.wallet.add_master_public_key('x3/', xpub3)
        return True



    def need_server(self, tx):
        from electrum_xmc.account import BIP32_Account
        # Detect if the server is needed
        long_id, short_id = self.get_user_id()
        xpub3 = self.wallet.master_public_keys['x3/']
        for x in tx.inputs_to_sign():
            if x[0:2] == 'ff':
                xpub, sequence = BIP32_Account.parse_xpubkey(x)
                if xpub == xpub3:
                    return True
        return False

    @hook
    def sign_tx(self, tx):
        self.print_error("twofactor:sign_tx")
        if self.wallet.storage.get('wallet_type') != '2fa':
            return

        if not self.need_server(tx):
            self.print_error("twofactor: xpub3 not needed")
            self.auth_code = None
            return

        self.auth_code = self.auth_dialog()

    @hook
    def before_send(self):
        # request billing info before forming the transaction
        self.billing_info = None
        self.waiting_dialog = WaitingDialog(self.window, 'please wait...', self.request_billing_info)
        self.waiting_dialog.start()
        self.waiting_dialog.wait()
        if self.billing_info is None:
            self.window.show_message('Could not contact server')
            return True
        return False

    @hook
    def extra_fee(self, tx):
        if self.billing_info.get('tx_remaining'):
            return 0
        if self.is_billing:
            return 0
        # trustedcoin won't charge if the total inputs is lower than their fee
        price = int(self.price_per_tx.get(1))
        assert price <= 100000
        if tx.input_value() < price:
            self.print_error("not charging for this tx")
            return 0
        return price

    @hook
    def make_unsigned_transaction(self, tx):
        price = self.extra_fee(tx)
        if not price:
            return
        tx.outputs.append(('address', self.billing_info['billing_address'], price))

    @hook
    def sign_transaction(self, tx, password):
        self.print_error("twofactor:sign")
        if self.wallet.storage.get('wallet_type') != '2fa':
            self.print_error("twofactor: aborting")
            return

        self.long_user_id, self.user_id = self.get_user_id()

        if not self.auth_code:
            return

        if tx.is_complete():
            return

        tx_dict = tx.as_dict()
        raw_tx = tx_dict["hex"]
        try:
            r = server.sign(self.user_id, raw_tx, self.auth_code)
        except Exception as e:
            tx.error = str(e)
            return

        self.print_error( "received answer", r)
        if not r:
            return

        raw_tx = r.get('transaction')
        tx.update(raw_tx)
        self.print_error("twofactor: is complete", tx.is_complete())


    def auth_dialog(self ):
        d = QDialog(self.window)
        d.setModal(1)
        vbox = QVBoxLayout(d)
        pw = AmountEdit(None, is_int = True)
        msg = _('Please enter your Google Authenticator code')
        vbox.addWidget(QLabel(msg))
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(QLabel(_('Code')), 1, 0)
        grid.addWidget(pw, 1, 1)
        vbox.addLayout(grid)
        vbox.addLayout(Buttons(CancelButton(d), OkButton(d)))
        if not d.exec_():
            return
        return pw.get_amount()

    def settings_dialog(self):
        self.waiting_dialog = WaitingDialog(self.window, 'please wait...', self.request_billing_info, self.show_settings_dialog)
        self.waiting_dialog.start()

    def show_settings_dialog(self, success):
        if not success:
            self.window.show_message(_('Server not reachable.'))
            return

        d = QDialog(self.window)
        d.setWindowTitle("TrustedCoin Information")
        d.setMinimumSize(500, 200)
        vbox = QVBoxLayout(d)
        hbox = QHBoxLayout()

        logo = QLabel()
        logo.setPixmap(QPixmap(":icons/trustedcoin.png"))
        msg = _('This wallet is protected by TrustedCoin\'s two-factor authentication.') + '<br/>'\
              + _("For more information, visit") + " <a href=\"https://api.trustedcoin.com/#/electrum-help\">https://api.trustedcoin.com/#/electrum-help</a>"
        label = QLabel(msg)
        label.setOpenExternalLinks(1)

        hbox.addStretch(10)
        hbox.addWidget(logo)
        hbox.addStretch(10)
        hbox.addWidget(label)
        hbox.addStretch(10)

        vbox.addLayout(hbox)
        vbox.addStretch(10)

        msg = _('TrustedCoin charges a fee per co-signed transaction. You may pay on each transaction (an extra output will be added to your transaction), or you may purchase prepaid transaction using this dialog.') + '<br/>'
        label = QLabel(msg)
        label.setWordWrap(1)
        vbox.addWidget(label)

        vbox.addStretch(10)
        grid = QGridLayout()
        vbox.addLayout(grid)

        v = self.price_per_tx.get(1)
        grid.addWidget(QLabel(_("Price per transaction (not prepaid):")), 0, 0)
        grid.addWidget(QLabel(self.window.format_amount(v) + ' ' + self.window.base_unit()), 0, 1)

        i = 1

        if 10 not in self.price_per_tx:
            self.price_per_tx[10] = 10 * self.price_per_tx.get(1)

        for k, v in sorted(self.price_per_tx.items()):
            if k == 1:
                continue
            grid.addWidget(QLabel("Price for %d prepaid transactions:"%k), i, 0)
            grid.addWidget(QLabel("%d x "%k + self.window.format_amount(v/k) + ' ' + self.window.base_unit()), i, 1)
            b = QPushButton(_("Buy"))
            b.clicked.connect(lambda b, k=k, v=v: self.on_buy(k, v, d))
            grid.addWidget(b, i, 2)
            i += 1

        n = self.billing_info.get('tx_remaining', 0)
        grid.addWidget(QLabel(_("Your wallet has %d prepaid transactions.")%n), i, 0)

        # tranfer button
        #def on_transfer():
        #    server.transfer_credit(self.user_id, recipient, otp, signature_callback)
        #    pass
        #b = QPushButton(_("Transfer"))
        #b.clicked.connect(on_transfer)
        #grid.addWidget(b, 1, 2)

        #grid.addWidget(QLabel(_("Next Billing Address:")), i, 0)
        #grid.addWidget(QLabel(self.billing_info['billing_address']), i, 1)
        vbox.addLayout(Buttons(CloseButton(d)))
        d.exec_()

    def on_buy(self, k, v, d):
        d.close()
        if self.window.pluginsdialog:
            self.window.pluginsdialog.close()
        uri = "xmc:" + self.billing_info['billing_address'] + "?message=TrustedCoin %d Prepaid Transactions&amount="%k + str(Decimal(v)/100000000)
        self.is_billing = True
        self.window.pay_from_URI(uri)
        self.window.payto_e.setFrozen(True)
        self.window.message_e.setFrozen(True)
        self.window.amount_e.setFrozen(True)

    def request_billing_info(self):
        billing_info = server.get(self.user_id)
        billing_address = self.make_billing_address(billing_info['billing_index'])
        assert billing_address == billing_info['billing_address']
        self.billing_info = billing_info
        self.price_per_tx = dict(self.billing_info['price_per_tx'])
        return True

    def accept_terms_of_use(self, window):
        vbox = QVBoxLayout()
        window.set_layout(vbox)
        vbox.addWidget(QLabel(_("Terms of Service")))

        tos_e = QTextEdit()
        tos_e.setReadOnly(True)
        vbox.addWidget(tos_e)

        vbox.addWidget(QLabel(_("Please enter your e-mail address")))
        email_e = QLineEdit()
        vbox.addWidget(email_e)
        vbox.addStretch()
        accept_button = OkButton(window, _('Accept'))
        accept_button.setEnabled(False)
        vbox.addLayout(Buttons(CancelButton(window), accept_button))

        def request_TOS():
            tos = server.get_terms_of_service()
            self.TOS = tos
            window.emit(SIGNAL('twofactor:TOS'))

        def on_result():
            tos_e.setText(self.TOS)

        window.connect(window, SIGNAL('twofactor:TOS'), on_result)
        t = threading.Thread(target=request_TOS)
        t.setDaemon(True)
        t.start()

        regexp = r"[^@]+@[^@]+\.[^@]+"
        email_e.textChanged.connect(lambda: accept_button.setEnabled(re.match(regexp,email_e.text()) is not None))
        email_e.setFocus(True)

        if not window.exec_():
            return

        email = str(email_e.text())
        return email


    def setup_google_auth(self, window, _id, otp_secret):
        vbox = QVBoxLayout()
        window.set_layout(vbox)
        if otp_secret is not None:
            uri = "otpauth://totp/%s?secret=%s"%('trustedcoin.com', otp_secret)
            vbox.addWidget(QLabel("Please scan this QR code in Google Authenticator."))
            qrw = QRCodeWidget(uri)
            vbox.addWidget(qrw, 1)
            msg = _('Then, enter your Google Authenticator code:')
        else:
            label = QLabel("This wallet is already registered, but it was never authenticated. To finalize your registration, please enter your Google Authenticator Code. If you do not have this code, delete the wallet file and start a new registration")
            label.setWordWrap(1)
            vbox.addWidget(label)
            msg = _('Google Authenticator code:')

        hbox = QHBoxLayout()
        hbox.addWidget(QLabel(msg))
        pw = AmountEdit(None, is_int = True)
        pw.setFocus(True)
        hbox.addWidget(pw)
        hbox.addStretch(1)
        vbox.addLayout(hbox)

        b = OkButton(window, _('Next'))
        b.setEnabled(False)
        vbox.addLayout(Buttons(CancelButton(window), b))
        pw.textChanged.connect(lambda: b.setEnabled(len(pw.text())==6))

        while True:
            if not window.exec_():
                return False
            otp = pw.get_amount()
            try:
                server.auth(_id, otp)
                return True
            except:
                QMessageBox.information(self.window, _('Message'), _('Incorrect password'), _('OK'))
                pw.setText('')
