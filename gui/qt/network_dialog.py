#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2012 thomasv@gitorious
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

import sys, time, datetime, re, threading
from electrum_xmc.i18n import _
from electrum_xmc.util import print_error, print_msg
import os.path, json, ast, traceback

from PyQt4.QtGui import *
from PyQt4.QtCore import *
from electrum_xmc import DEFAULT_PORTS

from util import *

#protocol_names = ['TCP', 'HTTP', 'SSL', 'HTTPS']
#protocol_letters = 'thsg'
protocol_names = ['TCP', 'SSL']
protocol_letters = 'ts'

class NetworkDialog(QDialog):
    def __init__(self, network, config, parent):

        QDialog.__init__(self,parent)
        self.setModal(1)
        self.setWindowTitle(_('Network'))
        self.setMinimumSize(375, 20)

        self.network = network
        self.config = config
        self.protocol = None

        self.servers = network.get_servers()
        host, port, protocol, proxy_config, auto_connect = network.get_parameters()
        if not proxy_config:
            proxy_config = { "mode":"none", "host":"localhost", "port":"9050"}

        if parent:
            n = len(network.get_interfaces())
            if n:
                status = _("Blockchain") + ": " + "%d "%(network.get_local_height()) + _("blocks") +  ".\n" + _("Getting block headers from %d nodes.")%n
            else:
                status = _("Not connected")
            if network.is_connected():
                status += "\n" + _("Server") + ": %s"%(host)
            else:
                status += "\n" + _("Disconnected from server")
        else:
            status = _("Please choose a server.") + "\n" + _("Select 'Cancel' if you are offline.")

        vbox = QVBoxLayout()
        vbox.setSpacing(30)
        hbox = QHBoxLayout()
        l = QLabel()
        l.setPixmap(QPixmap(":icons/network.png"))
        hbox.addStretch(10)
        hbox.addWidget(l)
        hbox.addWidget(QLabel(status))
        hbox.addStretch(50)
        msg = _("Electrum-XMC sends your wallet addresses to a single server, in order to receive your transaction history.") + "\n\n" \
            + _("In addition, Electrum-XMC connects to several nodes in order to download block headers and find out the longest blockchain.") + " " \
            + _("This blockchain is used to verify the transactions sent by the address server.")
        hbox.addWidget(HelpButton(msg))
        vbox.addLayout(hbox)

        # grid layout
        grid = QGridLayout()
        grid.setSpacing(8)
        vbox.addLayout(grid)

        # server
        self.server_host = QLineEdit()
        self.server_host.setFixedWidth(200)
        self.server_port = QLineEdit()
        self.server_port.setFixedWidth(60)
        grid.addWidget(QLabel(_('Server') + ':'), 0, 0)

        # use SSL
        self.ssl_cb = QCheckBox(_('Use SSL'))
        self.ssl_cb.setChecked(auto_connect)
        grid.addWidget(self.ssl_cb, 3, 1)
        self.ssl_cb.stateChanged.connect(self.change_protocol)

        # auto connect
        self.autoconnect_cb = QCheckBox(_('Auto-connect'))
        self.autoconnect_cb.setChecked(auto_connect)
        grid.addWidget(self.autoconnect_cb, 0, 1)
        self.autoconnect_cb.setEnabled(self.config.is_modifiable('auto_connect'))
        msg = _("If auto-connect is enabled, Electrum-XMC will always use a server that is on the longest blockchain.") + " " \
            + _("If it is disabled, Electrum-XMC will warn you if your server is lagging.")
        grid.addWidget(HelpButton(msg), 0, 4)
        grid.addWidget(self.server_host, 0, 2, 1, 2)
        grid.addWidget(self.server_port, 0, 3)

        label = _('Active Servers') if network.is_connected() else _('Default Servers')
        self.servers_list_widget = QTreeWidget(parent)
        self.servers_list_widget.setHeaderLabels( [ label, _('Limit') ] )
        self.servers_list_widget.setMaximumHeight(150)
        self.servers_list_widget.setColumnWidth(0, 240)

        self.change_server(host, protocol)
        self.set_protocol(protocol)

        self.servers_list_widget.connect(self.servers_list_widget,
                                         SIGNAL('currentItemChanged(QTreeWidgetItem*,QTreeWidgetItem*)'),
                                         lambda x,y: self.server_changed(x))
        grid.addWidget(self.servers_list_widget, 1, 1, 1, 3)

        def enable_set_server():
            if config.is_modifiable('server'):
                enabled = not self.autoconnect_cb.isChecked()
                self.server_host.setEnabled(enabled)
                self.server_port.setEnabled(enabled)
                self.servers_list_widget.setEnabled(enabled)
            else:
                for w in [self.autoconnect_cb, self.server_host, self.server_port, self.ssl_cb, self.servers_list_widget]:
                    w.setEnabled(False)

        self.autoconnect_cb.clicked.connect(enable_set_server)
        enable_set_server()

        # proxy setting
        self.proxy_mode = QComboBox()
        self.proxy_host = QLineEdit()
        self.proxy_host.setFixedWidth(200)
        self.proxy_port = QLineEdit()
        self.proxy_port.setFixedWidth(60)
        self.proxy_mode.addItems(['NONE', 'SOCKS4', 'SOCKS5', 'HTTP'])

        def check_for_disable(index = False):
            if self.config.is_modifiable('proxy'):
                if self.proxy_mode.currentText() != 'NONE':
                    self.proxy_host.setEnabled(True)
                    self.proxy_port.setEnabled(True)
                else:
                    self.proxy_host.setEnabled(False)
                    self.proxy_port.setEnabled(False)
            else:
                for w in [self.proxy_host, self.proxy_port, self.proxy_mode]: w.setEnabled(False)

        check_for_disable()
        self.proxy_mode.connect(self.proxy_mode, SIGNAL('currentIndexChanged(int)'), check_for_disable)
        self.proxy_mode.setCurrentIndex(self.proxy_mode.findText(str(proxy_config.get("mode").upper())))
        self.proxy_host.setText(proxy_config.get("host"))
        self.proxy_port.setText(proxy_config.get("port"))

        grid.addWidget(QLabel(_('Proxy') + ':'), 4, 0)
        grid.addWidget(self.proxy_mode, 4, 1)
        grid.addWidget(self.proxy_host, 4, 2)
        grid.addWidget(self.proxy_port, 4, 3)

        # buttons
        vbox.addLayout(Buttons(CancelButton(self), OkButton(self)))
        self.setLayout(vbox)


    def init_servers_list(self):
        self.servers_list_widget.clear()
        for _host, d in sorted(self.servers.items()):
            if d.get(self.protocol):
                pruning_level = d.get('pruning','')
                self.servers_list_widget.addTopLevelItem(QTreeWidgetItem( [ _host, pruning_level ] ))


    def set_protocol(self, protocol):
        if protocol != self.protocol:
            self.protocol = protocol
            self.init_servers_list()

    def change_protocol(self, use_ssl):
        p = 's' if use_ssl else 't'
        host = unicode(self.server_host.text())
        pp = self.servers.get(host, DEFAULT_PORTS)
        if p not in pp.keys():
            p = pp.keys()[0]
        port = pp[p]
        self.server_host.setText( host )
        self.server_port.setText( port )
        self.set_protocol(p)

    def server_changed(self, x):
        if x:
            self.change_server(str(x.text(0)), self.protocol)

    def change_server(self, host, protocol):

        pp = self.servers.get(host, DEFAULT_PORTS)
        if protocol and protocol not in protocol_letters:
                protocol = None
        if protocol:
            port = pp.get(protocol)
            if port is None:
                protocol = None

        if not protocol:
            if 's' in pp.keys():
                protocol = 's'
                port = pp.get(protocol)
            else:
                protocol = pp.keys()[0]
                port = pp.get(protocol)

        self.server_host.setText( host )
        self.server_port.setText( port )
        self.ssl_cb.setChecked(protocol=='s')


    def do_exec(self):

        if not self.exec_():
            return

        host = str( self.server_host.text() )
        port = str( self.server_port.text() )
        protocol = 's' if self.ssl_cb.isChecked() else 't'

        if self.proxy_mode.currentText() != 'NONE':
            proxy = { 'mode':str(self.proxy_mode.currentText()).lower(),
                      'host':str(self.proxy_host.text()),
                      'port':str(self.proxy_port.text()) }
        else:
            proxy = None

        auto_connect = self.autoconnect_cb.isChecked()

        self.network.set_parameters(host, port, protocol, proxy, auto_connect)
        return True
