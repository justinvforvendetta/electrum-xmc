#!/usr/bin/python

# python setup.py sdist --format=zip,gztar

from setuptools import setup
import os
import sys
import platform
import imp


version = imp.load_source('version', 'lib/version.py')

if sys.version_info[:3] < (2, 7, 0):
    sys.exit("Error: Electrum-XMC requires Python version >= 2.7.0...")



data_files = []
if platform.system() in [ 'Linux', 'FreeBSD', 'DragonFly']:
    usr_share = os.path.join(sys.prefix, "share")
    data_files += [
        (os.path.join(usr_share, 'applications/'), ['electrum_xmc.desktop']),
        (os.path.join(usr_share, 'pixmaps/'), ['icons/electrum_xmc.png'])
    ]


setup(
    name="Electrum-XMC",
    version=version.ELECTRUM_VERSION,
    install_requires=[
        'slowaes>=0.1a1',
        'ecdsa>=0.9',
        'pbkdf2',
        'requests',
        'qrcode',
        'protobuf',
        'dnspython',
    ],
    package_dir={
        'electrum_xmc': 'lib',
        'electrum_xmc_gui': 'gui',
        'electrum_xmc_plugins': 'plugins',
    },
    packages=['electrum_xmc','electrum_xmc_gui','electrum_xmc_gui.qt','electrum_xmc_plugins'],
    package_data={
        'electrum_xmc': [
            'www/index.html',
            'wordlist/*.txt',
            'locale/*/LC_MESSAGES/electrum.mo',
        ],
        'electrum_xmc_gui': [
            "qt/themes/cleanlook/name.cfg",
            "qt/themes/cleanlook/style.css",
            "qt/themes/sahara/name.cfg",
            "qt/themes/sahara/style.css",
            "qt/themes/dark/name.cfg",
            "qt/themes/dark/style.css",
        ]
    },
    scripts=['electrum-xmc'],
    data_files=data_files,
    description="Lightweight XMC Wallet",
    author="sunerok",
    license="GNU GPLv3",
    url="https://electrum.org",
    long_description="""Lightweight XMC Wallet"""
)
